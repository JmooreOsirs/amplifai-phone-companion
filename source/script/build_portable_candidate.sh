#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname -m)" != arm64 ]; then
  echo "Use build_macos_intel_candidate.py for Intel; this script builds the retained ARM channel." >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/dist"
if [ "${1:-}" = "--isolated" ]; then
  OUTPUT_DIR="$(mktemp -d -t amplifai-revision.XXXXXX)"
  export AMPLIFAI_BUILD_OUTPUT_DIR="$OUTPUT_DIR"
elif [ "$#" -gt 0 ]; then
  echo "usage: $0 [--isolated]" >&2
  exit 2
else
  unset AMPLIFAI_BUILD_OUTPUT_DIR
fi
LOCAL_APP="$OUTPUT_DIR/AmplifaiPhone.app"
PORTABLE_APP="$OUTPUT_DIR/AmplifaiPhonePortable.app"
BUILD_ROOT="$(mktemp -d -t amplifai-portable-build.XXXXXX)"
trap 'rm -rf -- "$BUILD_ROOT"' EXIT

"$ROOT_DIR/script/build_and_run.sh" --build-only
swift build --package-path "$ROOT_DIR/collector/macos" --configuration release --arch arm64
RELEASE_BIN="$(swift build --package-path "$ROOT_DIR/collector/macos" --configuration release --arch arm64 --show-bin-path)"

# Fetch the exact provider runtime covered by the retained dependency closure.
# A generic managed 3.12 can silently change both Python and its static inputs.
INPUTS="$ROOT_DIR/collector/macos-release-source-inputs.json"
RUNTIME_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["channels"]["macos-arm64"]["runtime"]["url"])' "$INPUTS")"
RUNTIME_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["channels"]["macos-arm64"]["runtime"]["sha256"])' "$INPUTS")"
curl --fail --location --retry 3 "$RUNTIME_URL" -o "$BUILD_ROOT/python.tar.gz"
echo "$RUNTIME_SHA  $BUILD_ROOT/python.tar.gz" | shasum -a 256 --check
mkdir "$BUILD_ROOT/runtime"
tar -xzf "$BUILD_ROOT/python.tar.gz" -C "$BUILD_ROOT/runtime"
"$BUILD_ROOT/runtime/python/bin/python3.12" -c 'import platform,sys; assert platform.machine()=="arm64"; assert sys.version_info[:3]==(3,12,12)'
uv venv --python "$BUILD_ROOT/runtime/python/bin/python3.12" "$BUILD_ROOT/venv"
uv pip sync --require-hashes --python "$BUILD_ROOT/venv/bin/python" \
  "$ROOT_DIR/collector/portable-requirements.lock"

PYTHONPATH="$ROOT_DIR/collector" "$BUILD_ROOT/venv/bin/pyinstaller" \
  --noconfirm --clean --onedir --log-level WARN \
  --target-arch arm64 \
  --name amplifai-agent \
  --paths "$ROOT_DIR/collector" \
  --exclude-module PIL \
  --distpath "$BUILD_ROOT/dist" \
  --workpath "$BUILD_ROOT/work" \
  --specpath "$BUILD_ROOT/spec" \
  "$ROOT_DIR/collector/agent_entry.py"

if [ -e "$PORTABLE_APP" ]; then
  case "$PORTABLE_APP" in
    "$OUTPUT_DIR/AmplifaiPhonePortable.app") rm -rf -- "$PORTABLE_APP" ;;
    *) echo "Invalid portable app target" >&2; exit 1 ;;
  esac
fi
ditto "$LOCAL_APP" "$PORTABLE_APP"
cp "$RELEASE_BIN/AmplifaiPhone" "$PORTABLE_APP/Contents/MacOS/AmplifaiPhone"
ditto "$RELEASE_BIN/Sparkle.framework" "$PORTABLE_APP/Contents/Frameworks/Sparkle.framework"
python3 "$ROOT_DIR/script/normalize_macos_wrapper.py" "$PORTABLE_APP/Contents/MacOS/AmplifaiPhone" \
  --architecture arm64 > "$OUTPUT_DIR/wrapper-rpath-receipt.json"
RESOURCES="$PORTABLE_APP/Contents/Resources"
rm -- "$RESOURCES/uv" "$RESOURCES/uvx"
ditto "$BUILD_ROOT/dist/amplifai-agent" "$RESOURCES/helper"
"$RESOURCES/helper/amplifai-agent" runtime-check
codesign --force --sign - "$PORTABLE_APP"
codesign --verify --deep --strict "$PORTABLE_APP"
python3 "$ROOT_DIR/script/verify_macos_architecture.py" "$PORTABLE_APP" --arch arm64 --minimum-macos 14.0 \
  --output "$OUTPUT_DIR/architecture-receipt.json"
python3 "$ROOT_DIR/script/verify_frozen_companion_status.py" --helper "$RESOURCES/helper/amplifai-agent" \
  --architecture arm64 --version 2026.10.15-rc27 --build 26101527 > "$OUTPUT_DIR/status-receipt.json"
echo "$PORTABLE_APP"
