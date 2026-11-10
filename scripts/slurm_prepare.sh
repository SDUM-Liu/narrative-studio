#!/usr/bin/env bash
# Legacy FP8 conversion is retired. Native H3/Qwen BF16 files need no conversion.
set -euo pipefail
echo 'No conversion is required. Download native weights with scripts/download_models.py --group h3.'
