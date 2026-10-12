#!/bin/sh
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
native_arch=${AMPLIFAI_TARGET_ARCH:-$(uname -m)}
case "$native_arch" in
    arm64|x86_64) ;;
    *) echo "AMPLIFAI_TARGET_ARCH must be arm64 or x86_64" >&2; exit 2 ;;
esac
native_temp=$(mktemp -d)
trap 'if [ -f "$native_temp/CollectorFlowChecks" ]; then unlink "$native_temp/CollectorFlowChecks"; fi; rmdir "$native_temp"' EXIT
trap 'exit 130' HUP INT TERM
swiftc -parse-as-library -target "$native_arch-apple-macos14.0" \
    "$native_root/collector/macos/Sources/Services/CollectorModel.swift" \
    "$native_root/collector/macos/Sources/Services/ContactSuggestions.swift" \
    "$native_root/collector/macos/Sources/Services/ContactWorkspace.swift" \
    "$native_root/collector/macos/Sources/Services/SupportDiagnosticReporter.swift" \
    "$native_root/collector/macos/Sources/Models/AgentEvent.swift" \
    "$native_root/collector/macos/Tests/CollectorFlowChecks.swift" \
    -o "$native_temp/CollectorFlowChecks"
"$native_temp/CollectorFlowChecks"
