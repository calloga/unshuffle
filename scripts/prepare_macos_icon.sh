#!/usr/bin/env bash
set -euo pipefail

SOURCE_ICON="${1:-icons/app_logo.png}"
OUTPUT_ICON="${2:-build/app_icon.icns}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS icon preparation requires macOS." >&2
  exit 1
fi

ICONSET_DIR="$(mktemp -d)/Unshuffle.iconset"
trap 'rm -rf "$(dirname "$ICONSET_DIR")"' EXIT
mkdir -p "$ICONSET_DIR" "$(dirname "$OUTPUT_ICON")"

sips -z 16 16     "$SOURCE_ICON" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32     "$SOURCE_ICON" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32     "$SOURCE_ICON" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64     "$SOURCE_ICON" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128   "$SOURCE_ICON" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256   "$SOURCE_ICON" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256   "$SOURCE_ICON" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512   "$SOURCE_ICON" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512   "$SOURCE_ICON" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$SOURCE_ICON" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null

iconutil -c icns "$ICONSET_DIR" -o "$OUTPUT_ICON"
