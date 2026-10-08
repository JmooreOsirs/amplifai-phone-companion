#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ] || [ ! -d "$1" ] || [ -e "$2" ] || [ ! -d "$(dirname "$2")" ]; then
  echo "usage: $0 existing-notarized.app new-output.dmg (output must not exist)" >&2
  exit 2
fi

APP="$1"
OUTPUT="$2"
for legal_file in LICENSE COPYRIGHT SOURCE-README.md THIRD-PARTY-NOTICES.txt source-package-file-manifest.json; do
  if [ ! -s "$APP/Contents/Resources/Legal/$legal_file" ]; then
    echo "missing in-app legal offer: $legal_file" >&2
    exit 1
  fi
done
codesign --verify --deep --strict "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute "$APP"

STAGING="$(mktemp -d -t amplifai-dmg.XXXXXX)"
trap 'rm -rf -- "$STAGING"' EXIT
ditto "$APP" "$STAGING/AMPLIFai Phone.app"
ln -s /Applications "$STAGING/Applications"
if diskutil image create from --help >/dev/null 2>&1; then
  diskutil image create from --volumeName "AMPLIFai Phone" --format UDZO "$STAGING" "$OUTPUT"
else
  hdiutil create -volname "AMPLIFai Phone" -srcfolder "$STAGING" -format UDZO "$OUTPUT"
fi
hdiutil verify "$OUTPUT"
echo "DMG created; its own Developer ID signature, notarization, staple and assessment are separate release gates."
