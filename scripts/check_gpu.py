"""Validate the CUDA allocation with small matrix operations, without models."""
import argparse
import json
import os
from pathlib import Path
import socket

import torch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--min-vram-mib', type=int, default=20000)
args = parser.parse_args()
if not os.environ.get('SLURM_JOB_ID'):
    raise RuntimeError('GPU checks must run inside a Slurm allocation')
if not torch.cuda.is_available() or not torch.cuda.device_count():
    raise RuntimeError('No CUDA device in the Slurm allocation')
devices = []
for index in range(torch.cuda.device_count()):
    with torch.cuda.device(index):
        prop = torch.cuda.get_device_properties(index)
        if prop.total_memory / 1024**2 < args.min_vram_mib:
            raise RuntimeError(f'{prop.name}: insufficient VRAM for this service')
        bf16 = torch.cuda.is_bf16_supported(including_emulation=False)
        dtype = torch.bfloat16 if bf16 else torch.float16
        a = torch.ones((256, 256), device=f'cuda:{index}', dtype=dtype)
        result = a @ a
        torch.cuda.synchronize()
        assert result[0, 0].item() == 256
        uuid = str(prop.uuid)
        if not uuid.startswith(('GPU-', 'MIG-')): uuid = 'GPU-' + uuid
        devices.append({'ordinal': index, 'uuid': uuid, 'name': prop.name,
                        'vram_mib': round(prop.total_memory/1024**2),
                        'native_bf16': bf16, 'matmul_dtype': str(dtype), 'matmul': 'passed'})
report = {'job_id': os.environ['SLURM_JOB_ID'], 'host': socket.gethostname(),
          'torch': torch.__version__, 'cuda': torch.version.cuda, 'devices': devices}
root = Path(__file__).resolve().parents[1]
(root/'runtime'/f'cuda-{report["job_id"]}.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2), flush=True)
