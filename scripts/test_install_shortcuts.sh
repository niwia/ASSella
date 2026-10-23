#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_HOME=$(mktemp -d)
trap 'rm -rf "$TEST_HOME"' EXIT

# Source the installer so this test exercises its real desktop-entry helper
# without downloading a release or running the installer UI.
# shellcheck source=/dev/null
source "$REPO_DIR/install.sh"

DESKTOP_DIR="$TEST_HOME/.local/share/applications"
PRIMARY_DESKTOP_ENTRY="$DESKTOP_DIR/assella.desktop"
LEGACY_DESKTOP_ENTRY="$DESKTOP_DIR/accela.desktop"
EXEC_COMMAND="$TEST_HOME/.local/share/ACCELA/ASSella.AppImage %u"

mkdir -p "$DESKTOP_DIR"

# Reproduce the reported state: both desktop names point at each other.
ln -s accela.desktop "$PRIMARY_DESKTOP_ENTRY"
ln -s assella.desktop "$LEGACY_DESKTOP_ENTRY"

write_desktop_entry

test -f "$PRIMARY_DESKTOP_ENTRY"
test ! -L "$PRIMARY_DESKTOP_ENTRY"
test "$(readlink "$LEGACY_DESKTOP_ENTRY")" = "assella.desktop"
grep -q '^Exec=' "$PRIMARY_DESKTOP_ENTRY"
grep -q '^Icon=assella$' "$PRIMARY_DESKTOP_ENTRY"
grep -q '^Comment=Steam Game & Depot Management Suite$' "$PRIMARY_DESKTOP_ENTRY"
test -z "$DESKTOP_TMP"

# Test that paths containing spaces get properly quoted around the executable
EXEC_COMMAND="$TEST_HOME/Steam Deck User/.local/share/ACCELA/ASSella.AppImage %u"
write_desktop_entry
grep -q '^Exec="' "$PRIMARY_DESKTOP_ENTRY"
grep -q 'ASSella.AppImage" %u$' "$PRIMARY_DESKTOP_ENTRY"

# Test icon preservation on network failure
ICON_DIR="$TEST_HOME/.local/share/icons/hicolor/256x256/apps"
PRIMARY_ICON_PATH="$ICON_DIR/assella.png"
LEGACY_ICON_PATH="$ICON_DIR/accela.png"
mkdir -p "$ICON_DIR"
echo "existing icon data" > "$PRIMARY_ICON_PATH"
# Mock curl failure by pointing to non-existent local port
if command -v curl &>/dev/null; then
    curl() { return 1; }
    install_application_icon
    unset -f curl
    test -f "$PRIMARY_ICON_PATH"
    test "$(cat "$PRIMARY_ICON_PATH")" = "existing icon data"
    test -z "$ICON_TMP"
fi

echo "desktop shortcut symlink-loop & robust installer regression: PASS"

