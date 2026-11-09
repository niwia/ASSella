#!/usr/bin/env python3
"""Build, sign and stage plugins_manifest.json for ASSella.

The source of truth is the plugin repository (``~/Documents/spacebunny`` by
default), never ASSella's own download cache. For each published plugin this
script:

  * reads the metadata the plugin carries itself: its version
    (``X.VERSION = "..."`` or the ``-- name.lua (x.y)`` header) and its role
    (``KATAMARI.plugins.<id> = { role = "..." }``); the role decides whether
    ASSella treats it as a download interceptor;
  * refuses to publish a file whose contents changed while its version did
    not (bump the version in the plugin, or pass --allow-same-version);
  * copies the file into the staging directory (real copies, no symlinks);
  * writes the manifest with a monotonic ``serial`` and signs it with Ed25519
    (``plugins_manifest.json.sig``), which ASSella verifies before trusting it.

The PUBLISH table below is what ASSella shows in Settings -> AT0-M; a plugin
left out of it is not shown. Per plugin:

  required: True      essential; always installed, cannot be turned off
  default_on: True    optional, installed by default, the user may turn it off
  (neither)           optional, off until the user turns it on
  deployable: False   shown greyed out with disabled_reason; installed copies
                      are moved to .bak

RETIRED lists plugins that are gone for good (moved to .bak everywhere,
deleted from the bucket by ``--publish --prune``).

Typical use:

    # once: create the signing key, then put the printed public key in
    # MANIFEST_PUBLIC_KEYS in src/utils/plugin_manager.py
    python3 scripts/generate_plugins_manifest.py --init-key

    # every release
    python3 scripts/generate_plugins_manifest.py            # build + sign + stage
    python3 scripts/generate_plugins_manifest.py --publish  # ... and run publish.sh
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
DEFAULT_SOURCE = HOME / "Documents" / "spacebunny"
DEFAULT_STAGE = HOME / "r2-publish" / "github-files"
DEFAULT_KEY = HOME / "r2-publish" / "keys" / "plugins_manifest_ed25519.pem"
DEFAULT_PUBLISH_SCRIPT = HOME / "r2-publish" / "publish.sh"
BUNDLED_MANIFEST = Path(__file__).resolve().parents[1] / "src" / "res" / "plugins" / "plugins_manifest.json"

MANIFEST_NAME = "plugins_manifest.json"
INTERCEPTOR_ROLES = {"mrc-provider", "download-interceptor", "interceptor"}

# What gets published. Text here is what users see in ASSella.
PUBLISH: dict[str, dict] = {
    "spacebunny.lua": {
        "name": "SpaceBunny",
        "description": "Download interceptor: injects depots and decryption keys and resolves "
                       "manifest request codes from several providers with circuit breakers.",
        "required": True,
        "is_default": True,
        "deployable": True,
    },
    "styx.lua": {
        "name": "Styx",
        "description": "Ownership ticket splicer so apps added through AdditionalApps pass SteamStub DRM checks.",
        "required": True,
        "deployable": True,
    },
    "lobster.lua": {
        "name": "Lobster",
        "description": "Restores AdditionalDepots and DecryptionKeys in config.yaml when an SLSsteam update strips them.",
        "required": True,
        "deployable": True,
    },
    "lettuce.lua": {
        "name": "Lettuce",
        "description": "Proxy / ISP-block transport for the plugins' own requests (MRC providers, Hubcap), "
                       "with a Cloudflare worker fallback. Formerly Bypass.",
        "required": False,
        # SpaceBunny's (and later Pineapple's) one-shot proxy step; on by
        # default, the user may turn it off.
        "default_on": True,
        "deployable": True,
    },
    "pineapple.lua": {
        # Published name differs from the file in the plugin repository.
        "source": "pinapple_v1.lua",
        "name": "Pineapple",
        "description": "Manifest fallback: puts Hubcap manifests into Steam's depotcache before Steam asks, "
                       "so updates work when every MRC provider is down. Formerly Bakeneko.",
        "required": False,
        # Not ready: no tests yet, and it does not use Lettuce for its own
        # requests (only proxy.worker_url). Shown greyed out until then.
        "deployable": False,
        "disabled_reason": "Pineapple is still being finished and will be enabled in a future update.",
    },
}

RETIRED: dict[str, dict] = {
    "hubcap.lua": {"replaced_by": "pineapple.lua", "reason": "The Hubcap key resolver is retired; Pineapple replaces it."},
    "bypass.lua": {"replaced_by": "lettuce.lua", "reason": "Renamed to Lettuce."},
    "spacebunny1.5.lua": {"replaced_by": "spacebunny.lua", "reason": "Legacy SpaceBunny alias."},
    "spliced-tickets.lua": {"replaced_by": "styx.lua", "reason": "Superseded by Styx."},
    "katamari.lua": {"replaced_by": None, "reason": "The registry is no longer needed; katamari.conf.lua stays the shared config."},
    "bakeneko.lua": {"replaced_by": "pineapple.lua", "reason": "Renamed to Pineapple."},
    "download.lua": {"replaced_by": "spacebunny.lua", "reason": "The Standard Interceptor is no longer published; SpaceBunny is the interceptor."},
}

_KATAMARI_RE = re.compile(r"KATAMARI\.plugins(?:\.(\w+)|\[\s*[\"'](\w+)[\"']\s*\])\s*=\s*\{(.*?)\}", re.S)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plugin_meta(text: str) -> dict:
    """Version and role from the plugin's own source."""
    meta: dict = {"version": None, "role": None, "id": None}
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    m = re.search(r'\bVERSION\s*=\s*(?:\w+\.VERSION\s+or\s+)?["\']([^"\']+)["\']', text)
    if m:
        meta["version"] = m.group(1).strip()
    else:
        h = re.search(r"^--\s*[\w.\-]+\.lua\s*\(v?([\w.\-]+)\)", text, re.M)
        if h:
            meta["version"] = h.group(1)
    k = _KATAMARI_RE.search(code)
    if k:
        meta["id"] = k.group(1) or k.group(2)
        r = re.search(r"\brole\s*=\s*[\"']([\w\-]+)[\"']", k.group(3))
        if r:
            meta["role"] = r.group(1)
    if not meta["role"]:
        if re.search(r"GetPackage|GetManifestRequestCode|GetMRC\b|GetBinary", code):
            meta["role"] = "download-interceptor"
        elif "GetAppOwnershipTicket" in code:
            meta["role"] = "ticket-splicer"
    return meta


# ---------------------------------------------------------------- signing key
def _load_private_key(path: Path):
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def public_key_b64(private_key) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode()


def init_key(path: Path) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if path.exists():
        print(f"Key already exists: {path}")
        print("Public key:", public_key_b64(_load_private_key(path)))
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    print(f"Created signing key: {path}  (keep it private; back it up)")
    print("Public key (put this in MANIFEST_PUBLIC_KEYS):", public_key_b64(key))
    return 0


# ------------------------------------------------------------------- building
def previous_manifest(stage: Path) -> dict:
    for cand in (stage / MANIFEST_NAME, BUNDLED_MANIFEST):
        try:
            if cand.is_file() and not cand.is_symlink():
                return json.loads(cand.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def run_lua_tests(source: Path) -> bool:
    tests = ["test_SpaceBunny1_8.0beta.lua", "test_styx.lua", "test_lettuce.lua", "test_pinapple.lua", "test_lobster.lua"]
    luajit = shutil.which("luajit")
    if not luajit:
        print("luajit not found; skipping plugin tests (use --skip-tests to silence this)")
        return True
    ok = True
    for t in tests:
        if not (source / t).is_file():
            continue
        res = subprocess.run([luajit, t], cwd=source, capture_output=True, text=True, timeout=300)
        tail = (res.stdout.strip().splitlines() or [""])[-1]
        failed = bool(res.returncode != 0 or re.search(r"\b[1-9]\d* (?:failures|failed)\b", tail) or "FAIL " in res.stdout)
        print(f"  {'FAIL' if failed else 'ok  '} {t}: {tail}")
        ok = ok and not failed
    return ok


def build_manifest(source: Path, stage: Path, allow_same_version: bool) -> tuple[dict, dict[str, bytes]]:
    prev = (previous_manifest(stage).get("plugins") or {})
    plugins: dict[str, dict] = {}
    payloads: dict[str, bytes] = {}
    problems = []
    for fname, info in PUBLISH.items():
        src = source / info.get("source", fname)
        if not src.is_file():
            problems.append(f"{fname}: {src.name} not found in {source}")
            continue
        data = src.read_bytes()
        meta = plugin_meta(data.decode("utf-8", errors="ignore"))
        meta["version"] = meta["version"] or info.get("version")
        if not meta["version"]:
            problems.append(f"{fname}: no version in the file (VERSION = \"x.y\" or a '-- {fname} (x.y)' header)")
            continue
        digest = sha256_bytes(data)
        old = prev.get(fname) or {}
        if old.get("sha256") and old["sha256"] != digest and str(old.get("version")) == meta["version"] and not allow_same_version:
            problems.append(f"{fname}: contents changed but version is still {meta['version']}; bump it in the plugin")
            continue
        role = meta["role"] or info.get("role") or "companion"
        entry = {
            "name": info["name"],
            "filename": fname,
            "version": meta["version"],
            "role": role,
            "category": "interceptor" if role in INTERCEPTOR_ROLES else "companion",
            "description": info["description"],
            "sha256": digest,
            "size_bytes": len(data),
            "required": bool(info.get("required", False)),
            "deployable": bool(info.get("deployable", True)),
        }
        if info.get("is_default"):
            entry["is_default"] = True
        if info.get("default_on") and not entry["required"]:
            entry["default_on"] = True
        if info.get("disabled_reason"):
            entry["disabled_reason"] = info["disabled_reason"]
        plugins[fname] = entry
        payloads[fname] = data

    if problems:
        print("Not publishing:")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    manifest = {
        "manifest_version": 2,
        "serial": int(now.strftime("%Y%m%d%H%M%S")),
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "default_interceptor": "spacebunny.lua",
        "plugins": plugins,
        "retired": RETIRED,
    }
    return manifest, payloads


def stage_files(stage: Path, manifest_bytes: bytes, signature: bytes, payloads: dict[str, bytes]) -> None:
    stage.mkdir(parents=True, exist_ok=True)

    def put(name: str, data: bytes) -> None:
        dst = stage / name
        if dst.is_symlink():
            dst.unlink()  # the old layout linked into ASSella's cache; replace with a real file
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)

    for name, data in payloads.items():
        put(name, data)
    put(MANIFEST_NAME + ".sig", signature)
    put(MANIFEST_NAME, manifest_bytes)

    # Retired payloads stay out of the upload set.
    for name in RETIRED:
        p = stage / name
        if p.is_symlink() or p.is_file():
            p.unlink()
            print(f"  removed retired {name} from staging")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build, sign and stage plugins_manifest.json for ASSella.")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="plugin repository (default: ~/Documents/spacebunny)")
    ap.add_argument("--stage", type=Path, default=DEFAULT_STAGE, help="R2 staging directory (default: ~/r2-publish/github-files)")
    ap.add_argument("--key", type=Path, default=DEFAULT_KEY, help="Ed25519 signing key (PEM)")
    ap.add_argument("--init-key", action="store_true", help="create the signing key and print its public key")
    ap.add_argument("--allow-same-version", action="store_true", help="publish changed files without a version bump")
    ap.add_argument("--skip-tests", action="store_true", help="do not run the luajit plugin tests first")
    ap.add_argument("--dry-run", action="store_true", help="print the manifest, write nothing")
    ap.add_argument("--update-bundled", action="store_true", help="also write src/res/plugins/plugins_manifest.json")
    ap.add_argument("--publish", action="store_true", help="run publish.sh after staging")
    ap.add_argument("--prune", action="store_true",
                    help="with --publish: also delete retired plugins from the bucket")
    args = ap.parse_args()

    if args.init_key:
        return init_key(args.key)

    if not args.key.is_file():
        print(f"No signing key at {args.key}. Create one with --init-key.")
        return 1
    key = _load_private_key(args.key)

    if not args.skip_tests:
        print("Running plugin tests...")
        if not run_lua_tests(args.source):
            print("Plugin tests failed; not publishing.")
            return 1

    manifest, payloads = build_manifest(args.source, args.stage, args.allow_same_version)
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    signature = base64.b64encode(key.sign(manifest_bytes)) + b"\n"

    for fname, e in manifest["plugins"].items():
        flag = "" if e["deployable"] else "  (not deployable)"
        print(f"  {fname:<16} {e['version']:<8} {e['role']:<20} {e['sha256'][:12]}{flag}")
    print(f"serial {manifest['serial']}, signed with key {public_key_b64(key)[:16]}...")

    if args.dry_run:
        return 0

    stage_files(args.stage, manifest_bytes, signature, payloads)
    print(f"Staged {len(payloads)} plugins + signed manifest in {args.stage}")

    if args.update_bundled:
        # The bundled copies are the offline fallback (and are used when a
        # source has no verified copy), so they must match the manifest.
        bundled_dir = BUNDLED_MANIFEST.parent
        BUNDLED_MANIFEST.write_bytes(manifest_bytes)
        for name, data in payloads.items():
            (bundled_dir / name).write_bytes(data)
        for old in bundled_dir.glob("*.lua"):
            if old.name not in payloads:
                old.unlink()
                print(f"  removed {old.name} from bundled plugins")
        print(f"Updated bundled manifest and {len(payloads)} plugin(s) in {bundled_dir}")

    if args.publish:
        if not DEFAULT_PUBLISH_SCRIPT.is_file():
            print(f"publish script not found: {DEFAULT_PUBLISH_SCRIPT}")
            return 1
        cmd = ["bash", str(DEFAULT_PUBLISH_SCRIPT), str(args.stage)]
        if args.prune:
            cmd.append("--prune")
        subprocess.run(cmd, cwd=DEFAULT_PUBLISH_SCRIPT.parent, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
