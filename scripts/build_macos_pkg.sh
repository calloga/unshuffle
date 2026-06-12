#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:-dist/Unshuffle.app}"
OUTPUT_PATH="${2:-dist/installer/Unshuffle-macos.pkg}"
IDENTIFIER="${3:-com.umu.unshuffle}"
VERSION="${4:-1.0.0}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS PKG packaging requires macOS." >&2
  exit 1
fi

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found: $APP_PATH" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"

pkgbuild \
  --component "$APP_PATH" \
  --install-location /Applications \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  "$OUTPUT_PATH"

