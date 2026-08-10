#!/bin/bash
# Install a previously verified local build. Run build_release.sh first.
set -euo pipefail

cd "$(dirname "$0")/.."
SOURCE="dist/LedgerTB.app"
TARGET="/Applications/LedgerTB.app"

[ -d "$SOURCE" ] || {
  echo "Missing $SOURCE. Run ./scripts/build_release.sh first."
  exit 1
}

LEDGERTB_MODE=selfcheck "$SOURCE/Contents/MacOS/LedgerTB"
codesign --verify --deep --strict "$SOURCE"

echo "Installing $TARGET"
# ditto MERGES into an existing bundle — it overwrites matching paths but never
# deletes target-only leftovers. Files the new build no longer ships (e.g. deps
# dropped by a spec EXCLUDES change) would survive, and since they aren't in the
# new signature, codesign fails with "a sealed resource is missing or invalid".
# Remove the old bundle first so every install is clean.
rm -rf "$TARGET"
ditto "$SOURCE" "$TARGET"
codesign --verify --deep --strict "$TARGET"
echo "Installed. Open LedgerTB from Applications, then choose Options > Keep in Dock."
