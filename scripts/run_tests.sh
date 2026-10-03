#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== Running ASSella Pre-Release Test Suite against Source ==="
PYTHONPATH="$REPO_ROOT/src" python3 "$SCRIPT_DIR/prerelease_check.py"

ACCELA_HOME="${ACCELA_HOME:-$HOME/.local/share/ACCELA}"
APPIMAGE_PATH="$ACCELA_HOME/ASSella.AppImage"
SQUASH_SRC="$ACCELA_HOME/squashfs-root/bin/src"

if [ -d "$SQUASH_SRC" ]; then
    echo "=== Running ASSella Pre-Release Test Suite against Extracted AppImage ==="
    PYTHONPATH="$SQUASH_SRC" python3 "$SCRIPT_DIR/prerelease_check.py" --src "$SQUASH_SRC"
fi
