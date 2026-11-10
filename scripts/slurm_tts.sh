#!/usr/bin/env bash
# Separate queue lanes let currently available 11GB GPUs serve TTS while
# image/video services wait for A800/4090 resources.
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
test -f configs/project.template.yaml
mkdir -p runtime/tmp logs
export TMPDIR="$PWD/runtime/tmp" HF_HOME="$PWD/models/hf-cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 NARRATIVE_WORKER_KINDS=tts
exec 9>runtime/slurm-tts.lock
flock -n 9 || { echo 'A TTS GPU allocation is already active'; exit 1; }
test "$(cat runtime/tts.status)" = complete
test -s models/tts/model.safetensors
test -s models/tts/speech_tokenizer/model.safetensors
.venv/bin/python -u scripts/check_gpu.py --min-vram-mib 6500
exec .venv-control/bin/python -u scripts/services.py run --lane-offset 2 --only worker-2 worker-3
