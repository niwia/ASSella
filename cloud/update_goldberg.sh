#!/usr/bin/env bash
# Fetch a Goldberg release, install it into src/deps/Goldberg, repackage it and
# publish it (plus any other component) to the Cloudflare R2 bucket.
#
# Usage:
#   ./cloud/update_goldberg.sh                 # latest release
#   ./cloud/update_goldberg.sh v1.0.0-beta1   # a specific tag
#   ./cloud/update_goldberg.sh --no-publish   # just stage locally
#
# Source: https://github.com/Detanup01/gbe_fork/releases
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLOUD_DIR="$REPO_ROOT/cloud"
GOLDBERG_SRC="$REPO_ROOT/src/deps/Goldberg"
PUBLISH_DIR="${PUBLISH_DIR:-$HOME/r2-publish/github-files}"
API="https://api.github.com/repos/Detanup01/gbe_fork/releases"

TAG=""
DO_PUBLISH=1
for arg in "$@"; do
  case "$arg" in
    --no-publish) DO_PUBLISH=0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) TAG="$arg" ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1" >&2; exit 1; }; }
need curl
need python3
need tar

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ------------------------------------------------------------ resolve release
if [ -z "$TAG" ]; then
  echo "==> Resolving latest release..."
  TAG="$(curl -fsSL "$API/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
fi
echo "==> Release: $TAG"

# --------------------------------------------- pick the Linux release archive
# gbe_fork publishes several assets per release:
#   emu-linux-release.tar.bz2   <- the one we want (82 MB, full linux payload)
#   emu-linux-debug.tar.bz2     <- debug build, not for end users
#   emu-win-*                   <- Windows only
#   migrate_gse-*               <- migration helpers, not the payload
echo "==> Selecting the linux release asset..."
ASSET_URL="$(curl -fsSL "$API/releases/tags/$TAG" | python3 -c '
import json, sys
rel = json.load(sys.stdin)
assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
# Preferred order: exact linux release, then any linux archive.
for want in ("emu-linux-release.tar.bz2", "emu-linux-release.tar.gz",
             "emu-linux.tar.bz2", "emu-linux.tar.gz"):
    if want in assets:
        print(assets[want]); break
else:
    cands = [(n, u) for n, u in assets.items()
             if "linux" in n.lower() and "debug" not in n.lower()
             and n.lower().endswith((".tar.bz2", ".tar.gz", ".tgz", ".tar.xz", ".zip"))]
    if cands:
        cands.sort(key=lambda p: p[0])
        print(cands[0][1])
    else:
        sys.exit("No linux release asset found in this release.")
')"

echo "==> Asset: $ASSET_URL"

# ------------------------------------------------------------- extract
# gbe_fork ships .tar.bz2, so decompression is chosen from the filename rather
# than assumed.
ARCHIVE="$WORK/gbe.archive"
curl -fsSL "$ASSET_URL" -o "$ARCHIVE"

echo "==> Extracting..."
mkdir -p "$WORK/x"
case "$ASSET_URL" in
  *tar.bz2|*.tbz2) tar -xjf "$ARCHIVE" -C "$WORK/x" ;;
  *tar.gz|*.tgz)   tar -xzf "$ARCHIVE" -C "$WORK/x" ;;
  *tar.xz)         tar -xJf "$ARCHIVE" -C "$WORK/x" ;;
  *.zip)           need unzip; unzip -q "$ARCHIVE" -d "$WORK/x" ;;
  *)               echo "!! Unsupported archive type: $ASSET_URL" >&2; exit 1 ;;
esac

# Locate the directory that actually holds the Goldberg payload. Release
# archives sometimes wrap it in another folder, so search rather than assume.
FOUND=""
for cand in "$WORK/x" "$WORK/x/Goldberg" "$WORK/x/gbe" "$WORK/x/steam_settings"; do
  if [ -d "$cand/linux" ] || [ -f "$cand/steam_appid.txt" ]; then FOUND="$cand"; break; fi
done
if [ -z "$FOUND" ]; then
  FOUND="$(find "$WORK/x" -maxdepth 3 -type d \( -name linux -o -name steam_settings \) \
           -printf '%h\n' 2>/dev/null | head -1)"
fi
if [ -z "$FOUND" ]; then
  echo "!! Could not locate the Goldberg payload in the release archive." >&2
  echo "   Extracted tree:" >&2
  find "$WORK/x" -maxdepth 2 | sed 's/^/     /' >&2
  exit 1
fi
echo "==> Payload at: ${FOUND#$WORK/x/}"

# ------------------------------------------------------------- install
echo "==> Installing into src/deps/Goldberg ..."
rm -rf "$GOLDBERG_SRC"
mkdir -p "$GOLDBERG_SRC"
cp -a "$FOUND/." "$GOLDBERG_SRC/"

# Sanity check: the payload must have the files we actually load at runtime.
missing=""
for f in steam_appid.txt version; do
  [ -f "$GOLDBERG_SRC/$f" ] || missing="$missing $f"
done
if [ -n "$missing" ]; then
  echo "!! Installed payload is missing:$missing" >&2
  echo "   Refusing to package a possibly-incomplete Goldberg." >&2
  exit 1
fi
if [ ! -d "$GOLDBERG_SRC/linux" ] && [ ! -d "$GOLDBERG_SRC/windows" ]; then
  echo "!! Installed payload has neither linux/ nor windows/ - refusing to package." >&2
  exit 1
fi
echo "    contents: $(find "$GOLDBERG_SRC" -type f | wc -l) files"

# ------------------------------------------------------------- repackage
echo "==> Repackaging + writing manifest..."
python3 "$CLOUD_DIR/generate_components_manifest.py" \
        --output "$CLOUD_DIR/components_manifest.json" \
        --archive-dir "$CLOUD_DIR"

# ------------------------------------------------------------- publish
if [ "$DO_PUBLISH" -eq 0 ]; then
  echo "==> Staged locally (skipped publish). Copy $CLOUD_DIR/*.tar.gz and"
  echo "    components_manifest.json into $PUBLISH_DIR, then run publish.sh."
  exit 0
fi

mkdir -p "$PUBLISH_DIR"
for f in "$CLOUD_DIR"/*.tar.gz "$CLOUD_DIR/components_manifest.json"; do
  ln -sf "$f" "$PUBLISH_DIR/$(basename "$f")"
done

echo "==> Publishing to R2 ..."
"$REPO_ROOT/cloud/publish_components.sh"
echo "==> Done. Users will see 'Update available' for Goldberg."