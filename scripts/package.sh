#!/usr/bin/env bash
# Package the plugin as a ZIP ready for QGIS "Install from ZIP"
# (Plugins -> Manage and Install Plugins -> Install from ZIP).
#
# Usage: ./scripts/package.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

VERSION="$(grep '^version=' metadata.txt | cut -d= -f2 | tr -d '[:space:]')"
OUT_DIR="$HERE/build"
OUT="$OUT_DIR/epidbot-${VERSION}.zip"
STAGE="$OUT_DIR/epidbot"

rm -rf "$OUT_DIR"
mkdir -p "$STAGE"

# Copy everything except development-only content.
cp -r metadata.txt __init__.py plugin.py core ui i18n icons README.md LICENSE \
   "$STAGE/" 2>/dev/null || true

# Strip caches and bytecode.
find "$STAGE" -type d \( -name '__pycache__' -o -name '.pytest_cache' \) -exec rm -rf {} +
find "$STAGE" -type f -name '*.pyc' -delete

mkdir -p "$OUT_DIR"
if command -v zip >/dev/null 2>&1; then
    (cd "$OUT_DIR" && zip -qr "epidbot-${VERSION}.zip" epidbot)
else
    (cd "$OUT_DIR" && python3 -m zipfile -c "epidbot-${VERSION}.zip" epidbot)
fi
rm -rf "$STAGE"

echo "Packaged: $OUT"
echo "Install via QGIS: Plugins -> Manage and Install Plugins -> Install from ZIP"
