#!/usr/bin/env bash
# ==============================================================================
# install-armada.sh — Native ARM64 / Armada OS Installer for ASSella
# ==============================================================================
# Installs ASSella in user-space on ARM64 Linux distributions (Armada OS, etc.)
# without requiring root privileges or modifying immutable ostree/bootc rootfs.
# ==============================================================================

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BOLD}${BLUE}=== ASSella ARM64 / Armada OS Installer ===${NC}"

# Parse command line flags
FORCE=0
UPDATE_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --force|-f)
            FORCE=1
            ;;
        --update|-u)
            UPDATE_ONLY=1
            ;;
    esac
done

# 1. Architecture Check
ARCH=$(uname -m)
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" && "$FORCE" -eq 0 ]]; then
    echo -e "${YELLOW}Warning: Detected architecture '$ARCH' is not ARM64 (aarch64).${NC}"
    echo "If you are testing or running under an emulator, pass '--force' to proceed."
    exit 1
fi

# 2. Prerequisites
echo -e "\n${BLUE}[1/5] Checking system dependencies...${NC}"
MISSING_DEPS=()
for cmd in python3 curl git; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING_DEPS+=("$cmd")
    fi
done

if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
    echo -e "${RED}Error: The following required commands are missing: ${MISSING_DEPS[*]}${NC}"
    echo "Please install them via your distribution package manager before running this installer."
    exit 1
fi
echo -e "${GREEN}✓ Core tools (python3, curl, git) present.${NC}"

# 3. User Directories Setup
INSTALL_DIR="${HOME}/.local/share/ASSella"
BIN_DIR="${HOME}/.local/bin"
DOTNET_DIR="${HOME}/.dotnet"
APP_DIR="${HOME}/.local/share/applications"
ICON_DIR_SVG="${HOME}/.local/share/icons/hicolor/scalable/apps"
ICON_DIR_PNG="${HOME}/.local/share/icons/hicolor/256x256/apps"

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$APP_DIR" "$ICON_DIR_SVG" "$ICON_DIR_PNG"

# 4. Install / Verify .NET 9 Runtime for linux-arm64
echo -e "\n${BLUE}[2/5] Checking Microsoft .NET 9 Runtime (ARM64)...${NC}"
if [[ -x "$DOTNET_DIR/dotnet" ]] && "$DOTNET_DIR/dotnet" --list-runtimes 2>/dev/null | grep -q "Microsoft.NETCore.App 9"; then
    echo -e "${GREEN}✓ .NET 9 runtime already installed in $DOTNET_DIR.${NC}"
else
    echo "Downloading and installing official .NET 9 runtime for linux-arm64..."
    DOTNET_SCRIPT=$(mktemp)
    curl -fsSL https://dot.net/v1/dotnet-install.sh -o "$DOTNET_SCRIPT"
    chmod +x "$DOTNET_SCRIPT"
    bash "$DOTNET_SCRIPT" --runtime dotnet --channel 9.0 --architecture arm64 --install-dir "$DOTNET_DIR"
    rm -f "$DOTNET_SCRIPT"
    echo -e "${GREEN}✓ .NET 9 runtime (ARM64) installed successfully.${NC}"
fi

# 5. Clone / Sync ASSella Source Repository (arm64 branch)
echo -e "\n${BLUE}[3/5] Setting up ASSella (arm64 branch)...${NC}"
REPO_URL="https://github.com/niwia/ASSella.git"
BRANCH="arm64"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "Updating existing installation in $INSTALL_DIR..."
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull origin "$BRANCH"
else
    # If the installer is being run from inside an existing cloned repo, copy over
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [[ -f "$SCRIPT_DIR/src/main.py" ]]; then
        echo "Copying local repository files to $INSTALL_DIR..."
        cp -rf "$SCRIPT_DIR"/* "$INSTALL_DIR"/
    else
        echo "Cloning ASSella from $REPO_URL ($BRANCH branch)..."
        git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
fi
echo -e "${GREEN}✓ ASSella files ready at $INSTALL_DIR.${NC}"

# 6. Python Virtual Environment & Dependencies
echo -e "\n${BLUE}[4/5] Setting up Python virtual environment...${NC}"
VENV_DIR="$INSTALL_DIR/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip --quiet
echo "Installing Python dependencies (PyQt6, requests, etc.)..."
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
echo -e "${GREEN}✓ Python virtual environment configured.${NC}"

# 6b. Kuroko Stealth Proxy (ARM64 wireproxy binary)
if [[ ! -x "$INSTALL_DIR/src/deps/wireproxy" ]] || file -b "$INSTALL_DIR/src/deps/wireproxy" 2>/dev/null | grep -q "x86-64"; then
    echo "Configuring native ARM64 wireproxy binary for Kuroko stealth proxy..."
    WP_TMP=$(mktemp -d)
    if curl -fsSL --retry 3 "https://github.com/windtf/wireproxy/releases/download/v0.3.2/wireproxy_linux_arm64.tar.gz" -o "$WP_TMP/wireproxy.tar.gz" 2>/dev/null; then
        tar -xzf "$WP_TMP/wireproxy.tar.gz" -C "$WP_TMP" 2>/dev/null || true
        if [[ -f "$WP_TMP/wireproxy" ]]; then
            mkdir -p "$INSTALL_DIR/src/deps"
            cp -f "$WP_TMP/wireproxy" "$INSTALL_DIR/src/deps/wireproxy"
            chmod +x "$INSTALL_DIR/src/deps/wireproxy"
            echo -e "${GREEN}✓ Native ARM64 wireproxy installed.${NC}"
        fi
    fi
    rm -rf "$WP_TMP"
fi

# 7. Create Launcher Script, Desktop Entry & Icons
echo -e "\n${BLUE}[5/5] Creating launcher, desktop shortcut and icons...${NC}"

# Install Icons to standard user icon theme paths
if [[ -f "$INSTALL_DIR/src/res/logo/icon.svg" ]]; then
    cp -f "$INSTALL_DIR/src/res/logo/icon.svg" "$ICON_DIR_SVG/assella.svg"
fi
if [[ -f "$INSTALL_DIR/src/res/logo/icon.png" ]]; then
    cp -f "$INSTALL_DIR/src/res/logo/icon.png" "$ICON_DIR_PNG/assella.png"
fi

# Launcher Script
LAUNCHER="$BIN_DIR/assella"
cat << 'EOF' > "$LAUNCHER"
#!/usr/bin/env bash
# ASSella ARM64 Launcher
export DOTNET_ROOT="${HOME}/.dotnet"
export PATH="${HOME}/.dotnet:${HOME}/.local/bin:${PATH}"
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
INSTALL_DIR="${HOME}/.local/share/ASSella"

cd "$INSTALL_DIR" || exit 1
exec "$INSTALL_DIR/.venv/bin/python3" "$INSTALL_DIR/src/main.py" "$@"
EOF
chmod +x "$LAUNCHER"

# Desktop Entry
ICON_PATH="$INSTALL_DIR/src/res/logo/icon.png"
DESKTOP_FILE="$APP_DIR/assella.desktop"

desktop_tmp=$(mktemp "${APP_DIR}/.assella.desktop.tmp.XXXXXX")
cat << EOF > "$desktop_tmp"
[Desktop Entry]
Name=ASSella
GenericName=Game Depot Downloader
Comment=Steam Game & Depot Management Suite (ARM64)
Exec=$LAUNCHER
Icon=assella
Terminal=false
Type=Application
Categories=Utility;Game;
StartupNotify=true
StartupWMClass=ASSella
EOF
chmod 0644 "$desktop_tmp"
rm -f "$DESKTOP_FILE"
mv -f "$desktop_tmp" "$DESKTOP_FILE"

echo -e "${GREEN}✓ Created launcher: $LAUNCHER${NC}"
echo -e "${GREEN}✓ Created desktop entry: $DESKTOP_FILE${NC}"

# Add ~/.local/bin to PATH in bashrc if not present
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
    if [[ -f "${HOME}/.bashrc" ]] && ! grep -q "export PATH=.*$BIN_DIR" "${HOME}/.bashrc"; then
        echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "${HOME}/.bashrc"
    fi
fi

echo -e "\n${BOLD}${GREEN}=== Installation Complete! ===${NC}"
echo -e "You can now launch ASSella using:"
echo -e "  • ${BOLD}Application Launcher${NC}: Click ASSella under Games / Utilities"
echo -e "  • ${BOLD}Terminal${NC}: Run '${BOLD}assella${NC}'"
echo -e "  • ${BOLD}Steam Game Mode (Armada OS / Gamescope)${NC}:"
echo -e "    1. Switch to Desktop Mode in Armada OS"
echo -e "    2. In Steam: Games -> 'Add a Non-Steam Game to My Library...'"
echo -e "    3. Select ASSella from the list and click 'Add Selected Programs'"
echo -e "    4. Return to Gaming Mode — ASSella is now accessible with controller support!"
