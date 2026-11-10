#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p vendor runtime/tmp runtime/pip-cache models logs
export PIP_CACHE_DIR="$PWD/runtime/pip-cache"
export TMPDIR="$PWD/runtime/tmp"
export HF_HOME="$PWD/models/hf-cache"
pinned_commit=$(cat configs/comfy-commit.txt)
if [ ! -d vendor/ComfyUI/.git ]; then
  git clone --no-checkout https://github.com/Comfy-Org/ComfyUI.git vendor/ComfyUI
fi
git -C vendor/ComfyUI fetch --depth 1 origin "$pinned_commit"
git -C vendor/ComfyUI checkout --detach "$pinned_commit"
if [ ! -x .venv/bin/python ]; then
  "${PYTHON_BIN:-python3}" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -m pip install -r configs/runtime.lock.txt
printf '%s\n' complete > runtime/bootstrap.status
