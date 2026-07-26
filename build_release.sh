#!/usr/bin/env bash
export PATH="/home/deck/bin:$PATH"
set -euo pipefail

# Configuration
WORKDIR="/tmp/assella_repack"
SRC_DIR="/home/deck/Projects/ASSella"
ACCELA_DIR="/home/deck/.local/share/ACCELA"
BACKUP_APPIMAGE="$ACCELA_DIR/ASSella.AppImage.dev"
OUTPUT_APPIMAGE="$ACCELA_DIR/ASSella.AppImage"
OFFSET=193728

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}=== Starting ASSella AppImage Builder & Releaser ===${NC}"

# Parse version from src/res/version
if [ ! -f "$SRC_DIR/src/res/version" ]; then
    echo -e "${RED}Error: version file not found at $SRC_DIR/src/res/version${NC}"
    exit 1
fi

VERSION_STR=$(cat "$SRC_DIR/src/res/version" | tr -d '\r\n')
# e.g., 20260603+ASSella-1.8f
# Extract the tag name (e.g. v1.8f)
if [[ "$VERSION_STR" =~ -([0-9]+\.[0-9]+(\.[0-9]+)?[a-z]?(-[a-zA-Z0-9]+)?)$ ]]; then
    TAG="v${BASH_REMATCH[1]}"
else
    echo -e "${RED}Error: version string format invalid: $VERSION_STR${NC}"
    exit 1
fi

echo -e "${GREEN}Detected version: $VERSION_STR (Tag: $TAG)${NC}"

# Ensure we have git tag matching
if ! git show-ref --tags --quiet "$TAG"; then
    echo -e "${YELLOW}Warning: Git tag $TAG does not exist locally. Creating it now...${NC}"
    git tag -a "$TAG" -m "ASSella $TAG release"
fi

# Clean up build dir
echo -e "${YELLOW}=== Extracting base AppImage squashfs ===${NC}"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
unsquashfs -dest "$WORKDIR/squashfs-root" -offset "$OFFSET" "$BACKUP_APPIMAGE"

echo -e "${YELLOW}=== Syncing current source code into squashfs-root ===${NC}"
rsync -a --delete "$SRC_DIR/src/" "$WORKDIR/squashfs-root/bin/src/"

# Write version string to squashfs-root version file (just to be absolutely sure)
echo "$VERSION_STR" > "$WORKDIR/squashfs-root/bin/src/res/version"

echo -e "${YELLOW}=== Building AppImage with appimagetool (ZSync enabled) ===${NC}"
APPIMAGETOOL="/home/deck/bin/appimagetool"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo -e "${RED}Error: appimagetool not found at $APPIMAGETOOL${NC}"
    exit 1
fi

# Clean up any pre-existing zsync file in working directory
rm -f "$SRC_DIR/ASSella.AppImage.zsync"

export ARCH=x86_64
"$APPIMAGETOOL" -u "gh-releases-zsync|niwia|ASSella|latest|ASSella.AppImage.zsync" \
    "$WORKDIR/squashfs-root" "$WORKDIR/ASSella.AppImage"

# Move generated zsync file to workdir
if [ -f "$SRC_DIR/ASSella.AppImage.zsync" ]; then
    mv "$SRC_DIR/ASSella.AppImage.zsync" "$WORKDIR/ASSella.AppImage.zsync"
elif [ -f "$WORKDIR/squashfs-root/../ASSella.AppImage.zsync" ]; then
    mv "$WORKDIR/squashfs-root/../ASSella.AppImage.zsync" "$WORKDIR/ASSella.AppImage.zsync"
fi

echo -e "${YELLOW}=== Verifying built AppImage runs offscreen ===${NC}"
# We test with offscreen platform. A successful launch will run until timeout (exit code 124).
# A crash will exit early with a different code.
set +e
QT_QPA_PLATFORM=offscreen timeout 5s "$WORKDIR/ASSella.AppImage" > /tmp/assella_test.log 2>&1
TEST_EXIT=$?
set -e

if [ $TEST_EXIT -ne 124 ]; then
    echo -e "${RED}Error: AppImage test run failed with code $TEST_EXIT.${NC}"
    cat /tmp/assella_test.log
    exit 1
fi
echo -e "${GREEN}Verification successful! AppImage launched successfully.${NC}"

echo -e "${YELLOW}=== Installing built AppImage locally ===${NC}"
cp -f "$WORKDIR/ASSella.AppImage" "$OUTPUT_APPIMAGE"
echo -e "${GREEN}Installed locally at: $OUTPUT_APPIMAGE${NC}"

echo -e "${YELLOW}=== Pushing to GitHub ===${NC}"
# Push branch
CURRENT_BRANCH=$(git branch --show-current)
echo "Pushing branch $CURRENT_BRANCH to remote..."
git push origin "$CURRENT_BRANCH"

# Push tag
echo "Pushing tag $TAG to remote..."
git push origin -f "$TAG"

# Create release if gh CLI is available
if command -v gh &>/dev/null; then
    # Check if release exists on GitHub, delete if so
    if env -u GITHUB_TOKEN gh release view "$TAG" &>/dev/null; then
        echo -e "${YELLOW}GitHub release $TAG already exists. Re-creating it...${NC}"
        env -u GITHUB_TOKEN gh release delete "$TAG" -y
    fi

    # Create GitHub release and upload both AppImage and the matching zsync file
    echo -e "${GREEN}Creating GitHub Release $TAG and uploading AppImage and ZSync files...${NC}"
    env -u GITHUB_TOKEN gh release create "$TAG" \
        "$WORKDIR/ASSella.AppImage" \
        "$WORKDIR/ASSella.AppImage.zsync" \
        --prerelease \
        --title "ASSella $TAG" \
        --notes-file "$SRC_DIR/release_notes.md"
else
    echo -e "${YELLOW}Warning: 'gh' CLI not found. Please create the release manually on GitHub and upload:${NC}"
    echo -e "${YELLOW}File to upload: $WORKDIR/ASSella.AppImage and $WORKDIR/ASSella.AppImage.zsync${NC}"
fi

echo -e "${GREEN}=== Build and release process completed successfully! ===${NC}"
