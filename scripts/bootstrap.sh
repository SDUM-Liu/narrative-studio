#!/usr/bin/env bash
# Main video backend is local H3. Qwen/Comfy asset tooling is explicitly optional.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
case "${1:-h3}" in
  h3) exec bash scripts/setup_h3.sh ;;
  assets) exec bash scripts/setup_reference_assets.sh ;;
  *) echo 'Usage: bash scripts/bootstrap.sh [h3|assets]'; exit 2 ;;
esac
