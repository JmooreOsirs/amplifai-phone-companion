#!/bin/sh
set -eu
native_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
native_arch=${AMPLIFAI_TARGET_ARCH:-$(uname -m)}
case "$native_arch" in
    arm64|x86_64) ;;
    *) echo "AMPLIFAI_TARGET_ARCH must be arm64 or x86_64" >&2; exit 2 ;;
esac
native_temp=$(mktemp -d)
trap 'rm -f -- "$native_temp/CompanionUpdaterChecks" "$native_temp/status-fixture"; rmdir "$native_temp"' EXIT
trap 'exit 130' HUP INT TERM
swift build --package-path "$native_root/collector/macos" --configuration release --arch "$native_arch"
build_bin=$(swift build --package-path "$native_root/collector/macos" --configuration release --arch "$native_arch" --show-bin-path)
swiftc -parse-as-library -target "$native_arch-apple-macos14.0" -F "$build_bin" \
    "$native_root/collector/macos/Sources/Services/CollectorModel.swift" \
    "$native_root/collector/macos/Sources/Services/ContactSuggestions.swift" \
    "$native_root/collector/macos/Sources/Services/ContactWorkspace.swift" \
    "$native_root/collector/macos/Sources/Services/CompanionUpdater.swift" \
    "$native_root/collector/macos/Sources/Services/CompanionStatusService.swift" \
    "$native_root/collector/macos/Sources/Services/SupportDiagnosticReporter.swift" \
    "$native_root/collector/macos/Sources/Models/AgentEvent.swift" \
    "$native_root/collector/macos/Tests/CompanionUpdaterChecks.swift" \
    -o "$native_temp/CompanionUpdaterChecks"
cat > "$native_temp/status-fixture" <<'EOF'
#!/bin/sh
echo '{"event":"status_ready"}'
while IFS= read -r status; do :; done
EOF
chmod +x "$native_temp/status-fixture"
DYLD_FRAMEWORK_PATH="$build_bin" "$native_temp/CompanionUpdaterChecks" "$native_temp/status-fixture"
