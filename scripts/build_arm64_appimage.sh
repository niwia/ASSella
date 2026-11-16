#!/usr/bin/env bash
# ==============================================================================
# build_arm64_appimage.sh — Standalone ARM64 AppImage Packaging for Armada OS
# ==============================================================================
# Assembles a 100% self-contained ARM64 AppImage designed for Fedora bootc,
# Gamescope (Game Mode), and KDE Plasma (Desktop Mode) handhelds.
#
# Key Features:
#   1. Bundled .NET 9 ARM64 Runtime: Zero host ~/.dotnet dependencies.
#   2. Wayland & Gamescope Native: QT_QPA_PLATFORM="wayland;xcb" in AppRun.
#   3. FUSE3 / bootc Resilient: Static runtime support & APPIMAGE_EXTRACT_AND_RUN.
#   4. Bundled Python 3.13 aarch64 runtime from python-build-standalone.
#   5. Native aarch64 wireproxy & appimageupdatetool.
# ==============================================================================

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BOLD}${BLUE}=== ASSella ARM64 AppImage Builder for Armada OS ===${NC}"

# 1. Architecture Check
HOST_ARCH=$(uname -m)
FORCE=0
for arg in "$@"; do
    if [[ "$arg" == "--force" || "$arg" == "-f" ]]; then
        FORCE=1
    fi
done

if [[ "$HOST_ARCH" != "aarch64" && "$HOST_ARCH" != "arm64" && "$FORCE" -eq 0 ]]; then
    echo -e "${YELLOW}Warning: Current host architecture is '$HOST_ARCH', not ARM64 (aarch64).${NC}"
    echo "To cross-pack or build under QEMU binfmt emulation, pass '--force'."
    exit 1
fi

BUILD_DIR="$SCRIPT_DIR/build_arm64"
APPDIR="$BUILD_DIR/AppDir"
OUTPUT_APPIMAGE="$SCRIPT_DIR/ASSella-arm64.AppImage"

rm -rf "$BUILD_DIR"
mkdir -p "$APPDIR/bin"

# 2. Desktop Entry & Icons Setup
echo -e "\n${BLUE}[1/7] Setting up desktop entry and icons...${NC}"
cat << 'EOF' > "$APPDIR/assella.desktop"
[Desktop Entry]
Name=ASSella
Comment=Steam Game & Depot Management Suite (ARM64)
Exec=AppRun %U
Icon=assella
Terminal=false
Type=Application
Categories=Utility;Game;
StartupWMClass=ASSella
EOF
cp "$APPDIR/assella.desktop" "$APPDIR/ACCELA.desktop"

mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

if [[ -f "src/res/logo/icon.svg" ]]; then
    cp "src/res/logo/icon.svg" "$APPDIR/assella.svg"
    cp "src/res/logo/icon.svg" "$APPDIR/accela.svg"
    cp "src/res/logo/icon.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/assella.svg"
fi
if [[ -f "src/res/logo/icon.png" ]]; then
    cp "src/res/logo/icon.png" "$APPDIR/assella.png"
    cp "src/res/logo/icon.png" "$APPDIR/.DirIcon"
    cp "src/res/logo/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/assella.png"
fi

# 3. AppRun (Gamescope Wayland, Sandbox, Bundled .NET)
echo -e "\n${BLUE}[2/7] Generating launcher entrypoints...${NC}"
cat << 'EOF' > "$APPDIR/AppRun"
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
cd "$HERE/bin"

# 1. Wayland & Gamescope compatibility (prefer Wayland in Game Mode, fallback to XCB in Desktop Mode)
export QT_QPA_PLATFORM="wayland;xcb"

# 2. QtWebEngine / Chromium sandbox guard for FUSE mounts
export QTWEBENGINE_DISABLE_SANDBOX=1

# 3. Prioritise bundled self-contained .NET 9 ARM64 runtime
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
if [ -d "$HERE/bin/.dotnet" ]; then
    export DOTNET_ROOT="$HERE/bin/.dotnet"
    export PATH="$HERE/bin/.dotnet:${PATH}"
elif [ -d "${HOME}/.dotnet" ]; then
    export DOTNET_ROOT="${HOME}/.dotnet"
    export PATH="${HOME}/.dotnet:${PATH}"
fi

export APPIMAGE=1
exec "./run.sh" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat << 'EOF' > "$APPDIR/bin/run.sh"
#!/usr/bin/env bash
cd "$(dirname "$(realpath "$0")")"
export APPIMAGE=1
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"

PYTHON_EXEC=".venv/bin/python3"
if [ -f "$PYTHON_EXEC" ]; then
    exec "$PYTHON_EXEC" src/main.py "$@"
else
    exec python3 src/main.py "$@"
fi
EOF
chmod +x "$APPDIR/bin/run.sh"

# 4. Copy Codebase & Stamp Version
echo -e "\n${BLUE}[3/7] Copying source code and assets...${NC}"
cp -r src "$APPDIR/bin/"
find "$APPDIR/bin/src" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Strip optional dynamically fetched binaries from AppDir to keep it clean
rm -rf "$APPDIR/bin/src/deps/Goldberg" "$APPDIR/bin/src/deps/Steamless" "$APPDIR/bin/src/deps/SLScheevo" "$APPDIR/bin/src/deps/schema-grabber"

BUILD_DATE_UTC="$(date -u +%Y-%m-%d)"
BUILD_TS_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 -c 'import re, sys; p = sys.argv[1]; t = open(p, "r", encoding="utf-8").read(); t = re.sub(r"^BUILD_DATE\s*=.*$", f"BUILD_DATE = \"{sys.argv[2]}\"", t, flags=re.M); t = re.sub(r"^BUILD_TIMESTAMP\s*=.*$", f"BUILD_TIMESTAMP = \"{sys.argv[3]}\"", t, flags=re.M); open(p, "w", encoding="utf-8").write(t); print(f"Stamped {p}: {sys.argv[2]}")' "$APPDIR/bin/src/utils/version.py" "$BUILD_DATE_UTC" "$BUILD_TS_UTC"

# 5. Bundle Microsoft .NET 9 ARM64 Runtime (Zero Host Dependencies)
echo -e "\n${BLUE}[4/7] Bundling self-contained .NET 9 ARM64 Runtime...${NC}"
DOTNET_ARM64_TAR="/tmp/dotnet-runtime-9.0-arm64.tar.gz"
if [ ! -f "$DOTNET_ARM64_TAR" ]; then
    echo "Downloading official Microsoft .NET 9 runtime (linux-arm64)..."
    curl -fsSL --retry 3 "https://dotnetcli.azureedge.net/dotnet/Runtime/9.0.0/dotnet-runtime-9.0.0-linux-arm64.tar.gz" -o "$DOTNET_ARM64_TAR" || \
    curl -fsSL --retry 3 "https://builds.dotnet.microsoft.com/dotnet/Runtime/9.0.0/dotnet-runtime-9.0.0-linux-arm64.tar.gz" -o "$DOTNET_ARM64_TAR"
fi

mkdir -p "$APPDIR/bin/.dotnet"
tar -xzf "$DOTNET_ARM64_TAR" -C "$APPDIR/bin/.dotnet"
chmod +x "$APPDIR/bin/.dotnet/dotnet"
echo -e "${GREEN}✓ Bundled .NET 9 ARM64 runtime.${NC}"

# 6. Fetch Native ARM64 Binaries (wireproxy & appimageupdatetool)
echo -e "\n${BLUE}[5/7] Bundling native ARM64 support binaries...${NC}"

# ARM64 wireproxy for Kuroko stealth proxy
echo "Downloading native ARM64 wireproxy..."
WP_TMP=$(mktemp -d)
if curl -fsSL --retry 3 "https://github.com/windtf/wireproxy/releases/download/v0.3.2/wireproxy_linux_arm64.tar.gz" -o "$WP_TMP/wireproxy.tar.gz" 2>/dev/null; then
    tar -xzf "$WP_TMP/wireproxy.tar.gz" -C "$WP_TMP" 2>/dev/null || true
    if [[ -f "$WP_TMP/wireproxy" ]]; then
        cp -f "$WP_TMP/wireproxy" "$APPDIR/bin/src/deps/wireproxy"
        chmod +x "$APPDIR/bin/src/deps/wireproxy"
        echo "✓ Bundled native ARM64 wireproxy."
    fi
fi
rm -rf "$WP_TMP"

# ARM64 appimageupdatetool
if [ ! -f "$APPDIR/bin/appimageupdatetool" ]; then
    echo "Downloading appimageupdatetool for aarch64..."
    if curl -fsSL --retry 3 -o /tmp/appimageupdatetool-arm64 "https://github.com/AppImageCommunity/AppImageUpdate/releases/download/continuous/appimageupdatetool-aarch64.AppImage" 2>/dev/null; then
        chmod +x /tmp/appimageupdatetool-arm64
        if /tmp/appimageupdatetool-arm64 --appimage-extract >/dev/null 2>&1; then
            mkdir -p "$APPDIR/bin/appimageupdatetool-root/usr"
            cp -r squashfs-root/usr/bin "$APPDIR/bin/appimageupdatetool-root/usr/bin" 2>/dev/null || true
            cp -r squashfs-root/usr/lib "$APPDIR/bin/appimageupdatetool-root/usr/lib" 2>/dev/null || true
            rm -rf squashfs-root
            printf '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/appimageupdatetool-root/usr/bin/appimageupdatetool" "$@"\n' > "$APPDIR/bin/appimageupdatetool"
            chmod +x "$APPDIR/bin/appimageupdatetool"
            echo "✓ Bundled aarch64 appimageupdatetool."
        fi
        rm -f /tmp/appimageupdatetool-arm64
    fi
fi

# 7. Standalone Python 3.13 Runtime for aarch64
echo -e "\n${BLUE}[6/7] Setting up standalone Python 3.13 runtime (aarch64)...${NC}"
PYTHON_TAR="/tmp/cpython-3.13-arm64.tar.gz"
if [ ! -f "$PYTHON_TAR" ]; then
    echo "Downloading standalone CPython 3.13 for aarch64-unknown-linux-gnu..."
    curl -fsSL --retry 3 "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.13.18+20261026-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz" -o "$PYTHON_TAR"
fi

tar -xzf "$PYTHON_TAR" -C "$APPDIR/bin/"
mv "$APPDIR/bin/python" "$APPDIR/bin/.venv"

# If host is native arm64, install pip dependencies into the bundled runtime
if [[ "$HOST_ARCH" == "aarch64" || "$HOST_ARCH" == "arm64" ]]; then
    echo "Installing application dependencies into bundled ARM64 runtime..."
    "$APPDIR/bin/.venv/bin/python3" -m pip install --upgrade pip setuptools wheel --quiet
    "$APPDIR/bin/.venv/bin/python3" -m pip install -r requirements.txt --quiet
    find "$APPDIR/bin/.venv" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
else
    echo "Cross-packaging note: Host is $HOST_ARCH. Pip wheel installation requires ARM64 host or QEMU user emulation."
fi

# 8. Package AppImage via appimagetool
echo -e "\n${BLUE}[7/7] Packaging AppImage via appimagetool...${NC}"

APPIMAGETOOL="appimagetool"
if ! command -v "$APPIMAGETOOL" &>/dev/null; then
    if [[ -x "/usr/local/bin/appimagetool" ]]; then
        APPIMAGETOOL="/usr/local/bin/appimagetool"
    elif [[ -x "/tmp/appimagetool" ]]; then
        APPIMAGETOOL="/tmp/appimagetool"
    else
        echo "Downloading appimagetool..."
        TOOL_ARCH="$HOST_ARCH"
        [[ "$TOOL_ARCH" == "arm64" ]] && TOOL_ARCH="aarch64"
        curl -fsSL --retry 3 "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${TOOL_ARCH}.AppImage" -o /tmp/appimagetool
        chmod +x /tmp/appimagetool
        APPIMAGETOOL="/tmp/appimagetool"
    fi
fi

# Use static runtime if available for FUSE3 compatibility on Fedora bootc
STATIC_RUNTIME="/tmp/runtime-aarch64"
RUNTIME_FLAG=()
if [ ! -f "$STATIC_RUNTIME" ]; then
    curl -fsSL --retry 3 "https://github.com/AppImageCommunity/type2-runtime/releases/download/continuous/runtime-aarch64" -o "$STATIC_RUNTIME" 2>/dev/null || true
fi
if [ -f "$STATIC_RUNTIME" ]; then
    RUNTIME_FLAG=("--runtime-file" "$STATIC_RUNTIME")
    echo "✓ Using static FUSE3-compatible aarch64 runtime."
fi

UPDATE_INFO="gh-releases-zsync|niwia|ASSella|latest-pre|ASSella-arm64.AppImage.zsync"
ARCH=aarch64 "$APPIMAGETOOL" -n -u "$UPDATE_INFO" "${RUNTIME_FLAG[@]}" "$APPDIR" "$OUTPUT_APPIMAGE"

sha256sum "$OUTPUT_APPIMAGE" > "${OUTPUT_APPIMAGE}.sha256"

echo -e "\n${BOLD}${GREEN}=== Build Complete! ===${NC}"
echo -e "AppImage: ${BOLD}$OUTPUT_APPIMAGE${NC}"
echo -e "Checksum: ${BOLD}${OUTPUT_APPIMAGE}.sha256${NC}"
echo -e "\n${BOLD}Smoke-testing on Armada OS / Fedora bootc container:${NC}"
echo "  podman run --rm -v \$(pwd):/app -w /app --platform linux/arm64 fedora:41 bash -c \"APPIMAGE_EXTRACT_AND_RUN=1 ./ASSella-arm64.AppImage --headless --check-updates\""
