#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:-dist/Unshuffle.app}"
OUTPUT_PATH="${2:-dist/installer/Unshuffle-macos.dmg}"
VOLUME_NAME="${3:-Unshuffle}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS DMG packaging requires macOS." >&2
  exit 1
fi

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found: $APP_PATH" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT

cp -R "$APP_PATH" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "$VOLUME_NAME" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$OUTPUT_PATH"

