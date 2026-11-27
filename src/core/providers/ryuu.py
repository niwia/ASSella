"""Ryuu's Manifests provider (https://generator.ryuu.lol).

Verified surface (Phase 0 spike, auth'd against app 440 and branch 105600):

  GET /api/download/{appid}                    -> ZIP bundle: ``{appid}.lua`` +
                                                  flat ``{depot}_{gid}.manifest``
  GET /api/download/{appid}?file_type=lua      -> single .lua (text/plain)
  GET /api/download/{appid}?branch=<b>         -> branch-specific ZIP (canonical)
  GET /requestupdate?appid=&branch=            -> ask the server to refresh its cache
  GET /request?appid=                          -> request a game
  GET /requestbranch?appid=&branch=            -> request a branch

Auth is an ``X-Auth-Key`` header carrying the permanent auth code from /login.

Gotchas confirmed by the spike:
  * ``branch=public`` must NOT be sent — it 404s ("Game not found"). Only send
    ``branch`` for non-public branches.
  * ``/api/manifest-keys`` is 403 even with a valid key, so there is no usable
    depot-key index. ``DEPOT_KEY_INDEX`` is therefore not advertised.
  * Downloads work anonymously; a bad/expired key must not block them, so the
    client retries once without the header on 401/403. ``/request*`` endpoints
    do require the key.
  * The server rate-limits aggressively (~5 burst requests, then HTTP 429). The
    shared ISP-bypass transport enforces per-domain pacing and backoff; this
    module adds a longer cooldown after a Ryuu 429 so the facade can fall back.

Ryuu has no per-depot ``generate`` by GID, no search, and no quota/stats
endpoint. It *does* serve branch bundles natively, which lets the facade skip
client-side assembly.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

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

BASE_URL = "https://generator.ryuu.lol"
DOMAIN = "generator.ryuu.lol"
DEFAULT_TIMEOUT = 60

# Observed rate limiting is tight; pair the transport's pacing with a cooldown.
_MIN_INTERVAL_SECONDS = 5.0
_RATE_LIMIT_COOLDOWN_SECONDS = 60.0

_cooldown_lock = threading.Lock()
_cooldown_until = 0.0
_session_obj: Optional[requests.Session] = None


def _session() -> requests.Session:
    global _session_obj
    if _session_obj is None:
        _session_obj = requests.Session()
    return _session_obj


def _check_cooldown() -> None:
    with _cooldown_lock:
        now = time.monotonic()
        if now < _cooldown_until:
            raise ProviderError(
                f"Ryuu is rate limited; retry in {int(_cooldown_until - now) + 1}s."
            )


def _enter_cooldown(seconds: float = _RATE_LIMIT_COOLDOWN_SECONDS) -> None:
    global _cooldown_until
    with _cooldown_lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)


class RyuuProvider(ManifestProvider):
    meta = ProviderMeta(
        id="ryuu",
        label="Ryuu's Manifests",
        capabilities=frozenset({
            Capability.BUNDLE_DOWNLOAD,
            Capability.NATIVE_BRANCH,
            Capability.HEALTH,
            Capability.REQUEST_MISSING,
        }),
    )
    rate_limit = (1, _MIN_INTERVAL_SECONDS)
    bypass_domains = (DOMAIN,)

    # -- credentials -------------------------------------------------------

    @staticmethod
    def _auth_key() -> str:
        try:
            from utils.settings import get_settings

            return str(get_settings().value("ryuu_auth_key", "", type=str) or "").strip()
        except Exception:
            return ""

    def _headers(self, required: bool = False) -> Dict[str, str]:
        """Build auth headers.

        Bundles are downloadable anonymously, so a key is optional there and an
        expired key must not block a working anonymous fetch. The ``/request*``
        endpoints do require it.
        """
        key = self._auth_key()
        if not key:
            if required:
                raise ProviderAuthError("Ryuu auth key is not set. Add it in Settings.")
            return {}
        return {"X-Auth-Key": key}

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _json_error(payload: bytes) -> Optional[str]:
        try:
            data = json.loads(payload.decode("utf-8", errors="ignore"))
        except (ValueError, UnicodeDecodeError) as e:
            logger.debug("[Ryuu] response body was not JSON: %s", e)
            return None
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
        return None

    @staticmethod
    def _raise_for_status(resp: requests.Response, body: bytes) -> None:
        error = RyuuProvider._json_error(body)
        if resp.status_code == 429:
            _enter_cooldown()
            raise ProviderError(error or "Ryuu rate limit reached; try again shortly.")
        if resp.status_code in (401, 403):
            raise ProviderAuthError(error or f"Ryuu rejected the auth key ({resp.status_code})")
        if resp.status_code >= 400:
            raise ProviderError(error or f"Ryuu returned HTTP {resp.status_code}")
        if error:
            raise ProviderError(error)

    def _request(self, method: str, url: str, *, headers: Dict[str, str],
                 params: Optional[Dict[str, Any]] = None, stream: bool = False,
                 timeout: int = DEFAULT_TIMEOUT) -> requests.Response:
        _check_cooldown()
        try:
            from utils.kuroko import stealth_http_request
            resp = stealth_http_request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=timeout,
                stream=stream,
                session=_session(),
            )
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 0) or 0)
                _enter_cooldown(max(_RATE_LIMIT_COOLDOWN_SECONDS, retry_after))
                raise ProviderError("Ryuu rate limit reached; try again shortly.")
            return resp
        except ProviderError:
            raise
        except requests.exceptions.RequestException as e:
            raise ProviderError(f"Ryuu request failed: {e}") from e

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
             timeout: int = DEFAULT_TIMEOUT, required_auth: bool = True) -> Dict[str, Any]:
        headers = self._headers(required=required_auth)
        try:
            resp = self._request("GET", f"{BASE_URL}{path}", headers=headers, params=params, timeout=timeout)
        except ProviderError:
            raise
        except requests.exceptions.HTTPError as e:
            response = getattr(e, "response", None)
            body = (response.content if response is not None else b"") or b""
            if response is not None:
                self._raise_for_status(response, body)
            raise ProviderError(str(e)) from e

        body = resp.content or b""
        self._raise_for_status(resp, body)

        if not body:
            return {"ok": True}
        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
        except (ValueError, UnicodeDecodeError):
            return {"ok": True, "message": body.decode("utf-8", errors="ignore").strip()}
        if isinstance(data, dict):
            data.setdefault("ok", True)
            return data
        return {"ok": True, "data": data}

    # -- ManifestProvider --------------------------------------------------

    def download_bundle(self, app_id, branch="public", dest_path=None, force_update=True):
        branch = branch or "public"
        params: Dict[str, Any] = {}
        if branch != "public":
            params["branch"] = branch

        if dest_path is not None:
            dest = Path(dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dir = dest.parent
        else:
            tmp_dir = Path(tempfile.gettempdir())

        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".ryuu_bundle.", suffix=".part", dir=tmp_dir)
        os.close(tmp_fd)
        tmp = Path(tmp_name)

        headers = self._headers(required=False)
        url = f"{BASE_URL}/api/download/{app_id}"

        try:
            try:
                resp = self._request("GET", url, headers=headers, params=params, stream=True)
            except requests.exceptions.HTTPError as first_err:
                response = getattr(first_err, "response", None)
                if response is not None and response.status_code in (401, 403) and headers:
                    self._raise_for_status(response, response.content or b"")
                    # An expired/unknown key can be rejected while anonymous access
                    # still works; retry once without it before surfacing an error.
                    try:
                        response.close()
                    except Exception as close_err:
                        logger.debug("[Ryuu] could not close rejected response: %s", close_err)
                    resp = self._request("GET", url, params=params, stream=True, headers={})
                else:
                    if response is not None:
                        self._raise_for_status(response, response.content or b"")
                    raise ProviderError(str(first_err)) from first_err

            try:
                if resp.status_code >= 400:
                    self._raise_for_status(resp, resp.content or b"")

                ctype = str(resp.headers.get("Content-Type", "")).lower()
                if "application/json" in ctype or "text/html" in ctype:
                    raise ProviderError(f"Ryuu returned a non-binary payload ({ctype or 'unknown'})")

                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            finally:
                resp.close()
        except (ProviderAuthError, ProviderError):
            tmp.unlink(missing_ok=True)
            raise

        flat, err = normalize_bundle(tmp, app_id, branch)
        if err:
            tmp.unlink(missing_ok=True)
            return None, f"Ryuu bundle for {app_id} is unusable: {err}"

        if dest_path is not None:
            try:
                os.replace(flat, Path(dest_path))
            except OSError as e:
                flat.unlink(missing_ok=True)
                return None, f"Could not save Ryuu bundle: {e}"
            return str(dest_path), None

        return str(flat), None

    def request_refresh(self, app_id, branch="public"):
        return self._get("/requestupdate", {"appid": app_id, "branch": branch or "public"})

    def request_game(self, app_id):
        return self._get("/request", {"appid": app_id})

    def request_branch(self, app_id, branch):
        return self._get("/requestbranch", {"appid": app_id, "branch": branch})

    def check_health(self, force: bool = False) -> Dict[str, Any]:
        try:
            resp = requests.get(BASE_URL, timeout=10, allow_redirects=True)
        except requests.exceptions.RequestException as e:
            return {"status": "unhealthy", "error": f"Ryuu is unreachable: {e}"}
        if resp.status_code >= 500:
            return {"status": "unhealthy", "error": f"Ryuu returned HTTP {resp.status_code}"}
        return {"status": "healthy", "source": "ryuu"}
