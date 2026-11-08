#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
test -x .venv-tts/bin/python
export PIP_CACHE_DIR="$PWD/runtime/pip-cache"
"${PYTHON_BIN:-python3}" -m venv .venv-asr
site_dir=$(.venv-asr/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
tts_site=$(.venv-tts/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
runtime_site=$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
printf '%s\n' "$tts_site" "$runtime_site" > "$site_dir/studio-runtime.pth"
.venv-asr/bin/python -m pip install qwen-asr==0.0.6 transformers==4.57.6
.venv-control/bin/python scripts/download_models.py --group asr
