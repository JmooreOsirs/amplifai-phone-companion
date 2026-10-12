#!/bin/sh
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
native_temp=$(mktemp -d)
trap 'if [ -f "$native_temp/CompanionUpdaterChecks" ]; then unlink "$native_temp/CompanionUpdaterChecks"; fi; rmdir "$native_temp"' EXIT
build_bin=$(swift build --package-path "$native_root/collector/macos" --show-bin-path)
swiftc -parse-as-library -target arm64-apple-macos14.0 -F "$build_bin" \
    "$native_root/collector/macos/Sources/Services/CollectorModel.swift" \
    "$native_root/collector/macos/Sources/Services/ContactSuggestions.swift" \
    "$native_root/collector/macos/Sources/Services/ContactWorkspace.swift" \
    "$native_root/collector/macos/Sources/Services/CompanionUpdater.swift" \
    "$native_root/collector/macos/Sources/Services/SupportDiagnosticReporter.swift" \
    "$native_root/collector/macos/Sources/Models/AgentEvent.swift" \
    "$native_root/collector/macos/Tests/CompanionUpdaterChecks.swift" \
    -o "$native_temp/CompanionUpdaterChecks"
DYLD_FRAMEWORK_PATH="$build_bin" "$native_temp/CompanionUpdaterChecks"
