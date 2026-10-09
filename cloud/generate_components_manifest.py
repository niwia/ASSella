#!/usr/bin/env python3
"""
Generate manifests for optional on-demand components (Goldberg, Steamless, SLScheevo).

These components are large binaries that most users never need, so they are
hosted on Cloudflare R2 and downloaded by ASSella on first use. This script
packages each component directory into a deterministic `.tar.gz` and writes a
manifest with a SHA-256 so the app can tell whether a local copy is current.

Usage:
    python3 scripts/generate_components_manifest.py --output components_manifest.json

Then publish with:
    /home/aiwin/r2-publish/publish.sh
"""
import argparse
import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
DEPS_DIR = SRC_DIR / "deps"

# name -> (source directory, human metadata)
COMPONENTS: Dict[str, dict] = {
    "goldberg": {
        "dir": DEPS_DIR / "Goldberg",
        "name": "Goldberg Emulator",
        "description": (
            "Co-op/emulation runtime used by a minority of supported games. "
            "Required only when a game's Steam emulates multiplayer."
        ),
    },
    "steamless": {
        "dir": DEPS_DIR / "Steamless",
        "name": "Steamless",
        "description": (
            "Standalone launcher for games whose Steam DRM cannot run natively. "
            "Only needed for the handful of titles that require it."
        ),
    },
    "slscheevo": {
        "dir": DEPS_DIR / "SLScheevo",
        "name": "SLScheevo",
        "description": (
            "Achievement schema/stat helper. Only needed when generating "
            "Steam achievement statistics."
        ),
    },
}

# Files that should never ship in a component archive.
EXCLUDE_NAMES = {"__pycache__", ".git", ".DS_Store", ".pytest_cache", ".mypy_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".pdb"}


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDE_NAMES for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    return False


def iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if should_skip(p.relative_to(root)):
            continue
        yield p


def make_archive(src: Path, out_file: Path) -> tuple:
    """Deterministic tar.gz: fixed mtime, sorted entries, no owner metadata."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=9) as tar:
        for f in iter_files(src):
            arcname = Path(src.name) / f.relative_to(src)
            info = tar.gettarinfo(str(f), arcname=str(arcname))
            info.mtime = 0            # reproducibility
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if info.mode & 0o100 else 0o644
            with open(f, "rb") as fh:
                tar.addfile(info, fh)
    data = buffer.getvalue()
    out_file.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data), sum(1 for _ in iter_files(src))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default="components_manifest.json",
        help="Output manifest path (default: components_manifest.json)",
    )
    parser.add_argument(
        "--archive-dir", default=None,
        help="Where to write the .tar.gz files (default: alongside --output)",
    )
    args = parser.parse_args()

    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path
    archive_dir = Path(args.archive_dir) if args.archive_dir else out_path.parent
    archive_dir.mkdir(parents=True, exist_ok=True)

    components = {}
    for key, meta in COMPONENTS.items():
        src = meta["dir"]
        if not src.is_dir():
            print(f"  skip {key}: {src} not found")
            continue
        archive = archive_dir / f"{key}.tar.gz"
        sha, size, count = make_archive(src, archive)
        components[key] = {
            "name": meta["name"],
            "description": meta["description"],
            "archive": archive.name,
            "sha256": sha,
            "size_bytes": size,
            "file_count": count,
            "install_dir": src.name,
            "optional": True,
        }
        print(f"  {key:10} {size/1048576:7.2f} MB  {count:4} files  {archive.name}")

    manifest = {
        "manifest_version": 1,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "components": components,
    }
    out_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path} ({len(components)} components)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())