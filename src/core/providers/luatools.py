"""LuaTools (Luie) manifest provider with optional Sushi fallback.

Provides manifest bundles via the lua.tools API (source=Luie) using the user's
authenticated Supabase session, with optional fallback to the Sushi manifest repository.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import requests

from core.providers.base import (
    Capability,
    ManifestProvider,
    ProviderAuthError,
    ProviderError,
    ProviderMeta,
    normalize_bundle,
)

logger = logging.getLogger(__name__)

API_BASE_URL = "https://lua.tools"
DISCOVERY_URL = "http://167.235.229.108/check_apis"
DISCOVERY_USER_AGENT = "secretgoonpoon"
SUSHI_BASE_URL = "https://raw.githubusercontent.com/sushi-dev55-alt/sushitools-games-repo-alt/refs/heads/main"
DEFAULT_TIMEOUT = 30

_availability_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_AVAILABILITY_TTL = 300.0  # 5 minutes


def check_luatools_availability(app_id: str | int) -> Dict[str, Any]:
    """Probe availability of app_id across providers via check_apis endpoint."""
    appid_str = str(app_id).strip()
    now = time.time()
    if appid_str in _availability_cache:
        ts, cached = _availability_cache[appid_str]
        if now - ts < _AVAILABILITY_TTL:
            return dict(cached)

    try:
        url = f"{DISCOVERY_URL}?appid={appid_str}"
        headers = {"User-Agent": DISCOVERY_USER_AGENT}
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                _availability_cache[appid_str] = (now, data)
                return data
    except Exception as e:
        logger.debug(f"[LuaTools] check_apis probe error for {appid_str}: {e}")

    return {}


class LuaToolsProvider(ManifestProvider):
    meta = ProviderMeta(
        id="luatools",
        label="LuaTools (Luie)",
        capabilities=frozenset({
            Capability.BUNDLE_DOWNLOAD,
            Capability.HEALTH,
        }),
    )

    def _get_token(self) -> Optional[str]:
        try:
            from core.lua_tools_api import LuaToolsAPI
            return LuaToolsAPI.get_valid_access_token()
        except Exception as e:
            logger.debug(f"[LuaTools] Could not load access token: {e}")
            return None

    def _is_sushi_fallback_allowed(self) -> bool:
        try:
            from utils.settings import get_settings
            return bool(get_settings().value("luatools_allow_sushi", False, type=bool))
        except Exception:
            return False

    def download_bundle(self, app_id, branch: str = "public", dest_path=None, force_update: bool = True):
        app_id_str = str(app_id).strip()
        branch = branch or "public"

        if dest_path is not None:
            dest = Path(dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir = dest.parent
        else:
            tmp_dir = Path(tempfile.gettempdir())

        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".luatools_bundle.", suffix=".part", dir=tmp_dir)
        os.close(tmp_fd)
        tmp = Path(tmp_name)

        # 1. Try Luie API if authenticated
        token = self._get_token()
        downloaded = False
        last_error = None

        if token:
            avail = check_luatools_availability(app_id_str)
            luie_status = str(avail.get("Luie") or "").lower()
            if luie_status in ("available", ""):  # probe available or probe unreached
                url = f"{API_BASE_URL}/api/manifest/download?appid={app_id_str}&source=Luie"
                headers = {
                    "Authorization": f"Bearer {token}",
                    "User-Agent": "ASSella/3.0",
                }
                try:
                    resp = requests.get(url, headers=headers, stream=True, timeout=DEFAULT_TIMEOUT)
                    if resp.status_code == 200:
                        with open(tmp, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                        downloaded = True
                    elif resp.status_code == 401:
                        last_error = "LuaTools token expired or invalid."
                    else:
                        last_error = f"Luie returned HTTP {resp.status_code}"
                except Exception as e:
                    last_error = f"Luie download failed: {e}"
            else:
                last_error = f"App {app_id_str} is reported unavailable on Luie ({luie_status})."
        else:
            last_error = "Not logged in to lua.tools."

        # 2. Try Sushi fallback if enabled
        if not downloaded and self._is_sushi_fallback_allowed():
            sushi_url = f"{SUSHI_BASE_URL}/{app_id_str}.zip"
            logger.info(f"[LuaTools] Luie unavailable ({last_error}); trying Sushi fallback...")
            try:
                resp = requests.get(sushi_url, stream=True, timeout=DEFAULT_TIMEOUT)
                if resp.status_code == 200:
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    downloaded = True
                else:
                    last_error = f"Sushi returned HTTP {resp.status_code}"
            except Exception as e:
                last_error = f"Sushi fallback failed: {e}"

        if not downloaded:
            tmp.unlink(missing_ok=True)
            if not token and not self._is_sushi_fallback_allowed():
                raise ProviderAuthError(last_error or "LuaTools authorization required.")
            return None, last_error or "Manifest bundle download failed."

        # Luie returns a plain-text .lua script rather than a .zip archive; wrap it into a ZIP if so.
        import zipfile
        if not zipfile.is_zipfile(tmp):
            try:
                raw_text = tmp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                raw_text = ""
            if "addappid(" in raw_text or raw_text.lstrip().startswith("--"):
                lua_zip_fd, lua_zip_name = tempfile.mkstemp(prefix=".luatools_lua.", suffix=".zip", dir=tmp_dir)
                os.close(lua_zip_fd)
                lua_zip = Path(lua_zip_name)
                try:
                    with zipfile.ZipFile(lua_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr(f"{app_id_str}.lua", raw_text.encode("utf-8"))
                    tmp.unlink(missing_ok=True)
                    tmp = lua_zip
                except Exception as wrap_err:
                    lua_zip.unlink(missing_ok=True)
                    tmp.unlink(missing_ok=True)
                    return None, f"Could not package LuaTools script for {app_id_str}: {wrap_err}"

        flat, err = normalize_bundle(tmp, app_id_str, branch)
        if err:
            tmp.unlink(missing_ok=True)
            return None, f"LuaTools bundle for {app_id_str} is unusable: {err}"

        if dest_path is not None:
            try:
                os.replace(flat, Path(dest_path))
            except OSError as e:
                flat.unlink(missing_ok=True)
                return None, f"Could not save LuaTools bundle: {e}"
            return str(dest_path), None

        return str(flat), None

    def check_health(self, force: bool = False) -> Dict[str, Any]:
        try:
            resp = requests.get(f"{API_BASE_URL}/api/denuvo/fixes?appid=400", timeout=8)
            if resp.status_code in (200, 404):
                return {"status": "healthy", "source": "luatools"}
            return {"status": "unhealthy", "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}
