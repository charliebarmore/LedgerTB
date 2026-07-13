#!/bin/bash
# Build and verify a standalone ProBooks.app. This script never installs or
# overwrites the copy in /Applications; run install_local.sh explicitly for that.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Running tests"
python -m pytest -q

echo "==> Compiling application modules"
python -m compileall -q app.py pages models services database utils \
  config.py constants.py money.py version.py desktop.py run_probooks.py

echo "==> Building standalone app"
pyinstaller ProBooks.spec --noconfirm

APP="dist/ProBooks.app"
BIN="$APP/Contents/MacOS/ProBooks"

echo "==> Running frozen runtime self-check"
PROBOOKS_MODE=selfcheck "$BIN"

echo "==> Verifying bundle signature"
codesign --verify --deep --strict "$APP"

echo "==> Build complete: $APP"
du -sh "$APP"
