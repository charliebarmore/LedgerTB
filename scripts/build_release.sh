#!/bin/bash
# Build and verify a standalone ProBooks.app. This script never installs or
# overwrites the copy in /Applications; run install_local.sh explicitly for that.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Verifying installed dependencies"
python -m pip check
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  python scripts/verify_lock.py requirements-macos-arm64.lock
fi

# Local signing config (gitignored): sets PROBOOKS_CODESIGN_ID so the bundle
# signs with a stable Developer ID and Keychain items survive reinstalls.
if [ -f scripts/signing.env ]; then
  # shellcheck disable=SC1091
  source scripts/signing.env
  echo "==> Signing as: ${PROBOOKS_CODESIGN_ID:-'(ad-hoc)'}"
fi

echo "==> Running tests"
python -m pytest -q

echo "==> Compiling application modules"
python -m compileall -q app.py pages models services database utils \
  config.py constants.py money.py version.py desktop.py run_probooks.py

echo "==> Building standalone app"
# The bundle is built ad-hoc and re-signed once below. Passing the identity
# into PyInstaller signs every collected binary individually — hundreds of
# codesign+timestamp calls — which failed intermittently (errSecInternalComponent).
PROBOOKS_CODESIGN_ID= pyinstaller ProBooks.spec --noconfirm

APP="dist/ProBooks.app"
BIN="$APP/Contents/MacOS/ProBooks"

echo "==> Running frozen runtime self-check"
PROBOOKS_MODE=selfcheck "$BIN"

if [ -n "${PROBOOKS_CODESIGN_ID:-}" ]; then
  echo "==> Signing bundle with Developer ID"
  # No --timestamp for local builds: it needs one Apple round-trip per nested
  # binary and only matters for notarization (notarize.sh re-signs with it).
  codesign --force --deep --options runtime \
    --entitlements scripts/entitlements.plist \
    -s "$PROBOOKS_CODESIGN_ID" "$APP"
fi

echo "==> Verifying bundle signature"
codesign --verify --deep --strict "$APP"
if [ -n "${PROBOOKS_CODESIGN_ID:-}" ]; then
  # An identity was requested; an ad-hoc result means signing silently failed.
  if codesign -dv "$APP" 2>&1 | grep -q "Signature=adhoc"; then
    echo "ERROR: expected a Developer ID signature but got ad-hoc" >&2
    exit 1
  fi
fi

echo "==> Build complete: $APP"
du -sh "$APP"
