#!/bin/sh
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
native_arch=${AMPLIFAI_TARGET_ARCH:-$(uname -m)}
case "$native_arch" in
    arm64|x86_64) ;;
    *) echo "AMPLIFAI_TARGET_ARCH must be arm64 or x86_64" >&2; exit 2 ;;
esac
native_temp=$(mktemp -d)
trap 'if [ -f "$native_temp/PhonePreparationFlowChecks" ]; then unlink "$native_temp/PhonePreparationFlowChecks"; fi; rmdir "$native_temp"' EXIT
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
    "$native_root/collector/macos/Tests/PhoneRunFlowChecks.swift" \
    "$native_root/collector/macos/Tests/PhonePreparationFlowChecks.swift" \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$native_root/collector/macos/Info.plist" \
    -o "$native_temp/PhonePreparationFlowChecks"
"$native_temp/PhonePreparationFlowChecks"
