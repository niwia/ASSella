#!/usr/bin/env python3
"""Utility script to generate plugins_manifest.json for Cloudflare R2 hosting.

Scans a directory for .lua files, computes their SHA-256 and byte sizes,
and formats a clean plugins_manifest.json.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
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
        "version": "1.0.1",
        "category": "interceptor",
        "required": True,
    },
    "spacebunny1.5.lua": {
        "name": "SpaceBunny 1.5",
        "description": "SLSsteam download interceptor with MRC authority hierarchy, curl timeout fixes, and monotonic clocks.",
        "version": "1.5.0",
        "category": "interceptor",
        "required": False,
    },
    "spacebunnny1.5.lua": {
        "name": "SpaceBunny 1.5",
        "description": "SLSsteam download interceptor with MRC authority hierarchy, curl timeout fixes, and monotonic clocks.",
        "version": "1.5.0",
        "category": "interceptor",
        "required": False,
    },
    "download-1.4.0-spacetest.lua": {
        "name": "SpaceTest 1.4.0",
        "description": "Experimental download interceptor with wudrm MRC authority model.",
        "version": "1.4.0",
        "category": "interceptor",
        "required": False,
    },
    "spliced-tickets.lua": {
        "name": "Spliced Tickets",
        "description": "App and depot ownership ticket splicing handler for SLSsteam.",
        "version": "1.0.0",
        "category": "companion",
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


def generate_manifest(plugins_dir: Path, output_file: Path, force_bump: bool = False) -> dict:
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
                "category": "interceptor" if fname != "spliced-tickets.lua" else "companion",
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
            "category": meta.get("category", "interceptor"),
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
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate plugins_manifest.json for R2 and sync plugins")
    default_accela_dir = Path.home() / ".local/share/ACCELA/plugins"
    default_r2_staging = Path.home() / "r2-publish/github-files"
    publish_script = Path.home() / "r2-publish/publish.sh"

    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=default_r2_staging if default_r2_staging.is_dir() else default_accela_dir,
        help="Directory containing .lua plugins (default: r2-publish/github-files or ~/.local/share/ACCELA/plugins)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON file path (default: <plugins-dir>/plugins_manifest.json)",
    )
    parser.add_argument(
        "--stage-dir",
        type=Path,
        default=default_r2_staging if default_r2_staging.is_dir() else None,
        help="R2 staging directory to synchronize plugins into (default: ~/r2-publish/github-files)",
    )
    parser.add_argument(
        "--force-bump",
        action="store_true",
        help="Force increment version even if SHA-256 did not change",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Run publish.sh in r2-publish after generating manifest",
    )
    args = parser.parse_args()

    # Step 1: Synchronize plugins from ~/.local/share/ACCELA/plugins to stage-dir if staging
    stage_dir = args.stage_dir
    if stage_dir and stage_dir.is_dir() and default_accela_dir.is_dir():
        print(f"Syncing plugins from {default_accela_dir} to {stage_dir}...")
        for src_file in default_accela_dir.glob("*.lua"):
            dest_name = src_file.name
            # Normalize spacebunnny1.5.lua -> spacebunny1.5.lua (single 'n')
            if dest_name in ("spacebunnny1.5.lua", "spacebunnny1.5beta.lua"):
                dest_name = "spacebunny1.5.lua"

            dest_path = stage_dir / dest_name
            try:
                if dest_path.is_symlink() or dest_path.exists():
                    if dest_path.resolve() == src_file.resolve():
                        print(f"  Already linked: {dest_path.name} -> {src_file}")
                        continue
                    # Remove stale link/file
                    dest_path.unlink()
                dest_path.symlink_to(src_file)
                print(f"  Linked: {dest_path.name} -> {src_file}")
            except Exception as e:
                # Fallback to copy2
                try:
                    if dest_path.exists() and dest_path.resolve() == src_file.resolve():
                        continue
                    shutil.copy2(src_file, dest_path)
                    print(f"  Copied: {src_file.name} -> {dest_path.name}")
                except Exception as copy_err:
                    print(f"  Error syncing {src_file.name}: {copy_err}")

    target_dir = stage_dir if (stage_dir and stage_dir.is_dir()) else args.plugins_dir
    out_file = args.output or (target_dir / "plugins_manifest.json")

    manifest = generate_manifest(target_dir, out_file, force_bump=args.force_bump)

    # If staging dir was used, also mirror plugins_manifest.json back to ACCELA plugins
    target_accela_manifest = default_accela_dir / "plugins_manifest.json"
    if stage_dir and default_accela_dir.is_dir() and out_file.resolve() != target_accela_manifest.resolve():
        shutil.copy2(out_file, target_accela_manifest)
        print(f"Mirrored manifest to {target_accela_manifest}")

    # Step 2: Publish if requested
    if args.publish:
        if publish_script.is_file():
            print(f"Executing publish script: {publish_script} ...")
            subprocess.run(["bash", str(publish_script)], cwd=str(publish_script.parent), check=True)
            print("Publish to Cloudflare R2 complete!")
        else:
            print(f"Warning: Publish script not found at {publish_script}")


if __name__ == "__main__":
    main()
