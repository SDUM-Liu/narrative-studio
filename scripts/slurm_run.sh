#!/usr/bin/env bash
set -euo pipefail
: "${SLURM_JOB_ID:?Submit with scripts/slurm_submit.py}"
# Slurm executes a spooled script; --chdir supplies the repository directory.
test -f configs/project.template.yaml
test "${SLURM_JOB_NUM_NODES:-1}" = 1
mkdir -p runtime logs runtime/tmp
export TMPDIR="$PWD/runtime/tmp"
export HF_HOME="$PWD/models/hf-cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export NARRATIVE_COMFY_VRAM="${1:-normal}"
# GPU services are supervised in the foreground and stay in the job cgroup.
# A shared lock prevents two allocations from claiming the same queue lanes.
exec 9>runtime/slurm-compute.lock
flock -n 9 || { echo 'Another Narrative Studio GPU allocation is active'; exit 1; }
for marker in h3-runtime h3-models; do
  test "$(cat "runtime/$marker.status" 2>/dev/null)" = complete || {
    echo "H3 deployment incomplete: $marker. Run bootstrap and download_models --group h3"; exit 1;
  }
done
printf 'Job=%s Node=%s CUDA_VISIBLE_DEVICES=%s\n' "$SLURM_JOB_ID" "$(hostname)" "${CUDA_VISIBLE_DEVICES:-unset}"
nvidia-smi --query-gpu=index,uuid,name,memory.total,driver_version --format=csv
.venv-h3/bin/python -u scripts/check_gpu.py --min-vram-mib 80000
if test -n "${2:-}"; then
  exec .venv-control/bin/python -u -m scripts.run_episode "$2" "${3:-01}"
fi
exec .venv-control/bin/python -u scripts/services.py run --only h3
