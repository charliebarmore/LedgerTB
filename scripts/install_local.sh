#!/bin/bash
# Install a previously verified local build. Run build_release.sh first.
set -euo pipefail

cd "$(dirname "$0")/.."
SOURCE="dist/ProBooks.app"
TARGET="/Applications/ProBooks.app"

[ -d "$SOURCE" ] || {
  echo "Missing $SOURCE. Run ./scripts/build_release.sh first."
  exit 1
}

PROBOOKS_MODE=selfcheck "$SOURCE/Contents/MacOS/ProBooks"
codesign --verify --deep --strict "$SOURCE"

echo "Installing $TARGET"
ditto "$SOURCE" "$TARGET"
codesign --verify --deep --strict "$TARGET"
echo "Installed. Open ProBooks from Applications, then choose Options > Keep in Dock."
