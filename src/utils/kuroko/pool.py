"""High-level stealth proxy pool orchestration, rotation, failover, and UI fallback prompts."""

import inspect
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

from .config import get_or_create_warp_config as _default_get_or_create_warp_config
from .probe import test_and_benchmark_endpoint as _default_test_and_benchmark_endpoint
from .profiles import WireguardProfileManager as _default_WireguardProfileManager
from .scoring import (
    _endpoint_cooldowns,
    _safe_wg_profiles,
    clear_endpoint_cooldowns as _default_clear_cooldowns,
    get_endpoint_scores as _default_get_endpoint_scores,
    is_endpoint_in_cooldown as _default_is_in_cooldown,
    mark_endpoint_cooldown as _default_mark_cooldown,
)
from .tunnel import Kuroko as _default_Kuroko

logger = logging.getLogger(__name__)

get_or_create_warp_config = _default_get_or_create_warp_config
test_and_benchmark_endpoint = _default_test_and_benchmark_endpoint
WireguardProfileManager = _default_WireguardProfileManager
clear_endpoint_cooldowns = _default_clear_cooldowns
get_endpoint_scores = _default_get_endpoint_scores
is_endpoint_in_cooldown = _default_is_in_cooldown
mark_endpoint_cooldown = _default_mark_cooldown
Kuroko = _default_Kuroko


def _resolve_symbol(name: str, local_sym, default_sym):
    if local_sym is not default_sym:
        return local_sym
    pkg = sys.modules.get("utils.kuroko")
    if pkg is not None:
        pkg_sym = getattr(pkg, name, default_sym)
        if pkg_sym is not default_sym:
            return pkg_sym
    return local_sym


# Network transport and proxy error signatures for smart failover
NETWORK_ERROR_SIGNATURES = (
    "ratelimitexceeded",
    "429",
    "httprequestexception",
    "socketexception",
    "connection reset",
    "timed out",
    "timeout",
    "unable to connect to the remote server",
    "proxy error",
    "econnrefused",
    "econnreset",
    "broken pipe",
    "eof reached",
    "network is unreachable",
    "host is unreachable",
    "connection closed abruptly",
)


def is_network_or_proxy_error(error_text: str) -> bool:
    """Return True only if error_text matches transport or rate-limiting error signatures.

    Explicitly excludes non-network errors such as missing decryption keys (AccessDenied)
    or bad manifests so that valid proxies are not erroneously blacklisted.
    """
    if not error_text:
        return False
    lower = error_text.lower()
    if "accessdenied" in lower or "bad decryption key" in lower or "missing depot key" in lower:
        return False
    return any(sig in lower for sig in NETWORK_ERROR_SIGNATURES)


class StealthPoolManager:
    """Manages multi-endpoint rotation, sequential benchmarking, and smart failover."""

    @staticmethod
    def get_pool_candidates(settings=None) -> list[dict]:
        """Return all configured and enabled candidates in the stealth pool."""
        if settings is None:
            from utils.settings import get_settings

            settings = get_settings()

        wg_mgr = _resolve_symbol(
            "WireguardProfileManager", WireguardProfileManager, _default_WireguardProfileManager
        )
        candidates = []

        # 1. Cloudflare WARP
        include_warp = settings.value("stealth_pool_warp", True, type=bool)
        if include_warp:
            candidates.append({
                "id": "warp",
                "type": "warp",
                "name": "Cloudflare WARP (Auto)",
                "config_path": "",
                "url": "",
            })

        # 2. Imported WireGuard Profiles
        raw_profiles = _safe_wg_profiles(settings)
        p_dir = wg_mgr.get_profiles_dir()
        for item in raw_profiles:
            fname = str(item).strip()
            if not fname:
                continue
            conf_file = p_dir / fname
            if conf_file.is_file():
                candidates.append({
                    "id": f"wg:{fname}",
                    "type": "wireguard",
                    "name": f"WG: {conf_file.stem}",
                    "config_path": str(conf_file.resolve()),
                    "url": "",
                })

        # 3. Custom SOCKS5/HTTP Proxy
        include_custom = settings.value("stealth_pool_custom", False, type=bool)
        if include_custom:
            custom_url = settings.value("stealth_proxy_custom_url", "").strip()
            if custom_url:
                if "://" not in custom_url:
                    custom_url = f"socks5://{custom_url}"
                candidates.append({
                    "id": "custom",
                    "type": "custom",
                    "name": "Custom Proxy",
                    "config_path": "",
                    "url": custom_url,
                })

        return candidates

    @classmethod
    def get_eligible_candidates(cls, settings=None) -> list[dict]:
        """Return candidates not in cooldown, sorted by cached benchmark score."""
        in_cooldown_fn = _resolve_symbol(
            "is_endpoint_in_cooldown", is_endpoint_in_cooldown, _default_is_in_cooldown
        )
        scores_fn = _resolve_symbol(
            "get_endpoint_scores", get_endpoint_scores, _default_get_endpoint_scores
        )

        candidates = cls.get_pool_candidates(settings)
        eligible = [c for c in candidates if not in_cooldown_fn(c["id"])]
        scores = scores_fn(settings)

        def _score_key(c: dict) -> float:
            s = scores.get(c["id"])
            if not s or not s.get("ok"):
                return -9999.0
            return float(s.get("speed_mbps", 0.0)) - (float(s.get("ping_ms", 0.0)) / 50.0)

        eligible.sort(key=_score_key, reverse=True)
        return eligible

    @classmethod
    def test_and_benchmark_candidate(
        cls, candidate: dict, timeout: float = 8.0, settings=None
    ) -> Tuple[bool, str, int, float, str]:
        """Test connection and benchmark speed for a candidate in a single ephemeral tunnel session."""
        bench_fn = _resolve_symbol(
            "test_and_benchmark_endpoint",
            test_and_benchmark_endpoint,
            _default_test_and_benchmark_endpoint,
        )
        c_type = candidate.get("type", "warp")
        if c_type == "warp":
            res = bench_fn("warp", timeout=timeout, settings=settings)
        elif c_type == "wireguard":
            res = bench_fn(
                "wireguard", wg_path=candidate.get("config_path", ""), timeout=timeout, settings=settings
            )
        elif c_type == "custom":
            res = bench_fn(
                "custom", custom_url=candidate.get("url", ""), timeout=timeout, settings=settings
            )
        else:
            res = (False, "", 0, 0.0, f"Unknown candidate type: '{c_type}'")
        return res

    @classmethod
    def benchmark_candidate(
        cls, candidate: dict, timeout: float = 8.0, settings=None
    ) -> Tuple[bool, int, float, str]:
        """Benchmark a candidate endpoint in isolation and return (ok, ping_ms, speed_mbps, msg)."""
        ok, _exit_label, ping, speed, msg = cls.test_and_benchmark_candidate(
            candidate, timeout=timeout, settings=settings
        )
        return ok, ping, speed, msg

    @classmethod
    def benchmark_all_candidates(
        cls,
        settings=None,
        progress_callback: Optional[Callable[..., None]] = None,
    ) -> list[dict]:
        """
        Sequentially benchmark all pool candidates to prevent home network saturation.
        Calls progress_callback(candidate_id, success, exit_label, ping_ms, speed_mbps, message) on each step.
        Returns the candidates list sorted by highest speed_mbps (and lowest ping_ms).
        """
        candidates = cls.get_pool_candidates(settings)
        results = []
        for c in candidates:
            c_id = c["id"]
            ok, exit_label, ping, speed, msg = cls.test_and_benchmark_candidate(c, settings=settings)
            c_copy = dict(c)
            c_copy["ok"] = ok
            c_copy["exit_label"] = exit_label
            c_copy["ping_ms"] = ping
            c_copy["speed_mbps"] = speed
            c_copy["msg"] = msg
            results.append(c_copy)
            if progress_callback is not None:
                try:
                    sig = inspect.signature(progress_callback)
                    if len(sig.parameters) == 5:
                        progress_callback(c_id, ok, ping, speed, msg)
                    else:
                        progress_callback(c_id, ok, exit_label, ping, speed, msg)
                except Exception as cb_err:
                    logger.debug(f"[Kuroko] Benchmark progress callback error: {cb_err}")

        # Sort: healthy first, then highest speed, then lowest ping
        results.sort(
            key=lambda x: (not x.get("ok", False), -x.get("speed_mbps", 0.0), x.get("ping_ms", 9999))
        )
        return results

    @classmethod
    def resolve_best_stealth_endpoint(
        cls, settings=None, previous_failed_id: Optional[str] = None
    ) -> Tuple[str, Optional[Kuroko], str]:
        """
        Select and initialize the best available stealth endpoint.
        If rotation is enabled: tests candidates, verifies live peer reachability,
        and automatically falls over to the next candidate if dead.
        Returns (proxy_url, manager_instance, endpoint_id).
        Raises RuntimeError if all candidates fail or rotation pool is empty.
        """
        if settings is None:
            from utils.settings import get_settings

            settings = get_settings()

        kuroko_cls = _resolve_symbol("Kuroko", Kuroko, _default_Kuroko)
        warp_cfg_fn = _resolve_symbol(
            "get_or_create_warp_config", get_or_create_warp_config, _default_get_or_create_warp_config
        )
        mark_cd_fn = _resolve_symbol(
            "mark_endpoint_cooldown", mark_endpoint_cooldown, _default_mark_cooldown
        )
        clear_cd_fn = _resolve_symbol(
            "clear_endpoint_cooldowns", clear_endpoint_cooldowns, _default_clear_cooldowns
        )
        wg_mgr = _resolve_symbol(
            "WireguardProfileManager", WireguardProfileManager, _default_WireguardProfileManager
        )

        if previous_failed_id:
            mark_cd_fn(previous_failed_id)

        rotation_on = settings.value("stealth_rotation_enabled", False, type=bool)

        if not rotation_on:
            mode = settings.value("stealth_proxy_mode", "warp")
            manager = kuroko_cls()
            if mode == "warp":
                cfg = warp_cfg_fn()
                if not manager.start(cfg, port=0):
                    raise RuntimeError("Failed to start wireproxy for Cloudflare WARP.")
                if not manager.probe_alive(manager.proxy_url):
                    manager.stop()
                    raise RuntimeError("Cloudflare WARP endpoint failed live peer connectivity probe.")
                return manager.proxy_url, manager, "warp"

            elif mode == "wireguard":
                active_profile = settings.value("stealth_active_wg_profile", "", type=str).strip()
                p_dir = wg_mgr.get_profiles_dir()
                conf_path = ""
                if active_profile and (p_dir / active_profile).is_file():
                    conf_path = str((p_dir / active_profile).resolve())
                else:
                    conf_path = settings.value("stealth_proxy_wg_path", "", type=str).strip()

                if not conf_path or not os.path.isfile(conf_path):
                    raise RuntimeError(
                        f"WireGuard config file not found: '{conf_path}'. "
                        "Please select or import a valid .conf file in Settings -> Stealth."
                    )
                if not manager.start(conf_path, port=0):
                    raise RuntimeError("Failed to start wireproxy with WireGuard config.")
                if not manager.probe_alive(manager.proxy_url):
                    manager.stop()
                    raise RuntimeError(
                        f"WireGuard profile '{Path(conf_path).stem}' failed live peer connectivity probe."
                    )
                return manager.proxy_url, manager, f"wg:{Path(conf_path).name}"

            elif mode == "custom":
                custom_url = settings.value(
                    "stealth_proxy_custom_url", "socks5://127.0.0.1:1080"
                ).strip()
                if not custom_url:
                    raise RuntimeError("Custom proxy mode is enabled, but no proxy URL was specified.")
                if "://" not in custom_url:
                    custom_url = f"socks5://{custom_url}"
                if not kuroko_cls.probe_alive(custom_url):
                    raise RuntimeError(
                        f"Custom proxy at '{custom_url}' failed live connectivity probe."
                    )
                return custom_url, None, "custom"

            else:
                raise RuntimeError(f"Unsupported stealth proxy mode: '{mode}'.")

        # Rotation enabled: query eligible pool candidates
        candidates = cls.get_eligible_candidates(settings)
        if not candidates:
            if _endpoint_cooldowns:
                logger.info(
                    "[Kuroko] All endpoints were in cooldown; clearing cooldowns to retry pool."
                )
                clear_cd_fn()
                candidates = cls.get_pool_candidates(settings)

        if not candidates:
            raise RuntimeError(
                "Stealth Endpoint Rotation is enabled, but no pool candidates are checked. "
                "Please enable at least one candidate (WARP, WireGuard, or Custom) in Settings -> Stealth."
            )

        last_error = ""
        for cand in candidates:
            c_id = cand["id"]
            c_type = cand["type"]
            manager = kuroko_cls()
            try:
                if c_type == "warp":
                    cfg = warp_cfg_fn()
                    if not manager.start(cfg, port=0):
                        mark_cd_fn(c_id)
                        last_error = "WARP wireproxy start failed"
                        continue
                    if not manager.probe_alive(manager.proxy_url):
                        logger.warning(f"[Kuroko] Candidate '{c_id}' failed dead peer probe; rotating.")
                        mark_cd_fn(c_id)
                        manager.stop()
                        last_error = "WARP peer unreachable"
                        continue
                    return manager.proxy_url, manager, c_id

                elif c_type == "wireguard":
                    cfg_path = cand.get("config_path", "")
                    if not cfg_path or not os.path.isfile(cfg_path):
                        mark_cd_fn(c_id)
                        last_error = f"Config file missing: {cfg_path}"
                        continue
                    if not manager.start(cfg_path, port=0):
                        mark_cd_fn(c_id)
                        last_error = f"WireGuard start failed for {cand['name']}"
                        continue
                    if not manager.probe_alive(manager.proxy_url):
                        logger.warning(f"[Kuroko] Candidate '{c_id}' failed dead peer probe; rotating.")
                        mark_cd_fn(c_id)
                        manager.stop()
                        last_error = f"WireGuard peer '{cand['name']}' unreachable"
                        continue
                    return manager.proxy_url, manager, c_id

                elif c_type == "custom":
                    c_url = cand.get("url", "")
                    if not c_url or not kuroko_cls.probe_alive(c_url):
                        mark_cd_fn(c_id)
                        last_error = "Custom proxy unreachable"
                        continue
                    return c_url, None, c_id

            except Exception as e:
                logger.warning(f"[Kuroko] Failed initializing candidate '{c_id}': {e}")
                mark_cd_fn(c_id)
                manager.stop()
                last_error = str(e)
                continue

        raise RuntimeError(
            f"All stealth pool candidates failed live connectivity verification. Last error: {last_error}"
        )


def resolve_stealth_proxy_url(
    settings=None, failover_from: Optional[str] = None
) -> Tuple[Optional[str], Optional[Kuroko]]:
    """
    Check if stealth proxy is enabled in settings and resolve the active proxy URL and manager.
    Supports single mode, multi-endpoint rotation, and smart dead-peer failover.
    Returns (proxy_url, kuroko_manager_or_none).
    """
    if settings is None:
        from utils.settings import get_settings

        settings = get_settings()

    enable_proxy = settings.value("enable_stealth_proxy", False, type=bool)
    if not enable_proxy:
        return None, None

    pool_cls = _resolve_symbol("StealthPoolManager", StealthPoolManager, StealthPoolManager)
    url, mgr, cand_id = pool_cls.resolve_best_stealth_endpoint(
        settings=settings, previous_failed_id=failover_from
    )
    if mgr is not None:
        setattr(mgr, "endpoint_id", cand_id)
    return url, mgr


def prompt_stealth_fallback(error_message: str) -> bool:
    """Prompt user on Qt main GUI thread when stealth proxy / VPN connection fails.

    Offers two choices:
      1. 'Download Without Stealth' (Direct non-stealth download)
      2. 'Cancel Download'

    If user selects Direct:
      - Sets 'enable_stealth_proxy' to False in QSettings
      - Refreshes MainWindow system status visor
      - Returns True
    If user selects Cancel or closes dialog:
      - Leaves settings unchanged
      - Returns False
    """
    from utils.main_thread import post_to_main_thread

    choice = ["cancel"]
    done_event = threading.Event()

    def _show_dialog():
        try:
            from PyQt6.QtWidgets import QApplication

            if QApplication.instance() is None:
                # Headless / test environment without Qt event loop: do not construct QWidget
                return

            from ui.dialogs.themed_message_box import ThemedMessageBox

            clean_err = (error_message or "").strip()
            if not clean_err:
                clean_err = "The stealth proxy endpoint is unavailable or disconnected."

            res = ThemedMessageBox.choices(
                None,
                "Stealth Connection Failed",
                f"Unable to connect through stealth proxy / VPN:\n\n{clean_err}\n\n"
                "Would you like to proceed with a direct (non-stealth) download, or cancel the download?",
                buttons=[
                    ("Download Without Stealth", "direct"),
                    ("Cancel Download", "cancel"),
                ],
                default="cancel",
                severity="warning",
            )
            if res == "direct":
                choice[0] = "direct"
        except Exception as e:
            logger.error(f"[Kuroko] Error showing stealth fallback prompt: {e}")
        finally:
            done_event.set()

    try:
        post_to_main_thread(_show_dialog)
        done_event.wait()
    except Exception as e:
        logger.error(f"[Kuroko] Error scheduling fallback dialog on main thread: {e}")
        done_event.set()

    if choice[0] == "direct":
        logger.warning(
            "[Kuroko] User opted to bypass failed stealth proxy and proceed with direct download."
        )
        from utils.settings import get_settings

        settings = get_settings()
        settings.setValue("enable_stealth_proxy", False)

        def _refresh_visor():
            try:
                from PyQt6.QtWidgets import QApplication

                app = QApplication.instance()
                if app:
                    for widget in app.topLevelWidgets():
                        if hasattr(widget, "refresh_system_status"):
                            widget.refresh_system_status()
            except Exception as e:
                logger.debug(f"[Kuroko] Error refreshing visor after direct fallback: {e}")

        post_to_main_thread(_refresh_visor)
        return True

    return False


__all__ = [
    "NETWORK_ERROR_SIGNATURES",
    "StealthPoolManager",
    "is_network_or_proxy_error",
    "prompt_stealth_fallback",
    "resolve_stealth_proxy_url",
]