#!/usr/bin/env python3
"""Utility script to generate plugins_manifest.json for Cloudflare R2 hosting.

Scans a directory for .lua files, computes their SHA-256 and byte sizes,
and formats a clean plugins_manifest.json.
"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def calculate_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


DEFAULT_DESCRIPTIONS = {
    "download.lua": {
        "name": "Download Interceptor",
        "description": "Native Steam download interception and depot streaming plugin for SLSsteam.",
        "version": "1.0.0",
        "required": True,
    },
    "spliced-tickets.lua": {
        "name": "Spliced Tickets",
        "description": "App and depot ownership ticket splicing handler for SLSsteam.",
        "version": "1.0.0",
        "required": True,
    },
}


def increment_version(version_str: str) -> str:
    """Increment version x.y.z where z rolls over at 10 to bump y, and y rolls over at 10 to bump x."""
    try:
        parts = [int(p) for p in str(version_str).split(".")]
        while len(parts) < 3:
            parts.append(0)
        x, y, z = parts[0], parts[1], parts[2]
    except Exception:
        x, y, z = 1, 0, 0

    z += 1
    if z >= 10:
        z = 0
        y += 1
        if y >= 10:
            y = 0
            x += 1
    return f"{x}.{y}.{z}"


def generate_manifest(plugins_dir: Path, output_file: Path, force_bump: bool = False) -> None:
    # Attempt to load existing manifest to check versions and hashes
    existing_plugins = {}
    manifest_source = output_file if output_file.is_file() else (plugins_dir / "plugins_manifest.json")
    if manifest_source.is_file():
        try:
            with open(manifest_source, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                existing_plugins = old_data.get("plugins", {})
        except Exception:
            existing_plugins = {}

    plugins = {}
    for lua_file in sorted(plugins_dir.glob("*.lua")):
        fname = lua_file.name
        curr_sha256 = calculate_sha256(lua_file)
        curr_size = lua_file.stat().st_size

        meta = DEFAULT_DESCRIPTIONS.get(
            fname,
            {
                "name": fname.replace(".lua", "").replace("-", " ").title(),
                "description": f"SLSsteam plugin {fname}",
                "version": "1.0.0",
                "required": False,
            },
        )

        old_entry = existing_plugins.get(fname, {})
        old_sha = old_entry.get("sha256", "")
        old_version = old_entry.get("version", meta.get("version", "1.0.0"))

        if force_bump or not old_sha or old_sha != curr_sha256:
            new_version = increment_version(old_version) if old_sha else old_version
        else:
            new_version = old_version

        plugins[fname] = {
            "name": meta["name"],
            "filename": fname,
            "version": new_version,
            "description": meta["description"],
            "sha256": curr_sha256,
            "size_bytes": curr_size,
            "required": meta["required"],
        }

    manifest = {
        "manifest_version": 1,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plugins": plugins,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(f"Generated manifest with {len(plugins)} plugins -> {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Generate plugins_manifest.json for R2")
    default_dir = Path.home() / ".local/share/ACCELA/plugins"
    if not default_dir.is_dir():
        default_dir = Path(".")
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=default_dir,
        help="Directory containing .lua plugins (default: ~/.local/share/ACCELA/plugins)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("plugins_manifest.json"),
        help="Output JSON file path",
    )
    parser.add_argument(
        "--force-bump",
        action="store_true",
        help="Force increment version even if SHA-256 did not change",
    )
    args = parser.parse_args()
    generate_manifest(args.plugins_dir, args.output, force_bump=args.force_bump)


if __name__ == "__main__":
    main()
