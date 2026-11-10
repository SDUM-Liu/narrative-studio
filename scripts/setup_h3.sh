#!/usr/bin/env bash
# Dedicated Python 3.12 runtime; source revision is pinned and separate from Comfy/TTS.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p runtime/tmp logs vendor
export TMPDIR="$PWD/runtime/tmp"
export UV_CACHE_DIR="$PWD/runtime/uv-cache"
# The optional Rust extensions serve LLM/image preprocessing; H3 uses its native Python/CUDA implementation.
export SGLANG_BUILD_RUST_EXTS=none
printf "installing\n" > runtime/h3-runtime.status
trap 'printf "failed; see logs/setup-h3.log\n" > runtime/h3-runtime.status' ERR
command -v uv >/dev/null || { echo 'Install uv first: https://docs.astral.sh/uv/'; exit 1; }
if ! test -x .venv-h3/bin/python; then uv venv --python 3.12 .venv-h3; fi
h3_revision=$(cat configs/sglang-commit.txt)
uv pip install --prerelease=allow --python .venv-h3/bin/python "sglang[diffusion] @ https://github.com/sgl-project/sglang/archive/$h3_revision.tar.gz#subdirectory=python"
.venv-h3/bin/python -c 'from sglang.multimodal_gen import DiffGenerator; from sglang.multimodal_gen.configs.sample.minimax_h3 import MiniMaxH3SamplingParams; import torch; print(torch.__version__)'
uv pip check --python .venv-h3/bin/python
uv pip freeze --python .venv-h3/bin/python > runtime/installed-h3-requirements.txt
printf 'complete\n' > runtime/h3-runtime.status
