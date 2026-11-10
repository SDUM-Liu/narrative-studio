"""Isolated native SGLang inference; never executes in the control Python environment."""
import argparse
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    job = json.loads(Path(args.request).read_text())
    import torch
    from sglang.multimodal_gen import DiffGenerator
    count = torch.cuda.device_count()
    if not torch.cuda.is_available() or count < job['num_gpus']:
        raise RuntimeError(f'H3 requires {job["num_gpus"]} allocated CUDA GPUs, got {count}')
    if count != job['num_gpus']:raise RuntimeError('H3 must own precisely its configured allocation')
    for i in range(count):
        properties = torch.cuda.get_device_properties(i)
        if properties.total_memory < 79 * 1024**3 or not torch.cuda.is_bf16_supported():
            raise RuntimeError('Final H3 recipe requires native BF16 GPUs with at least 80GB; no quantized fallback')
    started = time.monotonic()
    # TP shards weights without quantization. Ulysses is separate sequence parallelism.
    generator = DiffGenerator.from_pretrained(
        model_path=job['model_path'], model_variant=job['variant'], local_mode=True,
        num_gpus=count, tp_size=job['tp_size'], ulysses_degree=job['ulysses_degree'],
        attention_backend='fa', enable_torch_compile=False,
        disable_autocast=True,
        allow_cudnn_tf32=False, allow_bf16_reduced_precision_reduction=False,
        dit_cpu_offload=False, text_encoder_cpu_offload=False, vae_cpu_offload=False,
    )
    loaded = time.monotonic()
    try:
        result = generator.generate(sampling_params_kwargs=job['sampling'])
        if result is None:raise RuntimeError('SGLang generation failed; inspect preceding device logs')
        # Device ranks report their own peaks in the upstream logs; parent CUDA
        # allocator statistics do not represent their allocations.
        report = {'model_load_seconds':round(loaded-started, 3),
                  'generation_seconds':round(time.monotonic()-loaded, 3),
                  'precision':'native BF16/FP32', 'attention':'fa', 'cloud_calls':False,
                  'slurm_job_id':os.environ.get('SLURM_JOB_ID')}
        (Path(args.request).parent/'h3-performance.json').write_text(json.dumps(report, indent=2))
    finally:generator.shutdown()


if __name__ == '__main__':main()
