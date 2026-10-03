#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#                      🚀 ASSella Installer & Manager Suite
# ==============================================================================

# Terminal Colors & Styling
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

INSTALL_DESTINATION="$HOME/.local/share/ACCELA"
DESKTOP_ENTRY="$HOME/.local/share/applications/accela.desktop"
ICON_PATH="$HOME/.local/share/icons/hicolor/256x256/apps/accela.png"
VERSION_FILE="$INSTALL_DESTINATION/version"
NIXOS_LAUNCHER="$INSTALL_DESTINATION/launch_nixos.sh"

# ------------------------------------------------------------------------------
#  1. System & Distro Detection
# ------------------------------------------------------------------------------
detect_distro() {
    DISTRO_ID="unknown"
    DISTRO_NAME="Generic Linux"
    DISTRO_LIKE=""
    IS_STEAM_DECK=false
    IS_NIXOS=false
    IS_CACHYOS=false

    if [ -f /etc/os-release ]; then
        # Source os-release safely
        set +e
        . /etc/os-release
        set -e
        DISTRO_ID="${ID:-unknown}"
        DISTRO_NAME="${PRETTY_NAME:-${NAME:-Generic Linux}}"
        DISTRO_LIKE="${ID_LIKE:-}"
    fi

    if [[ "$DISTRO_ID" == "steamos" ]] || [[ -f /etc/steamos-release ]] || [[ "$HOME" == *deck* ]]; then
        IS_STEAM_DECK=true
    fi

    if [[ "$DISTRO_ID" == "nixos" ]]; then
        IS_NIXOS=true
    fi

    if [[ "$DISTRO_ID" == "cachyos" ]] || [[ "$DISTRO_NAME" == *"CachyOS"* ]]; then
        IS_CACHYOS=true
    fi
}

check_fuse_status() {
    FUSE_INSTALLED=false
    if command -v ldconfig &>/dev/null; then
        if ldconfig -p 2>/dev/null | grep -q "libfuse.so.2"; then
            FUSE_INSTALLED=true
        fi
    fi

    if [ "$FUSE_INSTALLED" = false ]; then
        if [ -f /usr/lib/libfuse.so.2 ] || [ -f /lib/libfuse.so.2 ] || [ -f /usr/lib64/libfuse.so.2 ] || [ -f /lib64/libfuse.so.2 ] || [ -f /lib/x86_64-linux-gnu/libfuse.so.2 ]; then
            FUSE_INSTALLED=true
        fi
    fi
}

check_headcrab_status() {
    HEADCRAB_INSTALLED=false
    if [ -d "$HOME/.config/SLSsteam" ] || [ -d "/tmp/SLSsteam" ]; then
        HEADCRAB_INSTALLED=true
    elif command -v systemctl &>/dev/null && systemctl --user is-active slssteam &>/dev/null; then
        HEADCRAB_INSTALLED=true
    fi
}

get_local_version() {
    LOCAL_VER="Not Installed"
    if [ -f "$VERSION_FILE" ]; then
        LOCAL_VER=$(cat "$VERSION_FILE" | tr -d '\r\n')
    elif [ -f "$INSTALL_DESTINATION/squashfs-root/bin/src/res/version" ]; then
        LOCAL_VER=$(cat "$INSTALL_DESTINATION/squashfs-root/bin/src/res/version" | tr -d '\r\n')
    fi
}

get_latest_github_version() {
    LATEST_VER="Unknown"
    LATEST_URL=""

    # Determine channel from global or args or local version
    TARGET_CH="${INSTALL_CHANNEL:-auto}"
    if [ "$TARGET_CH" = "auto" ]; then
        LOCAL_LOWER=$(echo "$LOCAL_VER" | tr '[:upper:]' '[:lower:]')
        if echo "$LOCAL_LOWER" | grep -q -E 'canary|testing'; then
            TARGET_CH="canary"
        elif echo "$LOCAL_LOWER" | grep -q -E 'beta|dev|rc'; then
            TARGET_CH="beta"
        else
            TARGET_CH="stable"
        fi
    fi

    REL_JSON=$(curl -s "https://api.github.com/repos/niwia/ASSella/releases" || true)
    if [ -n "$REL_JSON" ]; then
        if command -v python3 &>/dev/null; then
            PARSED=$(echo "$REL_JSON" | python3 -c '
import sys, json

target_ch = sys.argv[1].lower() if len(sys.argv) > 1 else "stable"

def matches_channel(tag: str, ch: str) -> bool:
    t = tag.lower()
    if ch == "canary":
        return any(x in t for x in ("canary", "testing"))
    if ch == "beta":
        return any(x in t for x in ("beta", "dev", "rc"))
    # stable
    return ("stable" in t) or not any(x in t for x in ("canary", "testing", "beta", "dev", "rc", "alpha"))

def asset_matches(name: str, ch: str) -> bool:
    an = name.lower()
    if not an.endswith(".appimage") or "zsync" in an:
        return False
    if ch == "canary":
        return any(x in an for x in ("canary", "testing")) or not any(x in an for x in ("beta", "stable"))
    if ch == "beta":
        return any(x in an for x in ("beta", "dev", "rc")) or (an == "assella.appimage")
    return ("stable" in an) or (an == "assella.appimage") or not any(x in an for x in ("canary", "testing", "beta", "dev", "rc", "alpha"))

try:
    releases = json.load(sys.stdin)
    for r in releases:
        tag = r.get("tag_name", "").strip()
        if not matches_channel(tag, target_ch):
            continue
        for a in r.get("assets", []):
            name = a.get("name", "")
            if asset_matches(name, target_ch):
                print(tag)
                print(a.get("browser_download_url", ""))
                sys.exit(0)
except Exception:
    pass
' "$TARGET_CH" || true)
            if [ -n "$PARSED" ]; then
                TAG_NAME=$(echo "$PARSED" | sed -n '1p')
                DL_URL=$(echo "$PARSED" | sed -n '2p')
                if [ -n "$TAG_NAME" ]; then LATEST_VER="$TAG_NAME"; fi
                if [ -n "$DL_URL" ]; then LATEST_URL="$DL_URL"; fi
            fi
        fi

        if [ "$LATEST_VER" = "Unknown" ]; then
            if [ "$TARGET_CH" = "canary" ]; then
                TAG_NAME=$(echo "$REL_JSON" | grep -o '"tag_name": *"[^"]*"' | grep -i -E 'canary|testing' | head -n 1 | cut -d '"' -f 4 || true)
            elif [ "$TARGET_CH" = "beta" ]; then
                TAG_NAME=$(echo "$REL_JSON" | grep -o '"tag_name": *"[^"]*"' | grep -i -E 'beta|dev|rc' | head -n 1 | cut -d '"' -f 4 || true)
            else
                TAG_NAME=$(echo "$REL_JSON" | grep -o '"tag_name": *"[^"]*"' | grep -v -i -E 'canary|testing|beta|dev|rc|alpha' | head -n 1 | cut -d '"' -f 4 || true)
            fi
            if [ -n "$TAG_NAME" ]; then
                LATEST_VER="$TAG_NAME"
            fi

            DL_URL=$(echo "$REL_JSON" | grep -o '"browser_download_url": *"[^"]*ASSella[^"]*\.AppImage"' | head -n 1 | cut -d '"' -f 4 || true)
            if [ -n "$DL_URL" ]; then
                LATEST_URL="$DL_URL"
            fi
        fi
    fi

    if [ -z "$LATEST_URL" ]; then
        LATEST_URL="https://github.com/niwia/ASSella/releases/download/v2.6.5/ASSella.AppImage"
    fi
}

# ------------------------------------------------------------------------------
#  2. Header & Status Display
# ------------------------------------------------------------------------------
show_header() {
    clear
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
    echo -e "${GREEN}${BOLD}                 🚀 ASSella Installer & Manager Suite             ${NC}"
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
    echo -e "  ${BOLD}OS Detected:${NC}      $DISTRO_NAME ($(uname -m))"
    if [ "$IS_STEAM_DECK" = true ]; then
        echo -e "  ${BOLD}Device:${NC}           Steam Deck (SteamOS)"
    fi

    # Headcrab Status
    if [ "$HEADCRAB_INSTALLED" = true ]; then
        echo -e "  ${BOLD}Headcrab (SLS):${NC}   ${GREEN}Installed (~/.config/SLSsteam)${NC}"
    else
        echo -e "  ${BOLD}Headcrab (SLS):${NC}   ${YELLOW}Not Detected (Required for depot downloads)${NC}"
    fi

    # FUSE Status
    if [ "$IS_NIXOS" = true ]; then
        echo -e "  ${BOLD}AppImage FUSE:${NC}    ${CYAN}NixOS Mode (Uses steam-run / appimage-run wrapper)${NC}"
    elif [ "$FUSE_INSTALLED" = true ]; then
        echo -e "  ${BOLD}AppImage FUSE:${NC}    ${GREEN}OK (libfuse.so.2 found)${NC}"
    else
        echo -e "  ${BOLD}AppImage FUSE:${NC}    ${RED}WARNING: fuse2 missing (Required for AppImages)${NC}"
    fi

    # Version Status
    echo -e "  ${BOLD}Local Version:${NC}    $LOCAL_VER"
    echo -e "  ${BOLD}Latest Online:${NC}    $LATEST_VER"
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
}

# ------------------------------------------------------------------------------
#  3. Distro-Specific Requirements Guide
# ------------------------------------------------------------------------------
pause_if_interactive() {
    if [ "${INTERACTIVE:-false}" = true ]; then
        read -p "Press Enter to continue..." dummy
    fi
}

show_distro_guide() {
    show_header
    echo -e "\n${YELLOW}${BOLD}=== 📋 Distro-Specific Setup & Requirements ===${NC}\n"

    if [ "$IS_NIXOS" = true ]; then
        echo -e "${CYAN}${BOLD}❄️ NixOS Installation Guide:${NC}"
        echo -e "NixOS does not use standard /lib64 glibc linkers out of the box."
        echo -e "ASSella automatically generates a launcher using ${GREEN}steam-run${NC} or ${GREEN}appimage-run${NC}.\n"
        echo -e "  ${BOLD}Command to launch directly:${NC}"
        echo -e "    ${GREEN}nix-shell -p steam-run --run 'steam-run ~/.local/share/ACCELA/ASSella.AppImage'${NC}\n"
        echo -e "  ${BOLD}Global fix (Optional):${NC} Add ${GREEN}programs.nix-ld.enable = true;${NC} in /etc/nixos/configuration.nix"
    elif [ "$IS_CACHYOS" = true ] || [[ "$DISTRO_ID" == "arch" ]] || [[ "$DISTRO_LIKE" == *"arch"* ]]; then
        echo -e "${CYAN}${BOLD}⚡ CachyOS / Arch Linux Guide:${NC}"
        echo -e "Arch-based distros require ${GREEN}fuse2${NC} to execute AppImages.\n"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo pacman -S fuse2${NC}"
    elif [[ "$DISTRO_ID" == "ubuntu" ]] || [[ "$DISTRO_ID" == "debian" ]] || [[ "$DISTRO_LIKE" == *"ubuntu"* ]]; then
        echo -e "${CYAN}${BOLD}🐧 Ubuntu / Debian / Mint Guide:${NC}"
        echo -e "Ubuntu 22.04+ requires ${GREEN}libfuse2${NC} for AppImages.\n"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo apt install -y libfuse2${NC}"
    elif [[ "$DISTRO_ID" == "fedora" ]] || [[ "$DISTRO_LIKE" == *"fedora"* ]]; then
        echo -e "${CYAN}${BOLD}🎩 Fedora / RHEL Guide:${NC}"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo dnf install -y fuse-libs${NC}"
    elif [[ "$DISTRO_ID" == *"suse"* ]]; then
        echo -e "${CYAN}${BOLD}🦎 openSUSE Guide:${NC}"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo zypper install libfuse2${NC}"
    else
        echo -e "${CYAN}${BOLD}🐧 Generic Linux Guide:${NC}"
        echo -e "Ensure ${GREEN}libfuse2${NC} or ${GREEN}fuse2${NC} package is installed on your system."
    fi

    echo -e "\n${CYAN}${BOLD}-------------------------------------------------------------------${NC}"
    echo -e "  ${BOLD}Headcrab (SLSsteam Daemon):${NC}"
    echo -e "  Headcrab intercepts Steam depot requests to allow game downloads."
    echo -e "  Install/Update one-liner: ${GREEN}curl -fsSL headcrab.pages.dev | bash${NC}"
    echo -e "${CYAN}${BOLD}-------------------------------------------------------------------${NC}\n"

    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  4. Headcrab (SLSsteam) Installer Runner
# ------------------------------------------------------------------------------
install_headcrab() {
    echo -e "\n${YELLOW}[INFO] Running Headcrab (SLSsteam) installer script...${NC}"
    echo -e "${GREEN}Executing: curl -fsSL headcrab.pages.dev | bash${NC}\n"
    
    if curl -fsSL headcrab.pages.dev | bash; then
        echo -e "\n${GREEN}✓ Headcrab (SLSsteam) script executed successfully!${NC}"
    else
        echo -e "\n${RED}❌ Headcrab installation failed. Please check network connection.${NC}"
    fi

    check_headcrab_status
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  5. Main Installation & Update Engine
# ------------------------------------------------------------------------------
do_install() {
    echo -e "\n${YELLOW}[INFO] Installing / Updating ASSella...${NC}"
    mkdir -p "$INSTALL_DESTINATION"

    # NOTE: the existing binary is NOT backed up here. Rotation happens after
    # the download has been verified, so a failed or corrupt install can never
    # destroy the last known-good binary.
    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage" ] && [ ! -L "$INSTALL_DESTINATION/ACCELA.AppImage" ]; then
        echo -e "${YELLOW}[INFO] Existing ACCELA.AppImage found (will back up after verification).${NC}"
    fi

    # Fetch AppImage binary.
    # Download to a temp file, verify, then move into place atomically. This
    # prevents a truncated or corrupt download from ever becoming the installed
    # binary, and stops a corrupt binary from being swept into the .bak
    # rotation where --restore would later hand it back to the user.
    get_latest_github_version
    echo -e "${YELLOW}[INFO] Downloading ASSella.AppImage ($LATEST_VER)...${NC}"

    DL_TMP="$(mktemp "${INSTALL_DESTINATION}/.ASSella.AppImage.XXXXXX")" || {
        echo -e "${RED}[ERROR] Could not create temp file in $INSTALL_DESTINATION${NC}"
        pause_if_interactive
        return 1
    }

    if ! curl -fL --progress-bar -o "$DL_TMP" "$LATEST_URL"; then
        echo -e "${RED}[ERROR] Download failed! Please check your connection to GitHub.${NC}"
        rm -f "$DL_TMP"
        pause_if_interactive
        return 1
    fi

    # --- Integrity check ----------------------------------------------------
    # Each release publishes <asset>.sha256 next to the AppImage.
    #
    # Mismatch is fatal. A missing checksum is a warning only: older releases
    # predate checksum publishing, and refusing them would lock users out of a
    # perfectly working binary over absent metadata. Set ASSELLA_REQUIRE_SHA256=1
    # to make a missing checksum fatal too, or ASSELLA_NO_VERIFY=1 to bypass.
    local sha_state="missing"
    local expected_sha="" actual_sha=""

    if curl -fsL --max-time 20 -o "$DL_TMP.sha256" "${LATEST_URL}.sha256" 2>/dev/null; then
        expected_sha=$(grep -oiE '[a-f0-9]{64}' "$DL_TMP.sha256" | head -n1)
        if [ -z "$expected_sha" ]; then
            sha_state="malformed"
        else
            if command -v sha256sum &>/dev/null; then
                actual_sha=$(sha256sum "$DL_TMP" | awk '{print $1}')
            elif command -v shasum &>/dev/null; then
                actual_sha=$(shasum -a 256 "$DL_TMP" | awk '{print $1}')
            fi
            if [ -z "$actual_sha" ]; then
                sha_state="notool"
            elif [ "$(printf '%s' "$expected_sha" | tr 'A-F' 'a-f')" = "$(printf '%s' "$actual_sha" | tr 'A-F' 'a-f')" ]; then
                sha_state="ok"
            else
                sha_state="mismatch"
            fi
        fi
    fi
    rm -f "$DL_TMP.sha256"

    case "$sha_state" in
        ok)
            echo -e "${GREEN}[OK] SHA-256 verified: $actual_sha${NC}"
            ;;
        mismatch)
            echo -e "${RED}${BOLD}[ERROR] CHECKSUM MISMATCH - download is corrupt or was tampered with.${NC}"
            echo -e "${RED}  expected: $expected_sha${NC}"
            echo -e "${RED}  actual:   $actual_sha${NC}"
            if [ "${ASSELLA_NO_VERIFY:-0}" = "1" ]; then
                echo -e "${YELLOW}ASSELLA_NO_VERIFY=1 set, continuing without verification.${NC}"
            else
                rm -f "$DL_TMP"
                echo -e "${RED}Refusing to install. Your existing installation is untouched.${NC}"
                echo -e "${YELLOW}If this release predates checksum publishing, re-run with${NC}"
                echo -e "${YELLOW}ASSELLA_NO_VERIFY=1 to install it anyway.${NC}"
                pause_if_interactive
                return 1
            fi
            ;;
        malformed|notool)
            echo -e "${YELLOW}[WARN] Checksum unusable ($sha_state); cannot verify integrity.${NC}"
            ;;
        missing)
            echo -e "${YELLOW}[WARN] No published .sha256 for this release; integrity NOT verified.${NC}"
            if [ "${ASSELLA_REQUIRE_SHA256:-0}" = "1" ]; then
                echo -e "${RED}[ERROR] ASSELLA_REQUIRE_SHA256=1 set, refusing unverified download.${NC}"
                rm -f "$DL_TMP"
                pause_if_interactive
                return 1
            fi
            ;;
    esac

    # Promote the verified download into place, then rotate the previous binary
    # into the .bak restore point so a previously-corrupt file is never kept.
    if ! mv -f "$DL_TMP" "$INSTALL_DESTINATION/ASSella.AppImage"; then
        echo -e "${RED}[ERROR] Failed to move the downloaded binary into place.${NC}"
        rm -f "$DL_TMP"
        pause_if_interactive
        return 1
    fi

    chmod +x "$INSTALL_DESTINATION/ASSella.AppImage"

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage" ] && [ ! -L "$INSTALL_DESTINATION/ACCELA.AppImage" ]; then
        echo -e "${YELLOW}[INFO] Backing up previous ACCELA.AppImage to ACCELA.AppImage.bak...${NC}"
        mv -f "$INSTALL_DESTINATION/ACCELA.AppImage" "$INSTALL_DESTINATION/ACCELA.AppImage.bak"
    fi


    # Save local version file
    echo "$LATEST_VER" > "$VERSION_FILE"

    # Create symlink for ACCELA compatibility
    ln -sf ASSella.AppImage "$INSTALL_DESTINATION/ACCELA.AppImage"
    echo -e "${GREEN}[INFO] Created compatibility symlink ACCELA.AppImage -> ASSella.AppImage.${NC}"

    # Handle NixOS Launcher Script
    if [ "$IS_NIXOS" = true ]; then
        echo -e "${YELLOW}[INFO] Configuring NixOS compatibility launcher...${NC}"
        cat >"$NIXOS_LAUNCHER" <<'EOL'
#!/usr/bin/env bash
if command -v steam-run &>/dev/null; then
    exec steam-run "$HOME/.local/share/ACCELA/ASSella.AppImage" "$@"
elif command -v appimage-run &>/dev/null; then
    exec appimage-run "$HOME/.local/share/ACCELA/ASSella.AppImage" "$@"
else
    echo "NixOS detected! Please install steam-run or appimage-run to launch ASSella:"
    echo "  nix-shell -p steam-run --run 'steam-run ~/.local/share/ACCELA/ASSella.AppImage'"
    read -p "Press Enter to exit..."
fi
EOL
        chmod +x "$NIXOS_LAUNCHER"
        EXEC_COMMAND="$NIXOS_LAUNCHER %u"
    else
        EXEC_COMMAND="$INSTALL_DESTINATION/ACCELA.AppImage %u"
    fi

    # Desktop Shortcut Creation / Patch
    echo -e "${YELLOW}[INFO] Configuring desktop shortcut...${NC}"
    mkdir -p "$(dirname "$DESKTOP_ENTRY")"
    cat >"$DESKTOP_ENTRY" <<EOL
[Desktop Entry]
Version=2.0
Name=ASSella
Comment=god is in the ass
Exec=$EXEC_COMMAND
Icon=assella
Terminal=false
Type=Application
Categories=Utility;Game;
MimeType=x-scheme-handler/accela;
StartupWMClass=ASSella
EOL
    # Provide assella.desktop as alias/symlink
    ln -sf "$DESKTOP_ENTRY" "$(dirname "$DESKTOP_ENTRY")/assella.desktop" 2>/dev/null || true

    # Application Icon Deployment (512x512 and 256x256)
    echo -e "${YELLOW}[INFO] Applying application icon...${NC}"
    mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps" "$HOME/.local/share/icons/hicolor/512x512/apps"
    if [ -f "$INSTALL_DESTINATION/Logo/assella_500x500.png" ]; then
        cp -f "$INSTALL_DESTINATION/Logo/assella_500x500.png" "$HOME/.local/share/icons/hicolor/512x512/apps/assella.png"
        cp -f "$INSTALL_DESTINATION/Logo/assella_500x500.png" "$HOME/.local/share/icons/hicolor/512x512/apps/accela.png"
        cp -f "$INSTALL_DESTINATION/Logo/assella_500x500.png" "$HOME/.local/share/icons/hicolor/256x256/apps/assella.png"
        cp -f "$INSTALL_DESTINATION/Logo/assella_500x500.png" "$ICON_PATH"
    else
        curl -sL -o "$ICON_PATH" "https://raw.githubusercontent.com/niwia/ASSella/beta/src/res/logo/icon.png" || true
        cp -f "$ICON_PATH" "$HOME/.local/share/icons/hicolor/256x256/apps/assella.png" 2>/dev/null || true
    fi

    # Update system desktop database
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$(dirname "$DESKTOP_ENTRY")" 2>/dev/null || true
    fi
    if [ -z "${XDG_CURRENT_DESKTOP:-}" ] || [[ "$XDG_CURRENT_DESKTOP" != *"KDE"* ]]; then
        command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    fi

    # Warn about FUSE if missing
    if [ "$FUSE_INSTALLED" = false ] && [ "$IS_NIXOS" = false ]; then
        echo -e "\n${RED}${BOLD}⚠️ WARNING: FUSE (libfuse.so.2) is not installed on your system!${NC}"
        if [ "$IS_CACHYOS" = true ] || [[ "$DISTRO_ID" == "arch" ]]; then
            echo -e "${YELLOW}Please run: sudo pacman -S fuse2${NC}"
        elif [[ "$DISTRO_ID" == "ubuntu" ]] || [[ "$DISTRO_ID" == "debian" ]]; then
            echo -e "${YELLOW}Please run: sudo apt install libfuse2${NC}"
        elif [[ "$DISTRO_ID" == "fedora" ]]; then
            echo -e "${YELLOW}Please run: sudo dnf install fuse-libs${NC}"
        fi
    fi

    echo -e "\n${GREEN}${BOLD}=========================================${NC}"
    echo -e "${GREEN}${BOLD}✓ ASSella has been installed & patched!  ${NC}"
    echo -e "${GREEN}${BOLD}=========================================${NC}\n"

    get_local_version
}

# ------------------------------------------------------------------------------
#  6. Restore Original ACCELA Backup
# ------------------------------------------------------------------------------
do_restore_accela() {
    echo -e "\n${YELLOW}[INFO] Restoring original ACCELA backup...${NC}"

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage.bak" ]; then
        rm -f "$INSTALL_DESTINATION/ACCELA.AppImage"
        mv "$INSTALL_DESTINATION/ACCELA.AppImage.bak" "$INSTALL_DESTINATION/ACCELA.AppImage"
        chmod +x "$INSTALL_DESTINATION/ACCELA.AppImage"
        echo -e "${GREEN}[INFO] Restored ACCELA.AppImage from backup.${NC}"
    else
        echo -e "${YELLOW}[WARNING] No ACCELA.AppImage.bak found to restore.${NC}"
    fi

    if [ -f "$DESKTOP_ENTRY" ]; then
        sed -i 's/^Name=ASSella$/Name=ACCELA/' "$DESKTOP_ENTRY"
        echo -e "${GREEN}[INFO] Restored desktop entry name to ACCELA.${NC}"
    fi

    echo -e "\n${GREEN}✓ ACCELA restoration complete!${NC}"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  7. Clean Uninstall ASSella
# ------------------------------------------------------------------------------
do_uninstall() {
    echo -e "\n${RED}${BOLD}=== 🗑️ ASSella Clean Uninstaller ===${NC}\n"
    if [ "${INTERACTIVE:-false}" = true ]; then
        read -p "Are you sure you want to uninstall ASSella? [y/N]: " confirm
        if [[ "$confirm" != "y" ]] && [[ "$confirm" != "Y" ]]; then
            echo "Uninstallation cancelled."
            return
        fi
    fi

    echo -e "${YELLOW}[INFO] Removing binaries, symlinks, and desktop entries...${NC}"
    rm -f "$INSTALL_DESTINATION/ASSella.AppImage"
    rm -f "$INSTALL_DESTINATION/ACCELA.AppImage"
    rm -f "$INSTALL_DESTINATION/version"
    rm -f "$NIXOS_LAUNCHER"
    rm -f "$DESKTOP_ENTRY"
    rm -f "$ICON_PATH"

    # Restore backup if available
    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage.bak" ]; then
        mv "$INSTALL_DESTINATION/ACCELA.AppImage.bak" "$INSTALL_DESTINATION/ACCELA.AppImage"
        echo -e "${GREEN}[INFO] Restored original ACCELA.AppImage backup.${NC}"
    fi

    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$(dirname "$DESKTOP_ENTRY")" 2>/dev/null || true
    fi

    echo -e "\n${GREEN}✓ ASSella has been uninstalled.${NC}"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  8. Pre-Flight Diagnostics
# ------------------------------------------------------------------------------
run_diagnostics() {
    show_header
    echo -e "\n${YELLOW}${BOLD}=== 🔍 ASSella Pre-Flight Diagnostics ===${NC}\n"
    
    echo -n "  1. Python 3: "
    if command -v python3 &>/dev/null; then
        echo -e "${GREEN}OK ($(python3 --version | cut -d' ' -f2))${NC}"
    else
        echo -e "${RED}MISSING${NC}"
    fi

    echo -n "  2. Curl utility: "
    if command -v curl &>/dev/null; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}MISSING${NC}"
    fi

    echo -n "  3. FUSE (libfuse.so.2): "
    if [ "$FUSE_INSTALLED" = true ] || [ "$IS_NIXOS" = true ]; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}MISSING (AppImage won't open without fuse2)${NC}"
    fi

    echo -n "  4. Headcrab (SLSsteam): "
    if [ "$HEADCRAB_INSTALLED" = true ]; then
        echo -e "${GREEN}INSTALLED${NC}"
    else
        echo -e "${YELLOW}NOT INSTALLED (Run option 3 to install)${NC}"
    fi

    echo -n "  5. Desktop Shortcut: "
    if [ -f "$DESKTOP_ENTRY" ]; then
        echo -e "${GREEN}OK ($DESKTOP_ENTRY)${NC}"
    else
        echo -e "${YELLOW}NOT CREATED YET${NC}"
    fi

    echo -e "\n${CYAN}${BOLD}-------------------------------------------------------------------${NC}\n"
    read -p "Press Enter to return to main menu..." dummy
}

# ------------------------------------------------------------------------------
#  9. Interactive Main Menu Loop
# ------------------------------------------------------------------------------
interactive_menu() {
    INTERACTIVE=true
    detect_distro
    check_fuse_status
    check_headcrab_status
    get_local_version
    get_latest_github_version

    while true; do
        show_header
        echo -e "  ${BOLD}[1]${NC} Install / Update ASSella (Recommended)"
        echo -e "  ${BOLD}[2]${NC} Check for Updates & View Release Info"
        echo -e "  ${BOLD}[3]${NC} Install / Update Headcrab (SLSsteam Daemon)"
        echo -e "  ${BOLD}[4]${NC} View Distro-Specific Requirements Guide (CachyOS, NixOS, Arch...)"
        echo -e "  ${BOLD}[5]${NC} Restore Original ACCELA (Revert Backup)"
        echo -e "  ${BOLD}[6]${NC} Uninstall ASSella (Clean Files & Shortcuts)"
        echo -e "  ${BOLD}[7]${NC} Run Pre-Flight Diagnostics"
        echo -e "  ${BOLD}[8]${NC} Exit"
        echo -e "${CYAN}${BOLD}===================================================================${NC}"
        read -p "Select option [1-8]: " choice

        case "$choice" in
            1)
                do_install
                read -p "Press Enter to continue..." dummy
                ;;
            2)
                show_header
                echo -e "\n${YELLOW}Installed:${NC} $LOCAL_VER  -->  ${GREEN}Latest Online:${NC} $LATEST_VER"
                if [ "$LOCAL_VER" != "$LATEST_VER" ]; then
                    echo -e "${GREEN}${BOLD}An update is available! Select Option 1 to update.${NC}\n"
                else
                    echo -e "${GREEN}You are already on the latest version!${NC}\n"
                fi
                read -p "Press Enter to continue..." dummy
                ;;
            3)
                install_headcrab
                ;;
            4)
                show_distro_guide
                ;;
            5)
                do_restore_accela
                ;;
            6)
                do_uninstall
                ;;
            7)
                run_diagnostics
                ;;
            8|q|Q)
                echo "Exiting..."
                exit 0
                ;;
            *)
                echo -e "${RED}Invalid option!${NC}"
                sleep 1
                ;;
        esac
    done
}

# ------------------------------------------------------------------------------
#  10. CLI Unattended Argument Handler
# ------------------------------------------------------------------------------
main() {
    detect_distro
    check_fuse_status
    check_headcrab_status
    get_local_version

    ACTION="interactive"
    for arg in "$@"; do
        case "$arg" in
            --canary)
                INSTALL_CHANNEL="canary"
                ;;
            --beta)
                INSTALL_CHANNEL="beta"
                ;;
            --stable)
                INSTALL_CHANNEL="stable"
                ;;
            --install|-i)
                ACTION="install"
                ;;
            --update|-u)
                ACTION="update"
                ;;
            --headcrab)
                ACTION="headcrab"
                ;;
            --restore)
                ACTION="restore"
                ;;
            --uninstall)
                ACTION="uninstall"
                ;;
            --help|-h)
                ACTION="help"
                ;;
            *)
                echo "Unknown option: $arg"
                echo "Use ./install.sh --help for usage instructions."
                exit 1
                ;;
        esac
    done

    case "$ACTION" in
        interactive)
            if [ -t 0 ]; then
                interactive_menu
            else
                do_install
            fi
            ;;
        install)
            do_install
            ;;
        update)
            get_latest_github_version
            if [ "$LOCAL_VER" != "$LATEST_VER" ]; then
                do_install
            else
                echo -e "${GREEN}ASSella is already up to date ($LOCAL_VER).${NC}"
            fi
            ;;
        headcrab)
            install_headcrab
            ;;
        restore)
            do_restore_accela
            ;;
        uninstall)
            do_uninstall
            ;;
        help)
            echo "ASSella Installer & Management Suite"
            echo "Usage: ./install.sh [OPTION] [CHANNEL]"
            echo ""
            echo "Options:"
            echo "  --install, -i    Install or force update ASSella"
            echo "  --update, -u     Check and update if a new release exists"
            echo "  --headcrab       Install Headcrab (SLSsteam) daemon"
            echo "  --restore        Restore original ACCELA backup"
            echo "  --uninstall      Uninstall ASSella"
            echo "  --help, -h       Display this help message"
            echo ""
            echo "Environment:"
            echo "  ASSELLA_REQUIRE_SHA256=1  Fail if a release publishes no .sha256"
            echo "  ASSELLA_NO_VERIFY=1       Install even when the checksum mismatches"
            echo ""
            echo "Channels:"
            echo "  --stable         Target Stable releases (default if no branch keyword in tag)"
            echo "  --beta           Target Beta / Dev / RC releases"
            echo "  --canary         Target Canary / Testing releases"
            ;;
    esac
}

main "$@"
