#!/usr/bin/env bash
set -euo pipefail

# Configuration
WORKDIR="/tmp/assella_repack_dev"
SRC_DIR="/home/deck/Projects/ASSella"
ACCELA_DIR="/home/deck/.local/share/ACCELA"
BACKUP_APPIMAGE="$ACCELA_DIR/ASSella.AppImage"
OUTPUT_APPIMAGE="$ACCELA_DIR/ASSella.AppImage.dev"
OFFSET=193728

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}=== Starting Development ASSella AppImage Builder ===${NC}"

# Parse version from src/res/version
if [ ! -f "$SRC_DIR/src/res/version" ]; then
    echo -e "${RED}Error: version file not found at $SRC_DIR/src/res/version${NC}"
    exit 1
fi

VERSION_STR=$(cat "$SRC_DIR/src/res/version" | tr -d '\r\n')
echo -e "${GREEN}Detected version: $VERSION_STR (Dev)${NC}"

# Clean up build dir
echo -e "${YELLOW}=== Extracting base AppImage squashfs ===${NC}"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
unsquashfs -dest "$WORKDIR/squashfs-root" -offset "$OFFSET" "$BACKUP_APPIMAGE"

echo -e "${YELLOW}=== Syncing current source code into squashfs-root ===${NC}"
rsync -a --delete "$SRC_DIR/src/" "$WORKDIR/squashfs-root/bin/src/"

echo -e "${YELLOW}=== Updating AppImage root brand assets ===${NC}"
cp "$SRC_DIR/src/res/logo/icon.png" "$WORKDIR/squashfs-root/accela.png"

# Write version string to squashfs-root version file
echo "${VERSION_STR}-dev" > "$WORKDIR/squashfs-root/bin/src/res/version"

echo -e "${YELLOW}=== Building Dev AppImage with appimagetool ===${NC}"
APPIMAGETOOL="/home/deck/bin/appimagetool"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo -e "${RED}Error: appimagetool not found at $APPIMAGETOOL${NC}"
    exit 1
fi

export ARCH=x86_64
"$APPIMAGETOOL" "$WORKDIR/squashfs-root" "$WORKDIR/ASSella.AppImage.dev"

echo -e "${YELLOW}=== Verifying built Dev AppImage runs offscreen ===${NC}"
set +e
QT_QPA_PLATFORM=offscreen timeout 5s "$WORKDIR/ASSella.AppImage.dev" > /tmp/assella_dev_test.log 2>&1
TEST_EXIT=$?
set -e

if [ $TEST_EXIT -ne 124 ]; then
    echo -e "${RED}Error: Dev AppImage test run failed with code $TEST_EXIT.${NC}"
    cat /tmp/assella_dev_test.log
    exit 1
fi
echo -e "${GREEN}Verification successful! Dev AppImage launched successfully.${NC}"

echo -e "${YELLOW}=== Installing Dev AppImage locally ===${NC}"
rm -f "$OUTPUT_APPIMAGE"
cp "$WORKDIR/ASSella.AppImage.dev" "$OUTPUT_APPIMAGE"
echo -e "${GREEN}Installed locally at: $OUTPUT_APPIMAGE${NC}"
echo -e "${GREEN}=== Dev build completed successfully! ===${NC}"
