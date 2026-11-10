#!/usr/bin/env bash
# Install/download only. GPU scheduling remains explicit and reviewable.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p runtime logs
exec 8>runtime/deploy.lock
flock -n 8 || { echo 'Deployment is already running'; exit 1; }
bash scripts/bootstrap.sh
.venv-control/bin/python scripts/download_models.py --group h3 --jobs 4
printf 'complete\n' > runtime/deploy.status
echo 'Local H3 ready. Inspect scripts/slurm_submit.py, then add --submit to allocate A800s.'
