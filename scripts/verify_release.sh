#!/usr/bin/env bash
# Local dry-run harness for the ASSella AppImage release pipeline.
#
# Reproduces, on this machine, exactly what the "Build AppImage with ZSync
# Update Information" and "Verify Release Artifact Integrity" workflow steps do,
# so a build can be validated before it is ever tagged or published.
#
# Usage:
#   scripts/verify_release.sh <path-to-AppImage> [channel]
#
# Example:
#   scripts/verify_release.sh ~/assela-build/ASSella.AppImage beta
#
# Channel is one of stable|beta|canary and only affects the expected update
# string, matching the workflow's tag scoping.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

APPIMAGE="${1:-}"
CHANNEL="${2:-beta}"

if [ -z "$APPIMAGE" ] || [ ! -f "$APPIMAGE" ]; then
  echo "usage: $0 <path-to-AppImage> [stable|beta|canary]" >&2
  exit 2
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# --- Replicate the workflow's channel-scoped update string -------------------
case "$CHANNEL" in
  canary) UPDATE_TAG="*testing*" ;;
  beta)   UPDATE_TAG="*beta*" ;;
  *)      UPDATE_TAG="*" ;;
esac
UPDATE_INFO="gh-releases-zsync|niwia|ASSella|${UPDATE_TAG}|ASSella.AppImage.zsync"

echo -e "${CYAN}Channel:    $CHANNEL${NC}"
echo -e "${CYAN}Update tag: $UPDATE_TAG${NC}"
echo -e "${CYAN}Update str: $UPDATE_INFO${NC}"
echo

# --- Regenerate the zsync the way the workflow now does ----------------------
echo -e "${CYAN}==> Regenerating zsync from the final AppImage (as CI now does)${NC}"
if ! command -v zsyncmake &>/dev/null; then
  echo -e "${RED}zsyncmake not installed. Install it with:${NC}"
  echo -e "${RED}    apt install zsync      # Debian/Ubuntu${NC}"
  echo -e "${RED}    sudo pacman -S zsync  # Arch${NC}"
  exit 3
fi

# Resolve to an absolute path up front: the script cd's into $WORKDIR later,
# and the shipped-artifact check needs to read the original file.
APPIMAGE_ABS="$(cd "$(dirname "$APPIMAGE")" && pwd)/$(basename "$APPIMAGE")"

cp "$APPIMAGE_ABS" "$WORKDIR/ASSella.AppImage"
zsyncmake -u "$UPDATE_INFO" -o "$WORKDIR/ASSella.AppImage.zsync" "$WORKDIR/ASSella.AppImage"
sha256sum "$WORKDIR/ASSella.AppImage" | sed "s|$WORKDIR/||" > "$WORKDIR/ASSella.AppImage.sha256"
sha256sum "$WORKDIR/ASSella.AppImage.zsync" | sed "s|$WORKDIR/||" > "$WORKDIR/ASSella.AppImage.zsync.sha256"
echo

# --- The integrity gate, verbatim from the workflow --------------------------
echo -e "${CYAN}==> Verifying release artifact integrity${NC}"
cd "$WORKDIR"
actual_size=$(stat -c%s ASSella.AppImage)
actual_sha1=$(sha1sum ASSella.AppImage | awk '{print $1}')
actual_sha256=$(sha256sum ASSella.AppImage | awk '{print $1}')

zsync_size=$(grep -i '^Length:' ASSella.AppImage.zsync | awk '{print $2}' | tr -d '\r')
zsync_sha1=$(grep -i '^SHA-1:' ASSella.AppImage.zsync | awk '{print $2}' | tr -d '\r')

fail=0
if [ "$zsync_size" != "$actual_size" ]; then
  echo "  FAIL zsync Length mismatch: zsync=$zsync_size actual=$actual_size"; fail=1
else
  echo -e "  ${GREEN}OK${NC}   zsync Length  = $actual_size"
fi

if [ "$zsync_sha1" != "$actual_sha1" ]; then
  echo "  FAIL zsync SHA-1 mismatch: zsync=$zsync_sha1 actual=$actual_sha1"; fail=1
else
  echo -e "  ${GREEN}OK${NC}   zsync SHA-1   = $actual_sha1"
fi

if [ "$(awk '{print $1}' ASSella.AppImage.sha256)" != "$actual_sha256" ]; then
  echo "  FAIL .sha256 does not match the AppImage"; fail=1
else
  echo -e "  ${GREEN}OK${NC}   AppImage sha256 matches"
fi

if ! grep -qi '^Filename:[[:space:]]*ASSella\.AppImage[[:space:]]*$' ASSella.AppImage.zsync; then
  echo "  FAIL zsync Filename header does not match published asset name"; fail=1
else
  echo -e "  ${GREEN}OK${NC}   zsync Filename header"
fi

EMBEDDED=$(grep -a -o 'gh-releases-zsync|[^[:cntrl:]]*' ASSella.AppImage | head -1 || true)
echo "  Embedded update string: ${EMBEDDED:-<none>}"
case "$EMBEDDED" in
  gh-releases-zsync\|niwia\|ASSella\|*) echo -e "  ${GREEN}OK${NC}   update string embedded" ;;
  *) echo "  FAIL update string missing or malformed - delta updates would break"; fail=1 ;;
esac

echo

# --- Also report on what the SHIPPED image declares --------------------------
echo -e "${CYAN}==> Update string baked into the artifact you passed in${NC}"
SHIPPED=$(grep -a -o 'gh-releases-zsync|[^[:cntrl:]]*' "$APPIMAGE_ABS" | head -1 || true)
if [ -z "$SHIPPED" ]; then
  echo -e "  ${YELLOW}none embedded - this AppImage was built without -u, delta updates will not work${NC}"
else
  echo "  $SHIPPED"
  case "$SHIPPED" in
    *'|latest|'*) echo -e "  ${YELLOW}WARN resolves via 'latest', which 404s here (all releases are prereleases)${NC}" ;;
  esac
fi
echo

if [ "$fail" -ne 0 ]; then
  echo -e "${RED}${CYAN}==> RESULT: WOULD BLOCK THE RELEASE${NC}"
  echo "A build in this state must not be published: delta updates would be corrupt."
  exit 1
fi
echo -e "${GREEN}==> RESULT: all artifacts internally consistent - safe to publish${NC}"
echo
echo "To inspect the runtime payload:"
echo "  $APPIMAGE_ABS --appimage-extract && ls squashfs-root/bin"