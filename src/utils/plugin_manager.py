"""Plugin Manager: SLSsteam Lua plugins, distributed through a signed manifest.

How plugins reach a user
------------------------
1. ``plugins_manifest.json`` is fetched from the plugin sources (Cloudflare R2
   today; more mirrors can be appended to ``PLUGIN_SOURCES``). Each source is
   tried in order. A manifest is only accepted when its detached Ed25519
   signature (``plugins_manifest.json.sig``) verifies against a key built into
   ASSella and it is not older than the manifest already cached (rollback
   guard). TLS certificate checks are never disabled.
2. Only when no source is reachable does ASSella fall back to the cached
   manifest, then to the one bundled in ``res/plugins``.
3. Plugin files come from the same sources, and every file must match the
   SHA-256 in the manifest. Local copies (cache, bundled) are used only when
   the sources are unreachable, and then only when their hash matches.

The manifest also controls what may run:
  * ``deployable: false`` on an entry disables that plugin for everyone: it is
    not installed, and an installed copy is moved to ``.bak``.
  * ``retired`` lists plugins that are gone (with an optional
    ``replaced_by``); installed copies are moved to ``.bak``.

Installed files are never deleted or silently replaced: an older copy is kept
as ``<name>.bak``, and the 6-hourly background check only updates a plugin
when the installed file is one ASSella itself published (its hash appears in
a verified manifest). A file the user edited is left alone.
"""

import base64
import hashlib
import json
import logging
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from utils.helpers import get_base_path
from utils.paths import Paths
from utils.settings import get_settings
from utils.yaml_config_manager import (
    ensure_plugins_enabled,  # noqa: F401  (re-exported for older callers)
    ensure_sls_plugin_config_sections,
    ensure_smart_tickets_enabled,
    get_sls_plugins_dirs,
    is_slssteam_plugins_enabled,  # noqa: F401
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sources and trust
# ---------------------------------------------------------------------------

# Tried in order. Add GitLab / other mirrors here later; every mirror serves the
# same signed manifest and files, so a mirror does not need to be trusted.
PLUGIN_SOURCES: List[str] = [
    "https://pub-19657b4f385d424b91a909253efdb29c.r2.dev",
]
R2_PLUGINS_BASE_URL = PLUGIN_SOURCES[0]  # kept for older imports

MANIFEST_FILENAME = "plugins_manifest.json"
SIGNATURE_SUFFIX = ".sig"
MANIFEST_URL = f"{R2_PLUGINS_BASE_URL}/{MANIFEST_FILENAME}"

# Ed25519 public keys (raw 32 bytes, base64) allowed to sign the manifest.
# The private key lives only on the publishing machine
# (scripts/generate_plugins_manifest.py --init-key). Listing two keys lets a
# key be rotated without breaking clients that have not updated yet.
MANIFEST_PUBLIC_KEYS: List[str] = [
    "QQxWspG4TVGY8se+6+/izUXy5v+7bg2dKqnrVi/s10k=",  # ~/r2-publish/keys/plugins_manifest_ed25519.pem
]

DEFAULT_TIMEOUT = 10.0
MANIFEST_TIMEOUT = 6.0
HTTP_USER_AGENT = "ASSella-PluginManager/2.0"

# In-process memo of the last manifest result, so status checks in the UI do
# not hit the network every time. Actions (install/update buttons, deploy all,
# the background check) always refresh.
MANIFEST_MEMO_TTL = 15 * 60
UNREACHABLE_MEMO_TTL = 2 * 60
BACKGROUND_CHECK_INTERVAL = 6 * 60 * 60

# ---------------------------------------------------------------------------
# The family
# ---------------------------------------------------------------------------

# Plugins that make up the family. katamari.lua (the registry) is no longer
# shipped: every plugin creates the KATAMARI table itself and only reads
# katamari.conf.lua, which ASSella keeps writing as the shared config.
KATAMARI_FAMILY_PLUGINS = {
    "spacebunny.lua",
    "styx.lua",
    "lobster.lua",
    "lettuce.lua",
    "pineapple.lua",
}

# What "deploy all" installs, in order (interceptor first; it is resolved at
# run time because the user may have picked a different one).
FAMILY_DEPLOY_ORDER = ["styx.lua", "lobster.lua", "lettuce.lua", "pineapple.lua"]

# Shown when no manifest can be read at all.
FALLBACK_REQUIRED_PLUGINS = [
    "spacebunny.lua",
    "styx.lua",
    "lobster.lua",
]

# Retired plugins, archived to .bak wherever they are found. The manifest can
# add to this list ("retired"); these entries hold even with no manifest.
BUILTIN_RETIRED_PLUGINS: Dict[str, Optional[str]] = {
    "hubcap.lua": "pineapple.lua",
    "bypass.lua": "lettuce.lua",
    "spacebunny1.5.lua": "spacebunny.lua",
    "katamari.lua": None,              # registry no longer needed; katamari.conf.lua stays
    "bakeneko.lua": "pineapple.lua",   # renamed
}
# Not released yet: installable only when a manifest explicitly says
# "deployable": true (so old or bundled manifests cannot switch them on).
HELD_UNTIL_ENABLED: Dict[str, str] = {}

REDUNDANT_LEGACY_PLUGINS = {"spacebunny1.5.lua": "spacebunny.lua"}  # older name, kept for imports

# Role each known plugin registers with Katamari. Used when a file cannot be
# read; otherwise the role embedded in the file wins.
KNOWN_PLUGIN_ROLES: Dict[str, str] = {
    "spacebunny.lua": "mrc-provider",
    "download.lua": "download-interceptor",
    "katamari.lua": "registry",
    "bakeneko.lua": "manifest-seed",
    "pineapple.lua": "manifest-seed",
    "styx.lua": "ticket-splicer",
    "spliced-tickets.lua": "ticket-splicer",
    "lobster.lua": "config-recovery",
    "lettuce.lua": "transport",
    "bypass.lua": "transport",
    "hubcap.lua": "key-resolver",
}
INTERCEPTOR_ROLES = {"mrc-provider", "download-interceptor", "interceptor"}
TICKET_ROLES = {"ticket-splicer"}

# Kept so older imports keep working; derived from the roles above.
FALLBACK_COMPANION_PLUGINS = {f for f, r in KNOWN_PLUGIN_ROLES.items() if r not in INTERCEPTOR_ROLES}

_manifest_lock = threading.RLock()
_memo: Dict[str, Any] = {"data": None, "source": None, "at": 0.0, "unreachable_at": 0.0}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def calculate_sha256(file_path: Path) -> Optional[str]:
    """Calculate the SHA-256 checksum of a file."""
    if not file_path.is_file():
        return None
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.warning(f"[PluginManager] Could not compute SHA-256 for {file_path}: {e}")
        return None


def get_plugins_cache_dir() -> Path:
    """Local cache of downloaded plugins and the last verified manifest."""
    cache_dir = get_base_path() / "plugins"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cached_plugin_path(filename: str) -> Path:
    return get_plugins_cache_dir() / filename


def get_cached_manifest_path() -> Path:
    return get_plugins_cache_dir() / MANIFEST_FILENAME


def _known_shas_path() -> Path:
    return get_plugins_cache_dir() / "published_hashes.json"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _http_get_bytes(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
) -> bytes:
    """GET ``url`` with certificate checks always on.

    urllib first, then curl_cffi (browser TLS fingerprint, still verified),
    then the system curl (verified). There is deliberately no "insecure"
    fallback: an unverifiable connection counts as unreachable.
    """
    req_headers = {"User-Agent": HTTP_USER_AGENT}
    if headers:
        req_headers.update(headers)

    errors = []
    try:
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return resp.read()
            errors.append(f"urllib HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(url) from e
        errors.append(f"urllib {e}")
    except Exception as e:
        errors.append(f"urllib {e}")

    try:
        from curl_cffi import requests as cffi_requests

        r = cffi_requests.get(url, headers=req_headers, impersonate="chrome120", timeout=timeout, verify=True)
        if r.status_code == 200 and r.content:
            return r.content
        if r.status_code == 404:
            raise FileNotFoundError(url)
        errors.append(f"curl_cffi HTTP {r.status_code}")
    except FileNotFoundError:
        raise
    except Exception as e:
        errors.append(f"curl_cffi {e}")

    try:
        cmd = ["curl", "-sSfL", "--proto", "=https", "--max-time", str(int(timeout)), "-A", HTTP_USER_AGENT, url]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 2)
        if res.returncode == 0 and res.stdout:
            return res.stdout
        errors.append(f"curl rc={res.returncode}")
    except Exception as e:
        errors.append(f"curl {e}")

    raise ConnectionError(f"{url}: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# Manifest: signature, freshness, fetching
# ---------------------------------------------------------------------------

def _active_public_keys() -> List[bytes]:
    keys = []
    for k in MANIFEST_PUBLIC_KEYS:
        if not k or k.startswith("__"):
            continue
        try:
            raw = base64.b64decode(k)
            if len(raw) == 32:
                keys.append(raw)
        except Exception:
            logger.warning("[PluginManager] Ignoring malformed manifest public key")
    return keys


def verify_manifest_signature(manifest_bytes: bytes, signature: bytes) -> bool:
    """True when ``signature`` (raw or base64) is a valid Ed25519 signature of
    ``manifest_bytes`` by one of ``MANIFEST_PUBLIC_KEYS``."""
    keys = _active_public_keys()
    if not keys or not signature:
        return False
    sig = signature.strip()
    if len(sig) != 64:
        try:
            sig = base64.b64decode(sig, validate=True)
        except Exception:
            return False
    if len(sig) != 64:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception as e:
        logger.error(f"[PluginManager] cryptography unavailable, cannot verify plugin manifest: {e}")
        return False
    for raw in keys:
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, manifest_bytes)
            return True
        except InvalidSignature:
            continue
        except Exception as e:
            logger.debug(f"[PluginManager] Signature check error: {e}")
    return False


def _manifest_serial(data: Optional[Dict[str, Any]]) -> int:
    """Monotonic freshness number: ``serial`` if present, else ``updated_at``."""
    if not isinstance(data, dict):
        return 0
    try:
        if data.get("serial") is not None:
            return int(data["serial"])
    except (TypeError, ValueError):
        pass
    digits = re.sub(r"\D", "", str(data.get("updated_at") or ""))
    try:
        return int(digits[:14]) if digits else 0
    except ValueError:
        return 0


def _valid_manifest_shape(data: Any) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), dict):
        return False
    for fname, entry in data["plugins"].items():
        if not isinstance(entry, dict) or not str(fname).endswith(".lua"):
            return False
        sha = str(entry.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            return False
    return True


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _remember_published_hashes(data: Dict[str, Any]) -> None:
    """Keep every hash a verified manifest ever published, so the background
    updater can tell an untouched ASSella install from a user-edited file."""
    try:
        path = _known_shas_path()
        known = set((_read_json(path) or {}).get("sha256", []))
        for entry in (data.get("plugins") or {}).values():
            sha = str(entry.get("sha256") or "").lower()
            if sha:
                known.add(sha)
        _atomic_write(path, json.dumps({"sha256": sorted(known)[-500:]}, indent=0).encode())
    except Exception as e:
        logger.debug(f"[PluginManager] Could not record published hashes: {e}")


def _published_hashes() -> Set[str]:
    data = _read_json(_known_shas_path()) or {}
    return {str(s).lower() for s in data.get("sha256", [])}


def _fetch_remote_manifest() -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """Try every source. Returns (manifest, base_url, problem)."""
    cached = _read_json(get_cached_manifest_path())
    cached_serial = _manifest_serial(cached) if cached and cached.get("_verified") else 0
    problems = []
    for base in PLUGIN_SOURCES:
        url = f"{base}/{MANIFEST_FILENAME}"
        try:
            raw = _http_get_bytes(url, timeout=MANIFEST_TIMEOUT, headers={"Accept": "application/json"})
        except Exception as e:
            problems.append(f"{base}: unreachable ({e})")
            continue
        try:
            sig = _http_get_bytes(url + SIGNATURE_SUFFIX, timeout=MANIFEST_TIMEOUT)
        except Exception as e:
            problems.append(f"{base}: no signature ({e})")
            continue
        if not verify_manifest_signature(raw, sig):
            problems.append(f"{base}: signature does not verify")
            logger.warning(f"[PluginManager] Rejected plugin manifest from {base}: invalid signature")
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            problems.append(f"{base}: unreadable JSON ({e})")
            continue
        if not _valid_manifest_shape(data):
            problems.append(f"{base}: malformed manifest")
            continue
        serial = _manifest_serial(data)
        if cached_serial and serial < cached_serial:
            # A validly signed but older manifest: a replay. Keep ours.
            problems.append(f"{base}: older than the cached manifest ({serial} < {cached_serial})")
            logger.warning(f"[PluginManager] Ignoring older plugin manifest from {base}")
            continue

        stored = dict(data)
        stored["_verified"] = True
        stored["_source"] = base
        try:
            _atomic_write(get_cached_manifest_path(), json.dumps(stored, indent=2).encode("utf-8"))
            _atomic_write(get_cached_manifest_path().with_name(MANIFEST_FILENAME + SIGNATURE_SUFFIX), sig)
        except Exception as e:
            logger.debug(f"[PluginManager] Could not cache manifest: {e}")
        _remember_published_hashes(data)
        return stored, base, ""
    return None, None, "; ".join(problems)


_offline = threading.local()


class offline_manifest:
    """Context manager: inside it, manifest lookups never touch the network.

    Used to build UI (Settings -> AT0-M) instantly from the memo / cached /
    bundled manifest; the caller refreshes from the network afterwards in a
    background thread.
    """

    def __enter__(self):
        _offline.depth = getattr(_offline, "depth", 0) + 1
        return self

    def __exit__(self, *_exc):
        _offline.depth -= 1
        return False


def _local_manifest() -> Optional[Dict[str, Any]]:
    if _memo["data"] is not None:
        return _memo["data"]
    cached = _read_json(get_cached_manifest_path())
    if cached and _valid_manifest_shape(cached):
        return cached
    try:
        bundled = Paths.resource("plugins/" + MANIFEST_FILENAME)
        b = _read_json(bundled) if bundled.is_file() else None
        if b and isinstance(b.get("plugins"), dict):
            return b
    except Exception:
        pass
    return None


def fetch_plugins_manifest(force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """The plugin manifest: from the sources whenever they are reachable.

    ``force_refresh`` skips the short in-process memo. The cached copy and the
    bundled copy are fallbacks for when no source can be reached (or none
    serves a manifest that verifies). Inside ``offline_manifest()`` only the
    local copies are used.
    """
    if getattr(_offline, "depth", 0) > 0:
        return _local_manifest()
    with _manifest_lock:
        now = time.time()
        if not force_refresh:
            if _memo["data"] is not None and _memo["source"] == "remote" and now - _memo["at"] < MANIFEST_MEMO_TTL:
                return _memo["data"]
            if _memo["data"] is not None and now - _memo["unreachable_at"] < UNREACHABLE_MEMO_TTL:
                return _memo["data"]

        data, base, problem = _fetch_remote_manifest()
        if data is not None:
            _memo.update(data=data, source="remote", at=now, base=base, unreachable_at=0.0)
            return data

        logger.info(f"[PluginManager] Plugin sources unavailable, using local manifest ({problem})")
        fallback, source = None, None
        cached = _read_json(get_cached_manifest_path())
        if cached and _valid_manifest_shape(cached):
            fallback, source = cached, "cache"
        else:
            try:
                bundled = Paths.resource("plugins/" + MANIFEST_FILENAME)
                b = _read_json(bundled) if bundled.is_file() else None
                if b and isinstance(b.get("plugins"), dict):
                    fallback, source = b, "bundled"
            except Exception as e:
                logger.debug(f"[PluginManager] Bundled manifest unreadable: {e}")
        _memo.update(data=fallback, source=source, at=now, base=None, unreachable_at=now)
        return fallback


get_plugins_manifest = fetch_plugins_manifest


def get_manifest_source() -> Optional[str]:
    """'remote', 'cache', 'bundled' or None for the manifest last returned."""
    return _memo.get("source")


def get_manifest_plugin_info(filename: str) -> Optional[Dict[str, Any]]:
    manifest = fetch_plugins_manifest()
    if manifest and isinstance(manifest.get("plugins"), dict):
        return manifest["plugins"].get(filename)
    return None


def get_retired_plugins() -> Dict[str, Optional[str]]:
    """Retired filename -> replacement (or None): built-in list plus manifest."""
    retired = dict(BUILTIN_RETIRED_PLUGINS)
    manifest = _memo.get("data") or _read_json(get_cached_manifest_path()) or {}
    extra = manifest.get("retired") if isinstance(manifest, dict) else None
    if isinstance(extra, dict):
        for name, info in extra.items():
            repl = info.get("replaced_by") if isinstance(info, dict) else (info or None)
            retired[str(name)] = str(repl) if repl else None
    elif isinstance(extra, list):
        for name in extra:
            retired.setdefault(str(name), None)
    return retired


def is_plugin_deployable(filename: str) -> Tuple[bool, str]:
    """Whether the manifest allows ``filename`` to be installed, and why not."""
    if filename in get_retired_plugins():
        repl = get_retired_plugins()[filename]
        return False, f"{filename} is retired" + (f"; use {repl}" if repl else "")
    meta = get_manifest_plugin_info(filename)
    if meta is not None and meta.get("deployable") is False:
        reason = str(meta.get("disabled_reason") or "Disabled by the plugin manifest.")
        return False, reason
    if filename in HELD_UNTIL_ENABLED and not (meta and meta.get("deployable") is True):
        return False, HELD_UNTIL_ENABLED[filename]
    return True, ""


# ---------------------------------------------------------------------------
# Embedded plugin metadata (version / role)
# ---------------------------------------------------------------------------

_KATAMARI_BLOCK_RE = re.compile(
    r"KATAMARI\.plugins(?:\.(\w+)|\[\s*[\"'](\w+)[\"']\s*\])\s*=\s*\{(.*?)\}", re.S
)


def read_plugin_meta(content_or_path: Union[str, Path, bytes]) -> Dict[str, Optional[str]]:
    """The metadata a plugin carries itself: its Katamari id and role, and its
    version. Plugins without a Katamari registration are classified by the
    Steam functions they hook."""
    try:
        if isinstance(content_or_path, Path):
            content = content_or_path.read_text(encoding="utf-8", errors="ignore") if content_or_path.is_file() else ""
        elif isinstance(content_or_path, bytes):
            content = content_or_path.decode("utf-8", errors="ignore")
        else:
            content = str(content_or_path)
    except Exception:
        content = ""
    meta: Dict[str, Optional[str]] = {"id": None, "role": None, "version": extract_plugin_version(content)}
    # Ignore comments: katamari.lua documents the registration shape in one.
    content = "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("--"))
    m = _KATAMARI_BLOCK_RE.search(content)
    if m:
        meta["id"] = m.group(1) or m.group(2)
        r = re.search(r"\brole\s*=\s*[\"']([\w\-]+)[\"']", m.group(3))
        if r:
            meta["role"] = r.group(1)
    if not meta["role"] and content:
        if re.search(r"GetPackage|GetManifestRequestCode|GetMRC\b|GetBinary", content):
            meta["role"] = "download-interceptor"
        elif "GetAppOwnershipTicket" in content:
            meta["role"] = "ticket-splicer"
    return meta


def _find_plugin_file(filename: str) -> Optional[Path]:
    for d in get_sls_plugins_dirs():
        p = d / filename
        if p.is_file() and p.stat().st_size > 0:
            return p
    p = get_cached_plugin_path(filename)
    return p if p.is_file() else None


def get_plugin_role(filename: str) -> Optional[str]:
    """Role of a plugin: embedded in the file, else manifest, else known table."""
    path = _find_plugin_file(filename)
    if path is not None:
        role = read_plugin_meta(path).get("role")
        if role:
            return role
    meta = get_manifest_plugin_info(filename) or {}
    if meta.get("role"):
        return str(meta["role"])
    if meta.get("category") == "interceptor":
        return "interceptor"
    if meta.get("category") == "companion":
        return "companion"
    return KNOWN_PLUGIN_ROLES.get(filename)


def is_interceptor_plugin(filename: str) -> bool:
    """A download interceptor (only one may be active at a time)."""
    if filename.endswith(".bak") or filename.startswith(".") or not filename.endswith(".lua"):
        return False
    return get_plugin_role(filename) in INTERCEPTOR_ROLES


def is_ticket_splicer(filename: str) -> bool:
    return get_plugin_role(filename) in TICKET_ROLES


def get_companion_plugin_filenames() -> Set[str]:
    """Known non-interceptor plugins (from the role table and the manifest)."""
    companions = set(FALLBACK_COMPANION_PLUGINS)
    manifest = _memo.get("data") or {}
    for fname, entry in (manifest.get("plugins") or {}).items():
        if isinstance(entry, dict):
            role = entry.get("role")
            if (role and role not in INTERCEPTOR_ROLES) or entry.get("category") == "companion":
                companions.add(fname)
    return companions


def extract_plugin_version(content_or_path: Union[str, Path, bytes]) -> Optional[str]:
    """Extract semantic version string from Lua file content or path."""
    try:
        if isinstance(content_or_path, Path):
            if not content_or_path.is_file():
                return None
            content = content_or_path.read_text(encoding="utf-8", errors="ignore")
        elif isinstance(content_or_path, bytes):
            content = content_or_path.decode("utf-8", errors="ignore")
        else:
            content = str(content_or_path)

        m = re.search(r'\bVERSION\s*=\s*(?:\w+\.VERSION\s+or\s+)?["\']([^"\']+)["\']', content, re.IGNORECASE)
        if m:
            sem = re.search(r'(\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9_\.\-]+)?)', m.group(1))
            if sem:
                return sem.group(1)
        m2 = re.search(r'^--\s*[\w\.\-]+\.lua\s*\((v?\d+(?:\.\d+)*(?:[a-zA-Z0-9_\.\-]+)?)', content, re.MULTILINE | re.IGNORECASE)
        if m2:
            return m2.group(1).lstrip("v")
        m3 = re.search(r'^--\s*(?:version|ver|v)\s*[:=]?\s*v?(\d+\.\d+(?:\.\d+)*(?:[a-zA-Z0-9_\.\-]+)?)', content, re.MULTILINE | re.IGNORECASE)
        if m3:
            return m3.group(1)
        m4 = re.search(r'^--\s*[\w\.\-]+\.lua\s+v?(\d+\.\d+(?:\.\d+)*(?:[a-zA-Z0-9_\.\-]+)?)', content, re.MULTILINE | re.IGNORECASE)
        if m4:
            return m4.group(1)
    except Exception as exc:
        logger.debug(f"[PluginManager] Error extracting plugin version: {exc}")
    return None


def get_plugin_version(filename: str) -> Optional[str]:
    """Version of a plugin: the installed file first, then the manifest, the
    cache, and the bundled copy."""
    path = _find_plugin_file(filename)
    if path is not None:
        v = extract_plugin_version(path)
        if v:
            return v
    info = get_manifest_plugin_info(filename)
    if info and info.get("version"):
        return str(info["version"]).strip()
    try:
        bundled = Paths.resource(f"plugins/{filename}")
        if bundled.is_file():
            return extract_plugin_version(bundled)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Getting plugin bytes
# ---------------------------------------------------------------------------

def _local_candidates(filename: str) -> Iterable[Path]:
    candidates = [get_cached_plugin_path(filename)]
    try:
        candidates.append(Paths.resource(f"plugins/{filename}"))
    except Exception:
        pass
    seen = set()
    for p in candidates:
        try:
            if p.is_file() and p not in seen:
                seen.add(p)
                yield p
        except Exception:
            continue


def download_plugin(
    filename: str, expected_sha256: Optional[str] = None
) -> Tuple[bool, Optional[Path], str]:
    """Put a verified copy of ``filename`` in the local cache.

    With the sources reachable the file is always downloaded and must match
    ``expected_sha256``. Local copies are used only when no source can be
    reached, and then only if their hash matches.
    """
    cache_path = get_cached_plugin_path(filename)
    expected = (expected_sha256 or "").lower() or None
    meta = get_manifest_plugin_info(filename) or {}
    rel_path = str(meta.get("path") or filename).lstrip("/")

    unreachable = True
    if expected:
        for base in PLUGIN_SOURCES:
            url = f"{base}/{rel_path}"
            try:
                content = _http_get_bytes(url, timeout=DEFAULT_TIMEOUT)
            except FileNotFoundError:
                unreachable = False
                logger.warning(f"[PluginManager] {url} not found")
                continue
            except Exception as e:
                logger.info(f"[PluginManager] {base} unreachable for {filename}: {e}")
                continue
            unreachable = False
            got = hashlib.sha256(content).hexdigest()
            if got != expected:
                logger.error(f"[PluginManager] SHA-256 mismatch for {filename} from {base}: expected {expected[:12]}, got {got[:12]}")
                continue
            _atomic_write(cache_path, content)
            logger.info(f"[PluginManager] Downloaded {filename} ({len(content)} bytes) from {base}")
            return True, cache_path, f"Downloaded {filename}."
    else:
        logger.info(f"[PluginManager] No published hash for {filename}; not downloading it")

    # No verified download (sources unreachable, file missing on the source, or
    # the source's copy does not match the manifest in use - e.g. the bucket has
    # no signed manifest yet, so the bundled manifest is in use): fall back to a
    # local copy, but only one whose hash matches that manifest.
    for cand in _local_candidates(filename):
        if expected and calculate_sha256(cand) != expected:
            continue
        if cand != cache_path:
            shutil.copy2(cand, cache_path)
        why = "plugin sources unreachable" if unreachable else "no verified copy on the plugin sources"
        logger.info(f"[PluginManager] Using local copy of {filename} ({cand}); {why}")
        return True, cache_path, f"Using local copy of {filename}."
    if not unreachable:
        return False, None, f"Could not download a verified copy of {filename}."
    return False, None, f"{filename} could not be downloaded and no matching local copy exists."


# ---------------------------------------------------------------------------
# State of installed plugins
# ---------------------------------------------------------------------------

def is_plugin_installed_and_valid(filename: str, expected_sha256: Optional[str] = None) -> bool:
    """Installed in the primary SLSsteam plugins directory with the published hash."""
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False
    primary_file = target_dirs[0] / filename
    try:
        if not primary_file.is_file() or primary_file.stat().st_size == 0:
            return False
    except OSError:
        return False
    if expected_sha256 is None:
        info = get_manifest_plugin_info(filename)
        if info and "sha256" in info:
            expected_sha256 = info["sha256"]
    if expected_sha256:
        return calculate_sha256(primary_file) == str(expected_sha256).lower()
    return True


def is_plugin_check_bypassed() -> bool:
    """The user's 'I'm using custom plugins (Advanced)' switch."""
    try:
        return get_settings().value("custom_plugins_advanced", False, type=bool)
    except Exception:
        return False


def _archive(path: Path, reason: str) -> Optional[str]:
    bak = path.with_name(path.name + ".bak")
    try:
        if bak.is_file():
            bak.unlink()
        path.replace(bak)
        logger.info(f"[PluginManager] {reason}: {path.name} -> {bak.name}")
        return str(bak)
    except Exception as e:
        logger.warning(f"[PluginManager] Could not archive {path}: {e}")
        return None


def enforce_plugin_policy(deploy_replacements: bool = True) -> List[str]:
    """Move retired plugins, and plugins the manifest disabled, to ``.bak``.

    Runs on whatever manifest is at hand (no network). Skipped entirely when
    the user runs custom plugins.
    """
    if is_plugin_check_bypassed():
        return []
    archived: List[str] = []
    retired = get_retired_plugins()
    manifest = _memo.get("data") or _read_json(get_cached_manifest_path()) or {}
    entries = manifest.get("plugins") or {}
    replacements = set()
    for tdir in get_sls_plugins_dirs():
        if not tdir.is_dir():
            continue
        for p in tdir.glob("*.lua"):
            if not p.is_file():
                continue
            if p.name in retired:
                if _archive(p, "Archived retired plugin"):
                    archived.append(str(p))
                    if retired[p.name]:
                        replacements.add(retired[p.name])
            elif isinstance(entries.get(p.name), dict) and entries[p.name].get("deployable") is False:
                if _archive(p, "Archived plugin disabled by the manifest"):
                    archived.append(str(p))
    if deploy_replacements:
        for repl in replacements:
            ok, _ = is_plugin_deployable(repl)
            if ok and get_plugin_status(repl) == "missing" and not is_plugin_user_disabled(repl):
                deploy_plugin(repl, only_if_missing=True)
    return archived


def archive_redundant_legacy_plugins(auto_deploy_replacement: bool = True) -> List[str]:
    """Older name for :func:`enforce_plugin_policy`."""
    return enforce_plugin_policy(deploy_replacements=auto_deploy_replacement)


def get_active_interceptor_filename() -> Optional[str]:
    """The active download interceptor in the SLSsteam plugins directory."""
    try:
        retired = get_retired_plugins()
        for tdir in get_sls_plugins_dirs():
            if not tdir.is_dir():
                continue
            for p in sorted(tdir.glob("*.lua")):
                if p.is_file() and p.name not in retired and is_interceptor_plugin(p.name):
                    return p.name
    except Exception as e:
        logger.debug(f"[PluginManager] Error detecting active interceptor: {e}")
    return None


get_active_download_plugin = get_active_interceptor_filename


def are_all_plugins_installed(ignore_bypass: bool = False) -> bool:
    """Active interceptor and a ticket splicer installed with published hashes."""
    if not ignore_bypass and is_plugin_check_bypassed():
        return True
    active_interceptor = (
        get_active_interceptor_filename()
        or get_settings().value("selected_download_plugin", "")
        or get_default_interceptor_from_manifest()
    )
    if not is_plugin_installed_and_valid(active_interceptor):
        return False
    return is_plugin_installed_and_valid("styx.lua") or is_plugin_installed_and_valid("spliced-tickets.lua")


def are_plugins_present(ignore_bypass: bool = False) -> bool:
    """An interceptor and a ticket splicer exist in the plugins directory."""
    if not ignore_bypass and is_plugin_check_bypassed():
        return True
    has_interceptor = has_splicer = False
    for tdir in get_sls_plugins_dirs():
        if not tdir.is_dir():
            continue
        for p in tdir.glob("*.lua"):
            if p.name.startswith("."):
                continue
            if is_ticket_splicer(p.name):
                has_splicer = True
            elif is_interceptor_plugin(p.name):
                has_interceptor = True
    return has_interceptor and has_splicer


def get_plugin_status(filename: str) -> str:
    """
    State of a plugin relative to the manifest:
      - 'disabled': the manifest does not allow it (deployable: false / retired).
      - 'missing': not installed.
      - 'outdated': installed, but not the published file.
      - 'up_to_date': installed and identical to the published file.
    """
    deployable, _ = is_plugin_deployable(filename)
    if not deployable:
        return "disabled"
    found_file = None
    for tdir in get_sls_plugins_dirs():
        candidate = tdir / filename
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                found_file = candidate
                break
        except OSError:
            continue
    if not found_file:
        return "missing"
    info = get_manifest_plugin_info(filename)
    if not info or "sha256" not in info:
        return "up_to_date"
    if (calculate_sha256(found_file) or "").lower() == str(info["sha256"]).lower():
        return "up_to_date"
    return "outdated"


get_plugin_state = get_plugin_status


def _record_deployed_sha(filename: str, sha: str) -> None:
    try:
        settings = get_settings()
        settings.setValue(f"plugin_deployed_sha/{filename}", sha)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Deploying
# ---------------------------------------------------------------------------

def deploy_plugin(filename: str, force_download: bool = False, only_if_missing: bool = False) -> Tuple[bool, bool, str]:
    """Install the published ``filename`` into every SLSsteam plugins directory.

    An existing different copy is kept as ``<name>.bak``. Installing an
    interceptor moves any other interceptor to ``.bak``; installing a plugin
    moves the retired plugins it replaces to ``.bak``.

    Returns (success, skipped, message); skipped means nothing needed doing.
    ``force_download`` is accepted for older callers (downloads always happen
    when the sources are reachable).
    """
    ensure_sls_plugin_config_sections()

    deployable, why = is_plugin_deployable(filename)
    if not deployable:
        return False, False, f"{filename} cannot be installed: {why}"

    if filename in ("spliced-tickets.lua", "styx.lua") or is_ticket_splicer(filename):
        ensure_smart_tickets_enabled()

    set_plugin_user_disabled(filename, False)

    if only_if_missing and get_plugin_status(filename) != "missing":
        return True, True, f"{filename} is already present; preserving local version."

    meta = get_manifest_plugin_info(filename)
    expected_sha256 = (meta or {}).get("sha256")
    if meta is None and get_manifest_source() == "remote":
        return False, False, f"{filename} is not in the plugin manifest."

    ok, cache_path, dl_msg = download_plugin(filename, expected_sha256)
    if not ok or cache_path is None:
        return False, False, f"Failed getting {filename}: {dl_msg}"

    src_hash = calculate_sha256(cache_path)
    if not src_hash:
        return False, False, f"Could not compute hash for cached {filename}"

    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False, False, "No valid SLSsteam plugin directories found."

    is_interceptor = get_plugin_role(filename) in INTERCEPTOR_ROLES or (
        (meta or {}).get("category") == "interceptor"
    )
    replaced = {old for old, new in get_retired_plugins().items() if new == filename}
    if filename == "styx.lua":
        replaced.add("spliced-tickets.lua")

    any_deployed = False
    errors = []
    for tdir in target_dirs:
        try:
            tdir.mkdir(parents=True, exist_ok=True)
            for old in replaced:
                old_path = tdir / old
                if old_path.is_file():
                    _archive(old_path, f"Archived {old}, replaced by {filename}")

            if is_interceptor:
                stale_bak = tdir / f"{filename}.bak"
                if stale_bak.is_file():
                    try:
                        stale_bak.unlink()
                    except Exception as e:
                        logger.warning(f"[PluginManager] Could not remove old backup {stale_bak}: {e}")
                for existing in tdir.glob("*.lua"):
                    if existing.name != filename and existing.is_file() and is_interceptor_plugin(existing.name):
                        _archive(existing, "Deactivated alternative interceptor")

            dst_file = tdir / filename
            if dst_file.is_file():
                if dst_file.stat().st_size > 0:
                    if calculate_sha256(dst_file) == src_hash:
                        continue
                    bak_file = tdir / f"{filename}.bak"
                    try:
                        if bak_file.is_file():
                            bak_file.unlink()
                        shutil.copy2(dst_file, bak_file)
                        logger.info(f"[PluginManager] Kept previous {filename} as {bak_file.name}")
                    except Exception as bak_err:
                        logger.warning(f"[PluginManager] Failed creating backup {bak_file}: {bak_err}")
                else:
                    logger.info(f"[PluginManager] Overwriting 0-byte stub for {filename} at {dst_file}")

            shutil.copy2(cache_path, dst_file)
            any_deployed = True
            logger.info(f"[PluginManager] Deployed {filename} to {dst_file}")
        except Exception as exc:
            errors.append(f"{tdir}: {exc}")

    if errors:
        return False, False, f"Error deploying {filename}: {'; '.join(errors)}"

    _record_deployed_sha(filename, src_hash)
    if is_interceptor:
        try:
            get_settings().setValue("selected_download_plugin", filename)
        except Exception as exc:
            logger.debug(f"[PluginManager] Could not save selected_download_plugin setting: {exc}")

    if not any_deployed:
        return True, True, f"{filename} is already up to date."
    return True, False, f"Successfully deployed {filename} to SLSsteam."


def undeploy_plugin(filename: str) -> Tuple[bool, str]:
    """Remove a plugin from all SLSsteam plugin directories while preserving any .bak backup.
    Also records the plugin as user-disabled in settings so startup does not auto-redeploy it.
    """
    set_plugin_user_disabled(filename, True)
    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return False, "No valid SLSsteam plugin directories found."

    errors = []
    for tdir in target_dirs:
        try:
            target_file = tdir / filename
            if target_file.is_file():
                target_file.unlink()
                logger.info(f"[PluginManager] Removed plugin {target_file}")
        except Exception as exc:
            errors.append(f"{tdir}: {exc}")

    if errors:
        return False, f"Errors undeploying {filename}: {'; '.join(errors)}"
    return True, f"Successfully removed {filename}."


def is_plugin_user_disabled(filename: str) -> bool:
    """Check if the user has explicitly disabled this plugin in settings."""
    try:
        disabled_list = get_settings().value("disabled_plugins", [], type=list)
        return filename in (disabled_list or [])
    except Exception:
        return False


def set_plugin_user_disabled(filename: str, disabled: bool) -> None:
    """Record whether a plugin has been explicitly disabled by user."""
    try:
        settings = get_settings()
        disabled_list = list(settings.value("disabled_plugins", [], type=list) or [])
        if disabled and filename not in disabled_list:
            disabled_list.append(filename)
        elif not disabled and filename in disabled_list:
            disabled_list = [f for f in disabled_list if f != filename]
        settings.setValue("disabled_plugins", disabled_list)
        settings.sync()
    except Exception as exc:
        logger.warning(f"[PluginManager] Could not update disabled_plugins setting: {exc}")


def get_default_interceptor_from_manifest() -> str:
    """The manifest's default interceptor, falling back to spacebunny.lua."""
    try:
        manifest = fetch_plugins_manifest()
        if manifest:
            if manifest.get("default_interceptor"):
                return str(manifest["default_interceptor"]).strip()
            for fname, pdata in (manifest.get("plugins") or {}).items():
                if isinstance(pdata, dict) and (pdata.get("role") in INTERCEPTOR_ROLES or pdata.get("category") == "interceptor"):
                    if pdata.get("is_default") or pdata.get("default") or pdata.get("required"):
                        return fname
    except Exception as exc:
        logger.debug(f"[PluginManager] Error resolving default interceptor from manifest: {exc}")
    return "spacebunny.lua"


def deploy_all_plugins(force_download: bool = False, only_if_missing: bool = False) -> Tuple[bool, List[str]]:
    """Install the family: the interceptor, Styx, Katamari, Lobster, Lettuce,
    and Bakeneko when the manifest allows it.

    The interceptor and Styx are always installed; the others are skipped when
    the user removed them, or when the manifest marks them not deployable.
    """
    ensure_sls_plugin_config_sections()
    fetch_plugins_manifest(force_refresh=True)
    if not only_if_missing:
        # Explicit "deploy all" from the user: retire old plugins too.
        enforce_plugin_policy(deploy_replacements=False)

    retired = get_retired_plugins()
    active_interceptor = get_active_interceptor_filename() or get_settings().value("selected_download_plugin", "") or ""
    if not active_interceptor or active_interceptor in retired:
        active_interceptor = get_default_interceptor_from_manifest()

    results: List[str] = []
    overall_ok = True
    for name in [active_interceptor] + FAMILY_DEPLOY_ORDER:
        essential = name in (active_interceptor, "styx.lua")
        if not essential:
            deployable, why = is_plugin_deployable(name)
            if not deployable:
                logger.debug(f"[PluginManager] Skipping {name}: {why}")
                continue
            if is_plugin_user_disabled(name):
                logger.debug(f"[PluginManager] {name} removed by the user, skipping.")
                continue
        ok, _skipped, msg = deploy_plugin(name, only_if_missing=only_if_missing)
        results.append(msg)
        if not ok and essential:
            overall_ok = False
        elif not ok:
            logger.warning(f"[PluginManager] {msg}")
    return overall_ok, results


def autodeploy_lobster_if_needed() -> bool:
    """Deprecated: a missing Lobster is now offered in the startup plugin
    check (plan_plugin_maintenance) instead of being installed silently."""
    return False


def sync_plugins_if_enabled() -> bool:
    """Kept for older callers; the background check is run_plugin_maintenance()."""
    return True


def _snoozed(settings, key: str, fingerprint: str) -> bool:
    return str(settings.value(f"plugin_prompt_snooze/{key}", "") or "") == fingerprint


def plan_plugin_maintenance(force: bool = False) -> List[Dict[str, Any]]:
    """What the startup plugin check would change, without changing anything.

    Each action: {"id", "kind", "plugin", "title", "detail", "default"} where
    kind is "archive" (retired / disabled by the manifest), "install" (a
    family plugin that is missing), or "update" (a newer published version;
    files the user edited are offered but unticked). The user decides; see
    apply_plugin_actions().
    """
    if is_plugin_check_bypassed():
        return []
    try:
        from utils.yaml_config_manager import is_at0m_enabled
        if not is_at0m_enabled():
            return []
    except Exception:
        return []

    settings = get_settings()
    now = time.time()
    try:
        last = float(settings.value("plugin_manifest_last_check", 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    if force or now - last >= BACKGROUND_CHECK_INTERVAL:
        fetch_plugins_manifest(force_refresh=True)
        if get_manifest_source() == "remote":
            settings.setValue("plugin_manifest_last_check", now)
    else:
        fetch_plugins_manifest()

    actions: List[Dict[str, Any]] = []
    retired = get_retired_plugins()
    manifest = _memo.get("data") or {}
    entries = manifest.get("plugins") or {}
    seen = set()
    for tdir in get_sls_plugins_dirs():
        if not tdir.is_dir():
            continue
        for p in sorted(tdir.glob("*.lua")):
            if not p.is_file() or p.name in seen:
                continue
            seen.add(p.name)
            if p.name in retired:
                repl = retired[p.name]
                actions.append({
                    "id": f"archive:{p.name}", "kind": "archive", "plugin": p.name, "default": True,
                    "title": f"Retire {p.name}" + (f" (replaced by {repl})" if repl else ""),
                    "detail": "It is moved to .bak, not deleted.",
                })
            elif isinstance(entries.get(p.name), dict) and entries[p.name].get("deployable") is False:
                actions.append({
                    "id": f"archive:{p.name}", "kind": "archive", "plugin": p.name, "default": True,
                    "title": f"Turn off {p.name}",
                    "detail": str(entries[p.name].get("disabled_reason") or "Disabled by the plugin manifest.")
                              + " It is moved to .bak, not deleted.",
                })

    published = _published_hashes()
    for name in sorted(KATAMARI_FAMILY_PLUGINS):
        if is_plugin_user_disabled(name) or not is_plugin_deployable(name)[0]:
            continue
        status = get_plugin_status(name)
        info = entries.get(name) or {}
        ver = info.get("version")
        if status == "missing" and name in ("lobster.lua",) + tuple(FALLBACK_REQUIRED_PLUGINS):
            actions.append({
                "id": f"install:{name}", "kind": "install", "plugin": name, "default": True,
                "title": f"Install {info.get('name') or name}" + (f" {ver}" if ver else ""),
                "detail": str(info.get("description") or "Part of the plugin family, not installed."),
            })
        elif status == "outdated" and get_manifest_source() == "remote":
            path = _find_plugin_file(name)
            local_sha = (calculate_sha256(path) or "").lower() if path else ""
            recorded = str(settings.value(f"plugin_deployed_sha/{name}", "") or "").lower()
            edited = not (local_sha and (local_sha in published or local_sha == recorded))
            actions.append({
                "id": f"update:{name}", "kind": "update", "plugin": name, "default": not edited,
                "title": f"Update {info.get('name') or name}" + (f" to {ver}" if ver else ""),
                "detail": ("Your copy has local changes. " if edited else "")
                          + "The current file is kept as .bak.",
            })

    # Drop what the user already declined for exactly this situation.
    fingerprint = str(manifest.get("serial") or manifest.get("updated_at") or "")
    return [a for a in actions if not _snoozed(settings, a["id"], fingerprint)]


def snooze_plugin_actions(actions: List[Dict[str, Any]]) -> None:
    """Remember that the user declined these, until the manifest changes."""
    settings = get_settings()
    manifest = _memo.get("data") or {}
    fingerprint = str(manifest.get("serial") or manifest.get("updated_at") or "")
    for a in actions:
        settings.setValue(f"plugin_prompt_snooze/{a['id']}", fingerprint)


def apply_plugin_actions(actions: List[Dict[str, Any]]) -> List[str]:
    """Carry out the actions the user ticked. Returns one message per action."""
    results = []
    for a in actions:
        name = a["plugin"]
        if a["kind"] == "archive":
            done = False
            for tdir in get_sls_plugins_dirs():
                p = tdir / name
                if p.is_file() and _archive(p, "Archived on request"):
                    done = True
            results.append(f"{name}: moved to .bak" if done else f"{name}: nothing to move")
        else:
            ok, _skipped, msg = deploy_plugin(name)
            results.append(msg)
    return results


def run_plugin_maintenance(force: bool = False) -> Dict[str, Any]:
    """Non-interactive check for scripts and tests: plans, then applies only the
    safe defaults. ASSella itself asks the user (plan + apply)."""
    actions = [a for a in plan_plugin_maintenance(force=force) if a["default"]]
    report: Dict[str, Any] = {"checked": True, "archived": [], "updated": [], "skipped": []}
    for a, msg in zip(actions, apply_plugin_actions(actions)):
        if a["kind"] == "archive":
            report["archived"].append(a["plugin"])
        else:
            report["updated"].append(a["plugin"])
    return report


def detect_non_katamari_plugins() -> List[Path]:
    """Return active .lua files in SLSsteam plugin directories that are outside the Katamari family."""
    if is_plugin_check_bypassed():
        return []

    target_dirs = get_sls_plugins_dirs()
    if not target_dirs:
        return []

    active_interceptor = get_active_interceptor_filename() or ""
    retired = get_retired_plugins()  # these are archived automatically
    foreign: List[Path] = []
    for tdir in target_dirs:
        if not tdir.is_dir():
            continue
        for p in tdir.glob("*.lua"):
            if not p.is_file() or p.name.endswith(".bak") or p.name.startswith("."):
                continue
            # If standard download.lua is active intentionally, don't flag download.lua
            if p.name == "download.lua" and active_interceptor == "download.lua":
                continue
            if p.name not in KATAMARI_FAMILY_PLUGINS and p.name not in retired:
                foreign.append(p)
    return foreign


def archive_non_katamari_plugins() -> List[str]:
    """Archive any non-Katamari plugins and legacy configs/tickets to .bak."""
    archived = []
    target_dirs = get_sls_plugins_dirs()

    # 1. Archive foreign .lua files
    for tdir in target_dirs:
        if not tdir.is_dir():
            continue
        for p in tdir.glob("*.lua"):
            if not p.is_file() or p.name.endswith(".bak") or p.name.startswith("."):
                continue
            if p.name not in KATAMARI_FAMILY_PLUGINS:
                bak_p = tdir / f"{p.name}.bak"
                try:
                    if bak_p.is_file():
                        bak_p.unlink()
                    p.replace(bak_p)
                    archived.append(str(bak_p))
                    logger.info(f"[PluginManager] Archived foreign plugin {p.name} -> {bak_p.name}")
                except Exception as e:
                    logger.warning(f"[PluginManager] Could not archive {p}: {e}")

        # If styx.lua is present, archive spliced-tickets.lua to .bak
        styx_file = tdir / "styx.lua"
        spliced_file = tdir / "spliced-tickets.lua"
        if styx_file.is_file() and spliced_file.is_file():
            spliced_bak = tdir / "spliced-tickets.lua.bak"
            try:
                if spliced_bak.is_file():
                    spliced_bak.unlink()
                spliced_file.replace(spliced_bak)
                archived.append(str(spliced_bak))
                logger.info(f"[PluginManager] Archived legacy spliced-tickets.lua -> {spliced_bak.name}")
            except Exception as e:
                logger.warning(f"[PluginManager] Could not archive spliced-tickets.lua: {e}")

    # 2. Archive legacy mrc_config.lua
    try:
        from managers.katamari_config_manager import KatamariConfigManager
        if KatamariConfigManager.archive_legacy_mrc_config():
            archived.append("mrc_config.lua.bak")
    except Exception as e:
        logger.debug(f"[PluginManager] Error archiving mrc_config.lua: {e}")

    return archived


def check_and_prompt_legacy_plugins(parent_window=None) -> bool:
    """Prompt user once per ASSella version if unrecognized/legacy plugins exist."""
    if is_plugin_check_bypassed():
        return False

    settings = get_settings()
    is_atom = settings.value("enable_at0m", settings.value("enable_vapor", True, type=bool), type=bool)
    if not is_atom:
        return False

    foreign_files = detect_non_katamari_plugins()
    if not foreign_files:
        return False

    from utils.version import app_version, extract_semver
    clean_ver = extract_semver(app_version) or "current"
    flag_key = f"legacy_plugins_prompted_{clean_ver}"
    if settings.value(flag_key, False, type=bool):
        return False

    try:
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
        from PyQt6.QtCore import Qt

        dlg = QDialog(parent_window)
        dlg.setWindowTitle("SLSsteam Plugins — Katamari Suite")
        dlg.setFixedWidth(520)
        dlg.setStyleSheet("background-color: #1A1D24; color: #FFFFFF;")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title_lbl = QLabel("Legacy or Foreign Plugins Detected")
        title_lbl.setStyleSheet("font-size: 11pt; font-weight: bold; color: #64B5F6;")
        layout.addWidget(title_lbl)

        names_list = "\n".join(f"  • {p.name}" for p in foreign_files[:8])
        if len(foreign_files) > 8:
            names_list += f"\n  • ...and {len(foreign_files) - 8} more"

        body_lbl = QLabel(
            "ASSella detected plugins in your SLSsteam plugins directory that are outside the Katamari family:\n\n"
            f"{names_list}\n\n"
            "The Katamari family (SpaceBunny, Styx, Katamari, Lobster, Lettuce) is the unified suite for manifest "
            "resolution and ticket splicing.\n\n"
            "Choose 'Fix' to safely deactivate foreign plugins (.lua.bak), or 'Use Custom' to preserve them without interference."
        )
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet("font-size: 9pt; color: rgba(255, 255, 255, 0.85); line-height: 1.4;")
        layout.addWidget(body_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        fix_btn = QPushButton("Fix (Deactivate to .bak)")
        fix_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fix_btn.setStyleSheet("""
            QPushButton {
                background-color: #2E7D32;
                color: #FFFFFF;
                border: 1px solid #4CAF50;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 9pt;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #388E3C;
            }
        """)

        custom_btn = QPushButton("Use Custom Plugins")
        custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        custom_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.25);
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.16);
            }
        """)

        def _on_fix():
            archive_non_katamari_plugins()
            settings.setValue(flag_key, True)
            settings.sync()
            dlg.accept()

        def _on_custom():
            settings.setValue("custom_plugins_advanced", True)
            settings.setValue(flag_key, True)
            settings.sync()
            dlg.reject()

        fix_btn.clicked.connect(_on_fix)
        custom_btn.clicked.connect(_on_custom)

        btn_row.addStretch()
        btn_row.addWidget(custom_btn)
        btn_row.addWidget(fix_btn)
        layout.addLayout(btn_row)

        dlg.exec()
        return True
    except Exception as exc:
        logger.warning(f"[PluginManager] Error showing legacy plugins prompt: {exc}")
        return False
