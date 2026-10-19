#!/bin/sh
# FAKE DATA demo checks: real CollectorModel launch path against the compiled fake helper only.
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
macos="$native_root/collector/macos"
demo="$macos/Tests/Fixtures/FakeDataDemo"
native_arch=${AMPLIFAI_TARGET_ARCH:-$(uname -m)}
case "$native_arch" in
    arm64|x86_64) ;;
    *) echo "AMPLIFAI_TARGET_ARCH must be arm64 or x86_64" >&2; exit 2 ;;
esac
native_temp=$(mktemp -d)
trap 'rm -rf -- "${native_temp:?}"' EXIT
trap 'exit 130' HUP INT TERM
mkdir "$native_temp/helper"
swiftc -O -parse-as-library -target "$native_arch-apple-macos14.0" \
    "$demo/FakeDataSet.swift" "$demo/FakeBridgeTimeline.swift" "$demo/FakeHelper.swift" -o "$native_temp/helper/amplifai-agent"
# Static isolation: the fake helper links no network, device or process-spawning API.
if nm -u "$native_temp/helper/amplifai-agent" | grep -Ei 'urlsession|nsurlconnection|_socket$|_connect$|getaddrinfo|ioservice|amdevice|usbmux|nsworkspace|posix_spawn|_execv|nstask|_fork$'; then
    echo "fake helper imports a forbidden API" >&2; exit 1
fi
if otool -L "$native_temp/helper/amplifai-agent" | grep -Ei '/(Network|IOKit|CFNetwork|MobileDevice)\.framework'; then
    echo "fake helper links a forbidden framework" >&2; exit 1
fi
echo "fake helper links no network, device or process-spawning API"
touch "$native_temp/helper/FAST_DEMO_TIMING"
swiftc -parse-as-library -target "$native_arch-apple-macos14.0" \
    "$macos/Sources/Services/CollectorModel.swift" \
    "$macos/Sources/Services/ContactSuggestions.swift" \
    "$macos/Sources/Services/ContactWorkspace.swift" \
    "$macos/Sources/Services/SupportDiagnosticReporter.swift" \
    "$macos/Sources/Models/AgentEvent.swift" \
    "$macos/Sources/Services/PhoneRunAuthority.swift" \
    "$macos/Sources/Support/PhoneRunLink.swift" \
    "$macos/Sources/Support/CompanionWindowDirector.swift" \
    "$macos/Tests/Fixtures/SimulatedPhoneRunServer.swift" \
    "$demo/FakeDataSet.swift" "$demo/FakeBridgeTimeline.swift" "$demo/FakeDemoChecks.swift" \
    -o "$native_temp/FakeDemoChecks"
"$native_temp/FakeDemoChecks"
