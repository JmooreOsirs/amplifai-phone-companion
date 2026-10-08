#!/bin/sh
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
native_temp=$(mktemp -d)
trap 'if [ -f "$native_temp/CollectorFlowChecks" ]; then unlink "$native_temp/CollectorFlowChecks"; fi; rmdir "$native_temp"' EXIT
trap 'exit 130' HUP INT TERM
swiftc -parse-as-library -target arm64-apple-macos14.0 \
    "$native_root/collector/macos/Sources/Services/CollectorModel.swift" \
    "$native_root/collector/macos/Sources/Models/AgentEvent.swift" \
    "$native_root/collector/macos/Tests/CollectorFlowChecks.swift" \
    -o "$native_temp/CollectorFlowChecks"
"$native_temp/CollectorFlowChecks"
