#!/bin/sh
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
native_arch=${AMPLIFAI_TARGET_ARCH:-$(uname -m)}
case "$native_arch" in
    arm64|x86_64) ;;
    *) echo "AMPLIFAI_TARGET_ARCH must be arm64 or x86_64" >&2; exit 2 ;;
esac
# The checks launch only a synthetic helper they write next to this temporary executable.
native_temp=$(mktemp -d)
trap 'rm -f -- "$native_temp/CollectorModelChecks"; rm -rf -- "$native_temp/helper"; rmdir "$native_temp"' EXIT
trap 'exit 130' HUP INT TERM
swiftc -parse-as-library -target "$native_arch-apple-macos14.0" \
    "$native_root/collector/macos/Sources/Services/CollectorModel.swift" \
    "$native_root/collector/macos/Sources/Services/ContactSuggestions.swift" \
    "$native_root/collector/macos/Sources/Services/ContactWorkspace.swift" \
    "$native_root/collector/macos/Sources/Services/SupportDiagnosticReporter.swift" \
    "$native_root/collector/macos/Sources/Models/AgentEvent.swift" \
    "$native_root/collector/macos/Tests/CollectorModelChecks.swift" \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$native_root/collector/macos/Info.plist" \
    -o "$native_temp/CollectorModelChecks"
"$native_temp/CollectorModelChecks"
