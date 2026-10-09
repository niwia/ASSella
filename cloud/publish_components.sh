#!/usr/bin/env bash
# Publish only the optional components (Goldberg / Steamless / SLScheevo) and
# their manifest to the Cloudflare R2 bucket.
#
# Use this when you only changed a component. For a full publish that also
# covers the Lua plugins, use /home/aiwin/r2-publish/publish.sh instead.
#
# Usage:
#   ./cloud/publish_components.sh
set -euo pipefail

CLOUD_DIR="$(cd "$(dirname "$0")" && pwd)"
PUBLISH_DIR="${PUBLISH_DIR:-/home/aiwin/r2-publish/github-files}"
BUCKET="${BUCKET:-github-files}"
BASE="https://pub-19657b4f385d424b91a909253efdb29c.r2.dev"

command -v wrangler >/dev/null 2>&1 || { echo "wrangler not found on PATH" >&2; exit 1; }

mkdir -p "$PUBLISH_DIR"

# Keep the publish directory pointing at the freshly built artefacts.
for f in "$CLOUD_DIR"/*.tar.gz "$CLOUD_DIR/components_manifest.json"; do
  [ -f "$f" ] || continue
  ln -sf "$f" "$PUBLISH_DIR/$(basename "$f")"
done

echo "==> Staged in $PUBLISH_DIR:"
for f in "$PUBLISH_DIR"/*.tar.gz "$PUBLISH_DIR/components_manifest.json"; do
  [ -f "$f" ] || continue
  printf '    %-28s %s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"
done

# ------------------------------------------------------------- files.json index
python3 - "$PUBLISH_DIR" "$BASE" <<'EOF'
import hashlib, json, os, sys
d, base = sys.argv[1], sys.argv[2]
files = []
for n in sorted(os.listdir(d)):
    p = os.path.join(d, n)
    if n.startswith(".") or n == "files.json" or not os.path.isfile(p):
        continue
    files.append({
        "name": n,
        "url": f"{base}/{n}",
        "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(),
        "size_bytes": os.path.getsize(p),
    })
json.dump({"version": 1, "files": files}, open(os.path.join(d, "files.json"), "w"), indent=2)
print(f"    files.json                    ({len(files)} entries)")
EOF

upload() {
  wrangler r2 object put "$BUCKET/$(basename "$1")" --file "$1" --remote \
    --cache-control "public, max-age=300"
}

# Payloads first, manifests last, so a client never fetches a manifest that
# advertises a file the bucket does not have yet.
echo "==> Uploading payloads..."
for f in "$PUBLISH_DIR"/*.tar.gz; do
  [ -f "$f" ] || continue
  upload "$f"
done

echo "==> Uploading manifests (last)..."
[ -f "$PUBLISH_DIR/components_manifest.json" ] && upload "$PUBLISH_DIR/components_manifest.json"
[ -f "$PUBLISH_DIR/files.json" ] && upload "$PUBLISH_DIR/files.json"

echo "==> Done."