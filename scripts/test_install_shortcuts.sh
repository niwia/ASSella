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

# install.sh installs its own cleanup trap, replacing the one above. Re-register
# one that also removes the test home, or every run leaks a temp directory.
trap 'cleanup; rm -rf "$TEST_HOME"' EXIT INT TERM

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
grep -q '^StartupWMClass=ASSella$' "$PRIMARY_DESKTOP_ENTRY"
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

# Interrupted-run cleanup: tracked temp files and stale hidden mktemp leftovers
# must be removed, while the committed desktop entry survives.
DESKTOP_TMP=$(mktemp "$DESKTOP_DIR/.assella.desktop.tmp.XXXXXX")
ICON_TMP=$(mktemp "$ICON_DIR/.assella.png.tmp.XXXXXX")
stale_desktop="$DESKTOP_DIR/.assella.desktop.tmp.stale"
stale_icon="$ICON_DIR/.assella.png.tmp.stale"
: > "$stale_desktop"
: > "$stale_icon"
cleanup
test ! -e "$DESKTOP_TMP"
test ! -e "$ICON_TMP"
test ! -e "$stale_desktop"
test ! -e "$stale_icon"
test -f "$PRIMARY_DESKTOP_ENTRY"
DESKTOP_TMP=""
ICON_TMP=""

# Stdin pipe execution (curl ... | bash): BASH_SOURCE must not trigger an
# unbound variable error under set -u, and must execute main.
help_output=$(cat "$REPO_DIR/install.sh" | bash -s -- --help)
echo "$help_output" | grep -q 'ASSella Installer & Management Suite'

echo "desktop shortcut symlink-loop & robust installer regression: PASS"

