#!/usr/bin/env bash
# Read-only local checks. A green diagnostic is never distribution approval.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$ROOT_DIR/dist/AmplifaiPhonePortable.app}"
blocked=0

if [ ! -d "$APP" ]; then
  echo "BLOCKED app: no bundle at $APP"
  exit 1
fi

signature="$(codesign -dv --verbose=4 "$APP" 2>&1)"
if [ $? -ne 0 ]; then
  echo "BLOCKED signature: bundle has no readable code signature"
  blocked=1
elif ! codesign --verify --deep --strict "$APP" >/dev/null 2>&1; then
  echo "BLOCKED signature: strict verification failed"
  blocked=1
elif [[ "$signature" == *"Signature=adhoc"* ]] || [[ "$signature" == *"TeamIdentifier=not set"* ]]; then
  echo "BLOCKED signature: ad-hoc or no signing team"
  blocked=1
elif [[ "$signature" != *"Authority=Developer ID Application:"* ]]; then
  echo "BLOCKED signature: not a Developer ID Application signature"
  blocked=1
elif [[ "$signature" != *"(runtime)"* ]] || [[ "$signature" != *"Timestamp="* ]] || [[ "$signature" == *"Timestamp=none"* ]]; then
  echo "BLOCKED signature: hardened runtime or secure timestamp is missing"
  blocked=1
else
  echo "PASS signature: Developer ID Application signature verifies locally"
fi

HELPER="$APP/Contents/Resources/helper/amplifai-agent"
if [ ! -f "$HELPER" ]; then
  echo "BLOCKED helper: frozen collector executable is missing"
  blocked=1
else
  helper_signature="$(codesign -dv --verbose=4 "$HELPER" 2>&1)"
  if [ $? -ne 0 ] || ! codesign --verify --strict "$HELPER" >/dev/null 2>&1; then
    echo "BLOCKED helper: executable signature does not verify"
    blocked=1
  elif [[ "$helper_signature" == *"Signature=adhoc"* ]] || [[ "$helper_signature" == *"TeamIdentifier=not set"* ]] || [[ "$helper_signature" != *"Authority=Developer ID Application:"* ]]; then
    echo "BLOCKED helper: frozen collector lacks Developer ID Application signature"
    blocked=1
  elif [[ "$helper_signature" != *"(runtime)"* ]] || [[ "$helper_signature" != *"Timestamp="* ]] || [[ "$helper_signature" == *"Timestamp=none"* ]]; then
    echo "BLOCKED helper: hardened runtime or secure timestamp is missing"
    blocked=1
  else
    echo "PASS helper: Developer ID Application signature verifies locally"
  fi
fi

nested_count=0
nested_blocked=0
while IFS= read -r -d '' nested_code; do
  nested_count=$((nested_count + 1))
  nested_signature="$(codesign -dv --verbose=2 "$nested_code" 2>&1)"
  if [ $? -ne 0 ] || ! codesign --verify --strict "$nested_code" >/dev/null 2>&1 ||
     [[ "$nested_signature" == *"Signature=adhoc"* ]] ||
     [[ "$nested_signature" == *"TeamIdentifier=not set"* ]] ||
     [[ "$nested_signature" != *"Authority=Developer ID Application:"* ]]; then
    nested_blocked=$((nested_blocked + 1))
  fi
done < <(find "$APP/Contents/Resources/helper" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)
if [ "$nested_count" -eq 0 ]; then
  echo "BLOCKED nested libraries: none found in the frozen collector"
  blocked=1
elif [ "$nested_blocked" -gt 0 ]; then
  echo "BLOCKED nested libraries: $nested_blocked of $nested_count lack a verifiable Developer ID Application signature"
  blocked=1
else
  echo "PASS nested libraries: $nested_count verified with Developer ID Application signatures"
fi

identities="$(security find-identity -p codesigning -v 2>&1)"
if [[ "$identities" =~ ([0-9]+)[[:space:]]valid[[:space:]]identities[[:space:]]found ]]; then
  echo "INFO local keychain: ${BASH_REMATCH[1]} valid code-signing identities (membership and notarization access not proven)"
else
  echo "UNKNOWN local keychain: could not determine valid identity count"
fi

if spctl -a -vv "$APP" >/dev/null 2>&1; then
  echo "PASS Gatekeeper: accepted on this Mac"
else
  echo "BLOCKED Gatekeeper: rejected or not assessable on this Mac"
  blocked=1
fi

if xcrun --find stapler >/dev/null 2>&1; then
  stapler_result="$(xcrun stapler validate "$APP" 2>&1)"
  if [ $? -eq 0 ]; then
    echo "PASS notarization ticket: stapled ticket validates locally"
  elif [[ "$stapler_result" == *"does not have a ticket stapled"* ]]; then
    echo "BLOCKED notarization ticket: no ticket stapled"
    blocked=1
  else
    echo "BLOCKED notarization ticket: validation failed"
    blocked=1
  fi
else
  echo "UNKNOWN notarization ticket: stapler unavailable in the active developer tools"
  blocked=1
fi

echo "MANUAL release gates: companion rights/notices/source offer, fresh normal install, browser handoff, and owner-operated real-device consent/test are not proven by this script."
exit "$blocked"
