#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p runtime/tmp runtime/pip-cache logs projects
export PIP_CACHE_DIR="$PWD/runtime/pip-cache"
"${PYTHON_BIN:-python3}" -m venv .venv-control
.venv-control/bin/python -m pip install -r requirements-control.txt
.venv-control/bin/python -m engine.cli init
