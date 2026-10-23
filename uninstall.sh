#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/install.sh" ]; then
    exec "$SCRIPT_DIR/install.sh" --uninstall
else
    echo "Running ASSella Uninstaller..."
    INSTALL_DESTINATION="$HOME/.local/share/ACCELA"
    PRIMARY_DESKTOP_ENTRY="$HOME/.local/share/applications/assella.desktop"
    LEGACY_DESKTOP_ENTRY="$HOME/.local/share/applications/accela.desktop"
    PRIMARY_ICON_PATH="$HOME/.local/share/icons/hicolor/256x256/apps/assella.png"
    LEGACY_ICON_PATH="$HOME/.local/share/icons/hicolor/256x256/apps/accela.png"

    rm -f "$INSTALL_DESTINATION/ASSella.AppImage"
    rm -f "$INSTALL_DESTINATION/ACCELA.AppImage"
    rm -f "$INSTALL_DESTINATION/version"
    rm -f "$PRIMARY_DESKTOP_ENTRY" "$LEGACY_DESKTOP_ENTRY"
    rm -f "$PRIMARY_ICON_PATH" "$LEGACY_ICON_PATH"

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage.bak" ]; then
        mv "$INSTALL_DESTINATION/ACCELA.AppImage.bak" "$INSTALL_DESTINATION/ACCELA.AppImage"
        echo "Restored ACCELA.AppImage backup."
    fi

    echo "ASSella uninstalled."
fi
