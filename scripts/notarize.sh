#!/bin/bash
# Notarize a signed dist/ProBooks.app so it opens with no Gatekeeper warning on
# any Mac. Requires an Apple Developer Program membership ($99/yr).
#
# ONE-TIME SETUP
#   1. Get a "Developer ID Application" certificate into your login keychain
#      (Xcode ▸ Settings ▸ Accounts ▸ Manage Certificates, or developer.apple.com).
#      Find its exact name with:  security find-identity -v -p codesigning
#   2. Store notary credentials once (uses an app-specific password from
#      appleid.apple.com):
#        xcrun notarytool store-credentials probooks-notary \
#          --apple-id "you@example.com" --team-id "TEAMID" \
#          --password "xxxx-xxxx-xxxx-xxxx"
#
# BUILD (signed + hardened runtime + entitlements, done by PyInstaller):
#   PROBOOKS_CODESIGN_ID="Developer ID Application: Your Name (TEAMID)" \
#     pyinstaller ProBooks.spec --noconfirm
#
# THEN:
#   ./scripts/notarize.sh
#
set -euo pipefail

APP="dist/ProBooks.app"
ZIP="dist/ProBooks.zip"
PROFILE="${NOTARY_PROFILE:-probooks-notary}"

[ -d "$APP" ] || { echo "Build $APP first (see the header of this script)."; exit 1; }

echo "==> Verifying signature + hardened runtime..."
codesign --verify --deep --strict --verbose=2 "$APP"
if ! codesign -dvv "$APP" 2>&1 | grep -q "flags=.*runtime"; then
    echo "ERROR: $APP is not signed with the Hardened Runtime."
    echo "Rebuild with PROBOOKS_CODESIGN_ID set to your Developer ID Application identity"
    echo "(a plain/ad-hoc build cannot be notarized)."
    exit 1
fi

echo "==> Zipping for submission..."
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Submitting to Apple notary service (a few minutes)..."
xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait

echo "==> Stapling the ticket to the app..."
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
echo "==> Gatekeeper assessment:"
spctl --assess --type execute --verbose=4 "$APP" || true

echo "==> Done: dist/ProBooks.app is signed, notarized, and stapled."
