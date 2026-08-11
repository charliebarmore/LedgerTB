#!/bin/bash
# Notarize a signed dist/LedgerTB.app so it opens with no Gatekeeper warning on
# any Mac. Requires an Apple Developer Program membership ($99/yr).
#
# ONE-TIME SETUP
#   1. Get a "Developer ID Application" certificate into your login keychain
#      (Xcode ▸ Settings ▸ Accounts ▸ Manage Certificates, or developer.apple.com).
#      Find its exact name with:  security find-identity -v -p codesigning
#   2. Store notary credentials once (uses an app-specific password from
#      appleid.apple.com):
#        xcrun notarytool store-credentials ledgertb-notary \
#          --apple-id "you@example.com" --team-id "TEAMID" \
#          --password "xxxx-xxxx-xxxx-xxxx"
#
# BUILD (signed + hardened runtime + entitlements, done by PyInstaller):
#   LEDGERTB_CODESIGN_ID="Developer ID Application: Your Name (TEAMID)" \
#     pyinstaller LedgerTB.spec --noconfirm
#
# THEN:
#   ./scripts/notarize.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/LedgerTB.app"
ZIP="dist/LedgerTB.zip"
PROFILE="${NOTARY_PROFILE:-${PROBOOKS_NOTARY_PROFILE:-ledgertb-notary}}"

[ -d "$APP" ] || { echo "Build $APP first (see the header of this script)."; exit 1; }

if [ -f scripts/signing.env ]; then
    # shellcheck disable=SC1091
    source scripts/signing.env
fi
LEDGERTB_CODESIGN_ID="${LEDGERTB_CODESIGN_ID:-${PROBOOKS_CODESIGN_ID:-}}"
[ -n "$LEDGERTB_CODESIGN_ID" ] || {
    echo "ERROR: set LEDGERTB_CODESIGN_ID to a Developer ID Application identity."
    exit 1
}

echo "==> Re-signing bundle with a secure timestamp..."
codesign --force --deep --timestamp --options runtime \
    --entitlements scripts/entitlements.plist \
    -s "$LEDGERTB_CODESIGN_ID" "$APP"

echo "==> Verifying signature + hardened runtime..."
codesign --verify --deep --strict --verbose=2 "$APP"
# Capture first: piping codesign straight into `grep -q` races under
# pipefail — grep exits on the first match, codesign takes SIGPIPE, and the
# pipeline "fails" with the flag present.
SIGN_INFO="$(codesign -dvv "$APP" 2>&1)"
if ! printf '%s' "$SIGN_INFO" | grep -q "flags=.*runtime"; then
    echo "ERROR: $APP is not signed with the Hardened Runtime."
    echo "Rebuild with LEDGERTB_CODESIGN_ID set to your Developer ID Application identity"
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

echo "==> Rebuilding the public archive with the stapled app..."
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Done: dist/LedgerTB.app is signed, notarized, and stapled."
echo "==> Public archive: $ZIP"
