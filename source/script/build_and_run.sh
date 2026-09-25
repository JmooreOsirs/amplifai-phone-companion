#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
case "$MODE" in
  run|--build-only|--verify|--debug|--logs|--telemetry) ;;
  *) echo "usage: $0 [run|--build-only|--verify|--debug|--logs|--telemetry]" >&2; exit 2 ;;
esac
APP_NAME="AmplifaiPhone"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
if [ -n "${AMPLIFAI_BUILD_OUTPUT_DIR:-}" ]; then
  if [ "$MODE" != "--build-only" ] || [ ! -d "$AMPLIFAI_BUILD_OUTPUT_DIR" ] || [ -L "$AMPLIFAI_BUILD_OUTPUT_DIR" ]; then
    echo "Isolated output requires an existing real directory and --build-only" >&2
    exit 2
  fi
  DIST_DIR="$(cd "$AMPLIFAI_BUILD_OUTPUT_DIR" && pwd -P)"
  case "$DIST_DIR" in
    /private/var/folders/*/T/amplifai-revision.*|/var/folders/*/T/amplifai-revision.*|/private/tmp/amplifai-revision.*|/tmp/amplifai-revision.*) ;;
    *) echo "Invalid isolated output directory" >&2; exit 2 ;;
  esac
fi
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
CONTENTS="$APP_BUNDLE/Contents"
RESOURCES="$CONTENTS/Resources"
SWIFT_PACKAGE="$ROOT_DIR/collector/macos"
UVX_BIN="$(command -v uvx)"
UV_BIN="$(command -v uv)"

if [ ! -x "$UVX_BIN" ] || [ ! -x "$UV_BIN" ]; then
  echo "uv and uvx are required to build the local collector candidate" >&2
  exit 1
fi

swift build --package-path "$SWIFT_PACKAGE"
BUILD_BIN="$(swift build --package-path "$SWIFT_PACKAGE" --show-bin-path)/$APP_NAME"

mkdir -p "$DIST_DIR"
if [ -d "$APP_BUNDLE" ]; then
  rm -rf "$APP_BUNDLE"
fi
mkdir -p "$CONTENTS/MacOS" "$RESOURCES/collector/amplifai_phone"
cp "$BUILD_BIN" "$CONTENTS/MacOS/$APP_NAME"
cp "$SWIFT_PACKAGE/Info.plist" "$CONTENTS/Info.plist"
cp "$UVX_BIN" "$RESOURCES/uvx"
cp "$UV_BIN" "$RESOURCES/uv"
cp "$ROOT_DIR"/collector/amplifai_phone/*.py "$RESOURCES/collector/amplifai_phone/"
cp "$ROOT_DIR/public/brand/amplifai-by-nexus-original.png" "$RESOURCES/amplifai-logo.png"
chmod +x "$CONTENTS/MacOS/$APP_NAME" "$RESOURCES/uvx" "$RESOURCES/uv"

if [ "$MODE" = "--build-only" ]; then
  echo "$APP_BUNDLE"
  exit 0
fi

pkill -x "$APP_NAME" >/dev/null 2>&1 || true
case "$MODE" in
  run)
    /usr/bin/open -n "$APP_BUNDLE"
    ;;
  --verify)
    /usr/bin/open -n "$APP_BUNDLE"
    sleep 1
    pgrep -x "$APP_NAME" >/dev/null
    ;;
  --debug)
    lldb -- "$CONTENTS/MacOS/$APP_NAME"
    ;;
  --logs)
    /usr/bin/open -n "$APP_BUNDLE"
    /usr/bin/log stream --info --style compact --predicate 'process == "AmplifaiPhone"'
    ;;
  --telemetry)
    /usr/bin/open -n "$APP_BUNDLE"
    /usr/bin/log stream --info --style compact --predicate 'subsystem == "ai.satoris.amplifai.phone.candidate"'
    ;;
esac
