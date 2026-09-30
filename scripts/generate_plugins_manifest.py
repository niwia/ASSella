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


def generate_manifest(plugins_dir: Path, output_file: Path) -> None:
    plugins = {}
    for lua_file in sorted(plugins_dir.glob("*.lua")):
        fname = lua_file.name
        meta = DEFAULT_DESCRIPTIONS.get(
            fname,
            {
                "name": fname.replace(".lua", "").replace("-", " ").title(),
                "description": f"SLSsteam plugin {fname}",
                "version": "1.0.0",
                "required": False,
            },
        )
        plugins[fname] = {
            "name": meta["name"],
            "filename": fname,
            "version": meta["version"],
            "description": meta["description"],
            "sha256": calculate_sha256(lua_file),
            "size_bytes": lua_file.stat().st_size,
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
    args = parser.parse_args()
    generate_manifest(args.plugins_dir, args.output)


if __name__ == "__main__":
    main()
