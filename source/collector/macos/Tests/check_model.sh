#!/bin/sh
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
native_arch=${AMPLIFAI_TARGET_ARCH:-$(uname -m)}
case "$native_arch" in
    arm64|x86_64) ;;
    *) echo "AMPLIFAI_TARGET_ARCH must be arm64 or x86_64" >&2; exit 2 ;;
esac
# Default qualification includes the real 30-second native timer and production Python bridge.
# Capture/account boundaries are generated fixtures. --quick-only is a developer-only shortcut.
case "${1:-}" in
    "") native_python=${AMPLIFAI_TEST_PYTHON:-$(command -v python3)}
        "$native_python" -c 'import pymobiledevice3' ;;
    --quick-only) native_python= ;;
    *) echo "Usage: check_model.sh [--quick-only]" >&2; exit 2 ;;
esac
native_temp=$(mktemp -d)
trap 'rm -rf -- "$native_temp"' EXIT
trap 'exit 130' HUP INT TERM
swiftc -parse-as-library -target "$native_arch-apple-macos14.0" \
    "$native_root/collector/macos/Sources/Services/CollectorModel.swift" \
    "$native_root/collector/macos/Sources/Services/ContactSuggestions.swift" \
    "$native_root/collector/macos/Sources/Services/ContactWorkspace.swift" \
    "$native_root/collector/macos/Sources/Services/SupportDiagnosticReporter.swift" \
    "$native_root/collector/macos/Sources/Models/AgentEvent.swift" \
    "$native_root/collector/macos/Sources/Services/PhoneRunAuthority.swift" \
    "$native_root/collector/macos/Sources/Services/PhoneSetupAuthority.swift" \
    "$native_root/collector/macos/Sources/Support/PhoneRunLink.swift" \
    "$native_root/collector/macos/Sources/Support/CompanionWindowDirector.swift" \
    "$native_root/collector/macos/Tests/Fixtures/SimulatedPhoneRunServer.swift" \
    "$native_root/collector/macos/Tests/AuthorityRefreshIntegrationChecks.swift" \
    "$native_root/collector/macos/Tests/CollectorModelChecks.swift" \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$native_root/collector/macos/Info.plist" \
    -o "$native_temp/CollectorModelChecks"
if [ -n "$native_python" ]; then
    "$native_temp/CollectorModelChecks" "$native_python" "$native_root/collector"
else
    "$native_temp/CollectorModelChecks" --quick-only
fi
