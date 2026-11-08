#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
.venv-control/bin/python scripts/services.py status
curl --noproxy '*' --fail --silent http://127.0.0.1:7868/api/health
