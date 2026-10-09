"""Optional component manager: on-demand Goldberg / Steamless / SLScheevo.

These components are large binaries that most users never need, so they are
kept out of the AppImage and fetched from Cloudflare R2 on first use. A manifest
carries a SHA-256 per component so we can tell whether a local copy is current
or corrupt, and a corrupt or partially-extracted install is discarded rather
than trusted.

Install location
----------------
Components are installed into the *same* directory every existing code path
already reads from, ``Paths.deps(...)``, so no call site needs to change. That
directory differs per environment, so it is resolved once at import:

* Unpackaged / development - ``src/deps`` inside the repo, writable, so the
  component sits next to the rest of the bundled deps.
* Packaged AppImage - ``Paths.BASE_DIR`` is ``sys._MEIPASS``, a throwaway mount
  that disappears when the app exits. Writing there would mean re-downloading
  35 MB on every launch, so the deps root is redirected to a persistent
  location under the ACCELA data dir and ``Paths.DEPS`` is repointed at it.

``Paths.DEPS`` is repointed rather than given a second path, precisely so that
``Paths.deps("Goldberg")`` and :func:`get_install_dir` can never disagree -
they did once, which made downloads land where nothing looked for them.

State layout (in the ACCELA data dir):
    <base>/components/.state.json   {key: sha256} of what is currently installed
"""

import hashlib
import json
import logging
import os
import shutil
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from utils.helpers import get_base_path
from utils.paths import Paths

logger = logging.getLogger(__name__)

R2_BASE_URL = "https://pub-19657b4f385d424b91a909253efdb29c.r2.dev"
MANIFEST_FILENAME = "components_manifest.json"
MANIFEST_URL = f"{R2_BASE_URL}/{MANIFEST_FILENAME}"

DEFAULT_TIMEOUT = 30.0
HTTP_USER_AGENT = "ASSella-ComponentManager/1.0"

# Matches the escape hatch used for Hubcap TLS: verification is on by default.
_state_lock = threading.Lock()


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
def _is_writable_dir(path: Path) -> bool:
    """True when ``path`` exists (or can be created) and accepts writes."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = path / f".assella_write_test_{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def ensure_deps_root() -> Path:
    """Ensure ``Paths.DEPS`` is a persistent, writable directory.

    Returns the resolved deps root. Idempotent - safe to call repeatedly.
    """
    current = Paths.DEPS
    if _is_writable_dir(current):
        return current

    # Packaged and read-only (or otherwise unwritable): move the whole deps
    # root somewhere that survives between launches.
    fallback = Path(get_base_path()) / "deps"
    if _is_writable_dir(fallback):
        logger.info(
            "[ComponentManager] %s is not writable; using %s for optional "
            "components instead.", current, fallback,
        )
        Paths.DEPS = fallback
        return fallback

    # Nothing is writable. Leave Paths.DEPS alone; downloads will fail with a
    # clear error rather than silently going somewhere unreadable.
    logger.warning(
        "[ComponentManager] No writable deps directory found (tried %s and %s); "
        "optional components cannot be installed.", current, fallback,
    )
    return current


def get_components_root() -> Path:
    return Path(get_base_path()) / "components"


def get_state_file() -> Path:
    return get_components_root() / ".state.json"


def _install_dir_name(key: str) -> str:
    return {"goldberg": "Goldberg", "steamless": "Steamless", "slscheevo": "SLScheevo"}.get(
        key, key.capitalize()
    )


def get_install_dir(key: str) -> Path:
    """Where a component's files live once installed.

    Deliberately derived from ``Paths.deps`` so it can never drift away from
    the paths the rest of the codebase reads.
    """
    return Paths.deps(_install_dir_name(key))


# Resolve once at import so every later call agrees, including call sites that
# imported Paths before this module was loaded.
ensure_deps_root()


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------
def _tls_verify_enabled() -> bool:
    try:
        from utils.settings import get_settings
        return not get_settings().value("hubcap_disable_tls_verify", False, type=bool)
    except Exception:
        return True


def _http_get(url: str, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    ctx = None
    if url.startswith("https") and not _tls_verify_enabled():
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
    if ctx is not None:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return r.read()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_manifest() -> Optional[dict]:
    """Download and parse components_manifest.json. Returns None on failure."""
    try:
        raw = _http_get(MANIFEST_URL, timeout=15.0)
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data.get("components"), dict):
            return data
        logger.warning("[ComponentManager] Manifest has no 'components' object")
    except Exception as e:
        logger.debug(f"[ComponentManager] Could not fetch component manifest: {e}")
    return None


# --------------------------------------------------------------------------
# Installed-state tracking
# --------------------------------------------------------------------------
def _read_state() -> Dict[str, str]:
    path = get_state_file()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("installed", {}) or {}
    except Exception:
        return {}


def _write_state(state: Dict[str, str]) -> None:
    path = get_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"installed": state}, indent=2), encoding="utf-8")
    tmp.replace(path)


def get_installed_sha(key: str) -> Optional[str]:
    with _state_lock:
        return _read_state().get(key)


def mark_installed(key: str, sha256: str) -> None:
    with _state_lock:
        state = _read_state()
        state[key] = sha256
        _write_state(state)


def clear_installed(key: str) -> None:
    with _state_lock:
        state = _read_state()
        state.pop(key, None)
        _write_state(state)


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
def get_status(key: str, manifest: Optional[dict] = None) -> Dict[str, object]:
    """Report whether a component is installed / current / corrupt."""
    manifest = manifest or fetch_manifest() or {}
    entry = (manifest.get("components") or {}).get(key)
    if not entry:
        return {"key": key, "known": False, "installed": False, "current": False}

    install_dir = get_install_dir(key)
    installed_sha = get_installed_sha(key)
    on_disk = install_dir.is_dir()

    # Present but untracked: either it shipped in an older AppImage, or the
    # state file was lost. Hash it so we can still answer "is it current?".
    if on_disk and not installed_sha:
        logger.debug(f"[ComponentManager] {key} present but untracked; adopting as unverified")
        return {
            "key": key,
            "known": True,
            "installed": True,
            "current": None,          # unknown - needs a re-download to verify
            "name": entry.get("name"),
            "description": entry.get("description"),
            "size_bytes": entry.get("size_bytes"),
            "remote_sha256": entry.get("sha256"),
        }

    return {
        "key": key,
        "known": True,
        "installed": on_disk,
        "current": bool(installed_sha and installed_sha == entry.get("sha256")),
        "name": entry.get("name"),
        "description": entry.get("description"),
        "size_bytes": entry.get("size_bytes"),
        "remote_sha256": entry.get("sha256"),
        "local_sha256": installed_sha,
    }


def list_status() -> Dict[str, Dict[str, object]]:
    manifest = fetch_manifest() or {}
    keys = list((manifest.get("components") or {}).keys()) or ["goldberg", "steamless", "slscheevo"]
    return {k: get_status(k, manifest) for k in keys}


# --------------------------------------------------------------------------
# Download + install
# --------------------------------------------------------------------------
def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(tar: tarfile.TarFile, dest: Path, expected_root: str) -> None:
    """Extract members under ``expected_root`` into ``dest``, stripping the prefix.

    Members outside the expected root, or that traverse upwards, are skipped.
    Returns the directory the component's files were written to.
    """
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        if not member.isfile():
            continue
        parts = Path(member.name).parts
        if not parts or parts[0] != expected_root:
            logger.warning(f"[ComponentManager] skipping unexpected member {member.name!r}")
            continue
        if ".." in parts or member.name.startswith("/"):
            logger.warning(f"[ComponentManager] skipping unsafe member {member.name!r}")
            continue
        target = (dest / Path(*parts[1:])).resolve()
        if target != dest_resolved and dest_resolved not in target.parents:
            logger.warning(f"[ComponentManager] member escapes destination: {member.name!r}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        src = tar.extractfile(member)
        if src is None:
            continue
        with open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        if member.mode & 0o100:
            os.chmod(target, 0o755)
    return dest


def download_component(
    key: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    manifest: Optional[dict] = None,
) -> Tuple[bool, str]:
    """Download, verify, and install one component.

    Returns ``(ok, message)``. The install is atomic: files land in a temp
    directory and are swapped into place only after the SHA-256 matches, so a
    failed or interrupted download can never leave a half-installed component.
    """
    manifest = manifest or fetch_manifest()
    if not manifest:
        return False, "Could not fetch the component manifest (offline?)."

    entry = (manifest.get("components") or {}).get(key)
    if not entry:
        return False, f"Unknown component '{key}'."

    url = f"{R2_BASE_URL}/{entry['archive']}"
    expected = entry.get("sha256")
    install_dir = get_install_dir(key)

    tmpdir = Path(tempfile.mkdtemp(prefix=f"assella_component_{key}_"))
    archive = tmpdir / entry["archive"]
    try:
        # --- download -----------------------------------------------------
        ctx = None
        if not _tls_verify_enabled():
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
        opener = urllib.request.urlopen(req, context=ctx, timeout=120) if ctx else urllib.request.urlopen(req, timeout=120)
        with opener as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            done = 0
            with open(archive, "wb") as fh:
                while chunk := resp.read(65536):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)

        # --- verify -------------------------------------------------------
        actual = _sha256_of(archive)
        if expected and actual != expected:
            logger.error(f"[ComponentManager] {key} checksum mismatch")
            return False, "Checksum mismatch - download corrupted, not installed."

        # --- extract to staging ------------------------------------------
        # _safe_extract strips the archive's root folder, so the component's
        # files land directly in `staged`.
        staged_dir = tmpdir / "staged"
        staged_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            _safe_extract(tar, staged_dir, entry.get("install_dir", _install_dir_name(key)))

        if not staged_dir.is_dir() or not any(staged_dir.iterdir()):
            return False, "Archive did not contain the expected files."

        # --- swap into place ---------------------------------------------
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        backup = install_dir.with_name(install_dir.name + ".old")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if install_dir.exists():
            install_dir.rename(backup)
        try:
            shutil.move(str(staged_dir), str(install_dir))
        except Exception:
            if backup.exists():
                backup.rename(install_dir)
            raise
        shutil.rmtree(backup, ignore_errors=True)

        mark_installed(key, actual)
        mb = (entry.get("size_bytes") or 0) / 1048576
        logger.info(f"[ComponentManager] Installed {key} ({mb:.1f} MB)")
        return True, f"{entry.get('name', key)} installed ({mb:.1f} MB)."

    except urllib.error.URLError as e:
        return False, f"Download failed: {e}"
    except Exception as e:
        logger.error(f"[ComponentManager] {key} install failed: {e}", exc_info=True)
        return False, f"Install failed: {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def remove_component(key: str) -> Tuple[bool, str]:
    """Delete a component's files so it can be re-downloaded on demand."""
    install_dir = get_install_dir(key)
    clear_installed(key)
    if not install_dir.exists():
        return True, "Not installed."
    try:
        shutil.rmtree(install_dir)
        logger.info(f"[ComponentManager] Removed {key}")
        return True, "Removed."
    except Exception as e:
        return False, f"Could not remove: {e}"


def is_component_available(key: str) -> bool:
    """True when the component's files are present on disk.

    Used by callers that need the component to exist. Being 'available' means
    present, not necessarily hash-verified against the current remote.
    """
    return get_install_dir(key).is_dir()