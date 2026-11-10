#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if command -v sox >/dev/null 2>&1; then exit 0; fi
if ! test -x vendor/sox/usr/bin/sox; then
  mkdir -p runtime/sox-debs vendor/sox
  (cd runtime/sox-debs && apt-get download sox libsox3 libsox-fmt-base)
  for package in runtime/sox-debs/*.deb; do dpkg-deb -x "$package" vendor/sox; done
fi
LD_LIBRARY_PATH="$PWD/vendor/sox/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" vendor/sox/usr/bin/sox --version
