#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#                      ASSella Installer & Management Suite
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

detect_distro_family() {
    local family="unknown"
    if [ "$IS_STEAM_DECK" = true ]; then
        family="arch"
    elif [ "$DISTRO_ID" = "bazzite" ]; then
        family="bazzite"
    elif [[ "$DISTRO_ID" == "fedora" || "$DISTRO_ID" == "rhel" || "$DISTRO_ID" == "centos" || "$DISTRO_ID" == "nobara" || "$DISTRO_LIKE" =~ "fedora" || "$DISTRO_LIKE" =~ "rhel" ]]; then
        family="fedora"
    elif [[ "$DISTRO_ID" == "debian" || "$DISTRO_ID" == "ubuntu" || "$DISTRO_ID" == "linuxmint" || "$DISTRO_ID" == "pop" || "$DISTRO_LIKE" =~ "debian" || "$DISTRO_LIKE" =~ "ubuntu" ]]; then
        family="debian"
    elif [[ "$DISTRO_ID" == "arch" || "$DISTRO_ID" == "cachyos" || "$DISTRO_ID" == "manjaro" || "$DISTRO_ID" == "endeavouros" || "$DISTRO_LIKE" =~ "arch" ]]; then
        family="arch"
    elif [[ "$DISTRO_ID" =~ opensuse || "$DISTRO_ID" =~ suse || "$DISTRO_LIKE" =~ opensuse || "$DISTRO_LIKE" =~ suse ]]; then
        family="opensuse"
    elif [[ "$DISTRO_ID" == "void" ]]; then
        family="void"
    fi
    echo "$family"
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

    REL_JSON=$(curl -s "https://api.github.com/repos/niwia/ASSella/releases" || true)
    if [ -n "$REL_JSON" ]; then
        TAG_NAME=$(echo "$REL_JSON" | grep -o '"tag_name": *"[^"]*"' | head -n 1 | cut -d '"' -f 4 || true)
        if [ -n "$TAG_NAME" ]; then
            LATEST_VER="$TAG_NAME"
        fi

        DL_URL=$(echo "$REL_JSON" | grep -o '"browser_download_url": *"[^"]*ASSella\.AppImage"' | head -n 1 | cut -d '"' -f 4 || true)
        if [ -n "$DL_URL" ]; then
            LATEST_URL="$DL_URL"
        fi
    fi

    if [ -z "$LATEST_URL" ]; then
        LATEST_URL="https://github.com/niwia/ASSella/releases/download/v2.6.4/ASSella.AppImage"
    fi
}

# ------------------------------------------------------------------------------
#  2. Automated Dependency Resolution (Adapted from fix-deps)
# ------------------------------------------------------------------------------
install_dependencies() {
    local family
    family=$(detect_distro_family)
    echo -e "${YELLOW}[INFO] Checking system dependencies for ($family family)...${NC}"

    if [ "$family" = "bazzite" ] || [ "$IS_NIXOS" = true ]; then
        echo -e "${CYAN}[INFO] System is declarative/immutable ($DISTRO_NAME). Skipping automated package install.${NC}"
        return 0
    fi

    local SUDO=""
    if [ "$EUID" -ne 0 ]; then
        if command -v sudo &>/dev/null; then
            SUDO="sudo"
        fi
    fi

    case "$family" in
        arch)
            local arch_pkgs="fuse2 python xcb-util-cursor libnotify git lib32-glibc lib32-openssl lib32-curl curl p7zip"
            if [ -n "$SUDO" ]; then
                echo -e "${CYAN}[INFO] Ensuring dependencies are installed via pacman...${NC}"
                $SUDO pacman -Sy --needed --noconfirm $arch_pkgs 2>/dev/null || true
            fi
            ;;
        debian)
            if [ -n "$SUDO" ]; then
                if command -v dpkg &>/dev/null && ! dpkg --print-foreign-architectures 2>/dev/null | grep -q i386; then
                    $SUDO dpkg --add-architecture i386 2>/dev/null || true
                    $SUDO apt-get update -qq 2>/dev/null || true
                fi
                local deb_pkgs="libfuse2 python3 python3-venv libxcb-cursor0 libnotify-bin git p7zip-full libc6:i386 libcurl4:i386 libssl3:i386"
                echo -e "${CYAN}[INFO] Ensuring dependencies are installed via apt-get...${NC}"
                $SUDO apt-get install -y -qq $deb_pkgs 2>/dev/null || true
            fi
            ;;
        fedora)
            if [ -n "$SUDO" ]; then
                local fed_pkgs="fuse-libs python3 libxcb-cursor libnotify git p7zip p7zip-plugins libcurl.i686 openssl-libs.i686"
                echo -e "${CYAN}[INFO] Ensuring dependencies are installed via dnf...${NC}"
                $SUDO dnf install -y --setopt=install_weak_deps=False $fed_pkgs 2>/dev/null || true
            fi
            ;;
        opensuse)
            if [ -n "$SUDO" ]; then
                local suse_pkgs="libfuse2 python3 libxcb-cursor0 libnotify-tools git p7zip-full glibc-32bit libcurl4-32bit libopenssl3-32bit"
                echo -e "${CYAN}[INFO] Ensuring dependencies are installed via zypper...${NC}"
                $SUDO zypper --non-interactive install -y $suse_pkgs 2>/dev/null || true
            fi
            ;;
        void)
            if [ -n "$SUDO" ]; then
                echo -e "${CYAN}[INFO] Ensuring dependencies are installed via xbps-install...${NC}"
                $SUDO xbps-install -y fuse python3 xcb-util-cursor libnotify git p7zip 2>/dev/null || true
            fi
            ;;
        *)
            echo -e "${YELLOW}[WARN] Unknown distro family. Please verify libfuse2 and 32-bit libraries are installed.${NC}"
            ;;
    esac
}

# ------------------------------------------------------------------------------
#  3. Header & Status Display
# ------------------------------------------------------------------------------
show_header() {
    clear
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
    echo -e "${GREEN}${BOLD}                 ASSella Installer & Management Suite             ${NC}"
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
    echo -e "  ${BOLD}OS Detected:${NC}      $DISTRO_NAME ($(uname -m))"
    if [ "$IS_STEAM_DECK" = true ]; then
        echo -e "  ${BOLD}Device:${NC}           Steam Deck (SteamOS)"
    fi

    # Headcrab Status
    if [ "$HEADCRAB_INSTALLED" = true ]; then
        echo -e "  ${BOLD}Headcrab (SLS):${NC}   ${GREEN}Installed (~/.config/SLSsteam)${NC}"
    else
        echo -e "  ${BOLD}Headcrab (SLS):${NC}   ${YELLOW}Not Detected${NC}"
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
#  4. Distro-Specific Requirements Guide
# ------------------------------------------------------------------------------
pause_if_interactive() {
    if [ "${INTERACTIVE:-false}" = true ]; then
        read -p "Press Enter to continue..." dummy
    fi
}

show_distro_guide() {
    show_header
    echo -e "\n${YELLOW}${BOLD}=== Distro-Specific Setup & Requirements ===${NC}\n"

    if [ "$IS_NIXOS" = true ]; then
        echo -e "${CYAN}${BOLD}NixOS Installation Guide:${NC}"
        echo -e "NixOS does not use standard /lib64 glibc linkers out of the box."
        echo -e "ASSella automatically generates a launcher using ${GREEN}steam-run${NC} or ${GREEN}appimage-run${NC}.\n"
        echo -e "  ${BOLD}Command to launch directly:${NC}"
        echo -e "    ${GREEN}nix-shell -p steam-run --run 'steam-run ~/.local/share/ACCELA/ASSella.AppImage'${NC}\n"
        echo -e "  ${BOLD}Global fix (Optional):${NC} Add ${GREEN}programs.nix-ld.enable = true;${NC} in /etc/nixos/configuration.nix"
    elif [ "$IS_CACHYOS" = true ] || [[ "$DISTRO_ID" == "arch" ]] || [[ "$DISTRO_LIKE" == *"arch"* ]]; then
        echo -e "${CYAN}${BOLD}CachyOS / Arch Linux Guide:${NC}"
        echo -e "Arch-based distros require ${GREEN}fuse2${NC} to execute AppImages.\n"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo pacman -S fuse2${NC}"
    elif [[ "$DISTRO_ID" == "ubuntu" ]] || [[ "$DISTRO_ID" == "debian" ]] || [[ "$DISTRO_LIKE" == *"ubuntu"* ]]; then
        echo -e "${CYAN}${BOLD}Ubuntu / Debian / Mint Guide:${NC}"
        echo -e "Ubuntu 22.04+ requires ${GREEN}libfuse2${NC} for AppImages.\n"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo apt install -y libfuse2${NC}"
    elif [[ "$DISTRO_ID" == "fedora" ]] || [[ "$DISTRO_LIKE" == *"fedora"* ]]; then
        echo -e "${CYAN}${BOLD}Fedora / RHEL Guide:${NC}"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo dnf install -y fuse-libs${NC}"
    elif [[ "$DISTRO_ID" == *"suse"* ]]; then
        echo -e "${CYAN}${BOLD}openSUSE Guide:${NC}"
        echo -e "  ${BOLD}Install command:${NC}"
        echo -e "    ${GREEN}sudo zypper install libfuse2${NC}"
    else
        echo -e "${CYAN}${BOLD}Generic Linux Guide:${NC}"
        echo -e "Ensure ${GREEN}libfuse2${NC} or ${GREEN}fuse2${NC} package is installed on your system."
    fi

    echo -e "\n${CYAN}${BOLD}-------------------------------------------------------------------${NC}\n"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  5. Main Installation & Update Engine
# ------------------------------------------------------------------------------
do_install() {
    echo -e "\n${YELLOW}[INFO] Installing / Updating ASSella...${NC}"
    mkdir -p "$INSTALL_DESTINATION"

    # Backup existing ACCELA.AppImage if present
    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage" ] && [ ! -L "$INSTALL_DESTINATION/ACCELA.AppImage" ]; then
        echo -e "${YELLOW}[INFO] Backing up existing ACCELA.AppImage to ACCELA.AppImage.bak...${NC}"
        mv -f "$INSTALL_DESTINATION/ACCELA.AppImage" "$INSTALL_DESTINATION/ACCELA.AppImage.bak"
    fi

    # Fetch AppImage binary
    get_latest_github_version
    echo -e "${YELLOW}[INFO] Downloading ASSella.AppImage ($LATEST_VER)...${NC}"
    if ! curl -fL -o "$INSTALL_DESTINATION/ASSella.AppImage" "$LATEST_URL"; then
        echo -e "${RED}[ERROR] Download failed! Please check your connection to GitHub.${NC}"
        pause_if_interactive
        return 1
    fi

    chmod +x "$INSTALL_DESTINATION/ASSella.AppImage"

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
Icon=accela
Terminal=false
Type=Application
Categories=Utility;Game;
MimeType=x-scheme-handler/accela;
EOL

    # Application Icon Download
    echo -e "${YELLOW}[INFO] Applying application icon...${NC}"
    mkdir -p "$(dirname "$ICON_PATH")"
    curl -sL -o "$ICON_PATH" "https://raw.githubusercontent.com/niwia/ASSella/main/src/res/logo/icon.png" || true

    # Update system desktop database
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$(dirname "$DESKTOP_ENTRY")" 2>/dev/null || true
    fi
    if [ -z "${XDG_CURRENT_DESKTOP:-}" ] || [[ "$XDG_CURRENT_DESKTOP" != *"KDE"* ]]; then
        command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    fi

    # Check FUSE
    check_fuse_status
    if [ "$FUSE_INSTALLED" = false ] && [ "$IS_NIXOS" = false ]; then
        echo -e "\n${RED}${BOLD}[WARN] FUSE (libfuse.so.2) is not installed on your system!${NC}"
        if [ "$IS_CACHYOS" = true ] || [[ "$DISTRO_ID" == "arch" ]]; then
            echo -e "${YELLOW}Please run: sudo pacman -S fuse2${NC}"
        elif [[ "$DISTRO_ID" == "ubuntu" ]] || [[ "$DISTRO_ID" == "debian" ]]; then
            echo -e "${YELLOW}Please run: sudo apt install libfuse2${NC}"
        elif [[ "$DISTRO_ID" == "fedora" ]]; then
            echo -e "${YELLOW}Please run: sudo dnf install fuse-libs${NC}"
        fi
    fi

    echo -e "\n${GREEN}${BOLD}=========================================${NC}"
    echo -e "${GREEN}${BOLD}[OK] ASSella has been installed & patched!${NC}"
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
        echo -e "${YELLOW}[WARN] No ACCELA.AppImage.bak found to restore.${NC}"
    fi

    if [ -f "$DESKTOP_ENTRY" ]; then
        sed -i 's/^Name=ASSella$/Name=ACCELA/' "$DESKTOP_ENTRY"
        echo -e "${GREEN}[INFO] Restored desktop entry name to ACCELA.${NC}"
    fi

    echo -e "\n${GREEN}[OK] ACCELA restoration complete!${NC}"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  7. Clean Uninstall ASSella
# ------------------------------------------------------------------------------
do_uninstall() {
    echo -e "\n${RED}${BOLD}=== ASSella Clean Uninstaller ===${NC}\n"
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

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage.bak" ]; then
        mv "$INSTALL_DESTINATION/ACCELA.AppImage.bak" "$INSTALL_DESTINATION/ACCELA.AppImage"
        echo -e "${GREEN}[INFO] Restored original ACCELA.AppImage backup.${NC}"
    fi

    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$(dirname "$DESKTOP_ENTRY")" 2>/dev/null || true
    fi

    echo -e "\n${GREEN}[OK] ASSella has been uninstalled.${NC}"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  8. Pre-Flight Diagnostics
# ------------------------------------------------------------------------------
run_diagnostics() {
    show_header
    echo -e "\n${YELLOW}${BOLD}=== ASSella Pre-Flight Diagnostics ===${NC}\n"

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
        echo -e "${YELLOW}NOT INSTALLED${NC}"
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
#  9. Interactive Main Menu Loop (Optional)
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
        echo -e "  ${BOLD}[1]${NC} Install / Update ASSella"
        echo -e "  ${BOLD}[2]${NC} Check for Updates & View Release Info"
        echo -e "  ${BOLD}[3]${NC} View Distro Requirements Guide"
        echo -e "  ${BOLD}[4]${NC} Restore Original ACCELA (Revert Backup)"
        echo -e "  ${BOLD}[5]${NC} Uninstall ASSella (Clean Files & Shortcuts)"
        echo -e "  ${BOLD}[6]${NC} Run Pre-Flight Diagnostics"
        echo -e "  ${BOLD}[7]${NC} Exit"
        echo -e "${CYAN}${BOLD}===================================================================${NC}"
        read -p "Select option [1-7]: " choice

        case "$choice" in
            1)
                install_dependencies
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
                show_distro_guide
                ;;
            4)
                do_restore_accela
                ;;
            5)
                do_uninstall
                ;;
            6)
                run_diagnostics
                ;;
            7|q|Q)
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
#  10. CLI Execution Entry Point
# ------------------------------------------------------------------------------
main() {
    detect_distro
    check_fuse_status
    check_headcrab_status
    get_local_version

    if [ $# -eq 0 ]; then
        install_dependencies
        do_install
        exit 0
    fi

    case "$1" in
        --install|-i)
            install_dependencies
            do_install
            ;;
        --deps)
            install_dependencies
            ;;
        --menu|-m)
            interactive_menu
            ;;
        --update|-u)
            get_latest_github_version
            if [ "$LOCAL_VER" != "$LATEST_VER" ]; then
                install_dependencies
                do_install
            else
                echo -e "${GREEN}ASSella is already up to date ($LOCAL_VER).${NC}"
            fi
            ;;
        --restore)
            do_restore_accela
            ;;
        --uninstall)
            do_uninstall
            ;;
        --help|-h)
            echo "ASSella Installer & Management Suite"
            echo "Usage: ./install.sh [OPTION]"
            echo ""
            echo "Options:"
            echo "  (no args)        Install dependencies and install/update ASSella automatically"
            echo "  --install, -i    Install or force update ASSella with dependencies"
            echo "  --deps           Install system dependencies only"
            echo "  --menu, -m       Launch interactive maintenance menu"
            echo "  --update, -u     Check and update if a new release exists"
            echo "  --restore        Restore original ACCELA backup"
            echo "  --uninstall      Uninstall ASSella"
            echo "  --help, -h       Display this help message"
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use ./install.sh --help for usage instructions."
            exit 1
            ;;
    esac
}

main "$@"
