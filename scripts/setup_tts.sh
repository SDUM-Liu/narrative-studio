#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p runtime
exec 9>runtime/tts-setup.lock
flock 9
test -x .venv/bin/python
export PIP_CACHE_DIR="$PWD/runtime/pip-cache"
"${PYTHON_BIN:-python3}" -m venv .venv-tts
site_dir=$(.venv-tts/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
runtime_site=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
printf '%s\n' "$runtime_site" > "$site_dir/studio-runtime.pth"
.venv-tts/bin/python -m pip install qwen-tts==0.1.1 transformers==4.57.3 gradio==5.49.1 huggingface_hub==0.36.2
bash scripts/setup_sox.sh
printf '%s\n' complete > runtime/tts.status
