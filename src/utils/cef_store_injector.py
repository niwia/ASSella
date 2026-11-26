"""Standalone CEF WebSocket injector for Steam's Store webview.

Connects directly to Steam's CEF remote debugging port (8080-8082) when
``store_patch.is_enabled()`` is True and Uran (Decky) is not already managing
the Store button. Injects an "Add to at0-m" button into Steam Store pages and
triggers ASSella's AT0-M registration when clicked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

_ASSELLA_ADD_TAG = "ASSELLA_ADD:"
_ASSELLA_REFETCH_TAG = "ASSELLA_REFETCH:"
_POLL_INTERVAL_SEC = 3.0


def _extract_appid(url: str) -> str:
    m = re.search(r"/app/(\d+)", str(url or ""))
    return m.group(1) if m else ""


def _build_inject_script() -> str:
    """Return the self-contained DOM injection script for Steam Store pages."""
    return f"""(function() {{
  try {{
    function log(msg){{ try {{ console.log('ASSELLA-STORE:' + msg); }} catch(e){{}} }}
    var ASSELLA_ADD_TAG = {json.dumps(_ASSELLA_ADD_TAG)};
    var ASSELLA_REFETCH_TAG = {json.dumps(_ASSELLA_REFETCH_TAG)};
    var BASE_BLUE_BG = 'rgba(103, 193, 245, 0.2)';
    var BASE_BLUE_HOVER_BG = 'linear-gradient(-60deg, #417a9b 5%, #67c1f5 95%)';
    var TEXT_COLOR = '#67c1f5';
    var TEXT_HOVER_COLOR = '#ffffff';

    function getState() {{
      if (!window.__assellaStoreState) {{
        window.__assellaStoreState = {{
          appId: '',
          label: 'Add to at0-m',
          tooltip: 'Add game to at0-m library',
          actionTag: ASSELLA_ADD_TAG,
          clickLabel: 'Adding...',
          locked: false
        }};
      }}
      return window.__assellaStoreState;
    }}

    function makeButton(text, tag, clickLabel, disabled, isWishlistStyle) {{
      var S = getState();
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'btnv6_blue_hoverfade btn_medium queue_btn_inactive';
      b.setAttribute('data-assella-action', tag || ASSELLA_ADD_TAG);
      b.setAttribute('role', 'button');
      b.tabIndex = 0;
      var tip = S.tooltip || 'Add game to at0-m library';
      b.title = tip;
      b.setAttribute('aria-label', tip);

      var span = document.createElement('span');
      span.textContent = text;
      span.style.cssText = isWishlistStyle
        ? 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;box-sizing:border-box;padding:0 16px;background:transparent;color:' + TEXT_COLOR + ';border-radius:2px;font-family:"Motiva Sans",Arial,sans-serif;font-size:14px;font-weight:500;white-space:nowrap;'
        : 'display:block;background:transparent;color:' + TEXT_COLOR + ';border-radius:2px;font-family:"Motiva Sans",Arial,sans-serif;font-size:15px;line-height:30px;padding:0 15px;box-sizing:border-box;white-space:nowrap;text-align:center;';

      b.style.cssText = isWishlistStyle
        ? 'width:100%;height:36px;display:flex;align-items:center;justify-content:center;background:' + BASE_BLUE_BG + ';border:none;border-radius:2px;padding:1px;box-sizing:border-box;cursor:' + (disabled ? 'default;' : 'pointer;') + 'opacity:' + (disabled ? '0.7;' : '1;') + 'text-decoration:none;'
        : 'display:inline-block;background:' + BASE_BLUE_BG + ';border:none;border-radius:2px;padding:1px;box-sizing:border-box;cursor:' + (disabled ? 'default;' : 'pointer;') + 'opacity:' + (disabled ? '0.7;' : '1;') + 'text-decoration:none;';

      b.appendChild(span);
      if (disabled) {{
        b.disabled = true;
      }} else {{
        b.onclick = function(e) {{
          e.stopPropagation();
          e.preventDefault();
          if (b.disabled) return;
          var cur = getState();
          span.textContent = cur.clickLabel || 'Adding...';
          b.style.opacity = '0.7';
          b.disabled = true;
          console.log(cur.actionTag + cur.appId);
        }};
        b.onmouseenter = function() {{ if (!b.disabled) {{ b.style.background = BASE_BLUE_HOVER_BG; span.style.color = TEXT_HOVER_COLOR; }} }};
        b.onmouseleave = function() {{ if (!b.disabled) {{ b.style.background = BASE_BLUE_BG; span.style.color = TEXT_COLOR; }} }};
      }}
      return b;
    }}

    function findExisting() {{
      var all = document.querySelectorAll('#Uran-btns, #assella-purchase-slot');
      for (var i = 1; i < all.length; i++) all[i].remove();
      return all[0] || null;
    }}

    function updateExisting(existing) {{
      if (!existing) return false;
      var S = getState();
      if (existing.getAttribute('data-assella-appid') !== S.appId) return false;
      var btn = existing.querySelector('button[data-assella-action]');
      if (btn) {{
        btn.setAttribute('data-assella-action', S.actionTag);
        var span = btn.querySelector('span');
        if (span) span.textContent = S.label;
        else btn.textContent = S.label;
        btn.title = S.tooltip || '';
        btn.style.opacity = S.locked ? '0.7' : '1';
        btn.style.cursor = S.locked ? 'default' : 'pointer';
        btn.disabled = !!S.locked;
      }}
      return true;
    }}

    function createWrapper(isWishlistStyle) {{
      var S = getState();
      var w = document.createElement('div');
      w.id = 'Uran-btns';
      w.setAttribute('data-assella-appid', S.appId);
      if (isWishlistStyle) {{
        w.style.cssText = 'width:100%;grid-column:1 / -1;display:flex;flex-direction:column;gap:4px;box-sizing:border-box;';
      }} else {{
        w.className = 'queue_control_button queue_btn_uran';
        w.style.cssText = 'flex-grow:0;';
      }}
      w.appendChild(makeButton(S.label, S.actionTag, S.clickLabel, S.locked, isWishlistStyle));
      return w;
    }}

    function injectNearWishlist() {{
      var q = document.getElementById('queueActionsCtn');
      if (!q || q.offsetHeight === 0) return false;
      var w = createWrapper(false);
      var follow = document.getElementById('add_to_wishlist_area') || q.firstElementChild;
      if (follow && follow.parentElement === q) q.insertBefore(w, follow.nextSibling);
      else q.appendChild(w);
      return true;
    }}

    function injectReactPurchaseRow() {{
      var ft = document.getElementById('FeatureTarget_purchase-options');
      if (!ft || ft.offsetHeight === 0) return false;
      var target = ft.querySelector('.StoreSalePriceWidgetContainer, [class*="StoreSalePriceWidgetContainer"], button[title*="Cart" i], button[aria-label*="cart" i]');
      if (!target || !target.parentElement) return false;
      target.parentElement.insertBefore(createWrapper(false), target);
      return true;
    }}

    function injectOldSteamAction() {{
      var area = document.querySelector('.game_purchase_action');
      if (!area || area.offsetHeight === 0) return false;
      var w = createWrapper(false);
      if (area.firstChild) area.insertBefore(w, area.firstChild);
      else area.appendChild(w);
      return true;
    }}

    function injectFromState() {{
      var S = getState();
      if (!S.appId) return;
      var existing = findExisting();
      if (existing) {{
        if (updateExisting(existing)) return;
        existing.remove();
      }}
      if (injectNearWishlist()) return;
      if (injectReactPurchaseRow()) return;
      if (injectOldSteamAction()) return;
    }}

    window.__assellaInjectFromState = injectFromState;
    if (!window.__assellaObserverInstalled) {{
      window.__assellaObserverInstalled = true;
      var mo = new MutationObserver(function() {{
        if (!window.__assellaStoreState || !window.__assellaStoreState.appId) return;
        var cur = findExisting();
        if (!cur) window.__assellaInjectFromState();
        else updateExisting(cur);
      }});
      mo.observe(document.body, {{ childList: true, subtree: true }});
    }}
    injectFromState();
  }} catch(err) {{
    try {{ console.log('ASSELLA-STORE: error ' + err); }} catch(e){{}}
  }}
}})();"""


class CefStoreInjector:
    """Background asyncio worker that attaches to Steam's CEF Store tab."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pending: Set[str] = set()
        self._msg_id = 1
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_thread, name="CefStoreInjector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._loop and self._ws is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except Exception as e:
                logger.debug("[CefStoreInjector] stop ws close skipped: %s", e)

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._main_loop())
        except Exception as e:
            logger.debug("[CefStoreInjector] loop exited: %s", e)

    def _should_run(self) -> bool:
        from utils import store_patch
        from utils.settings import get_settings

        if not get_settings().value("at0m_enabled", False, type=bool):
            return False
        if not store_patch.is_enabled():
            return False
        if store_patch.is_uran_managing_store():
            return False
        return True

    def _is_app_added(self, appid: str) -> bool:
        try:
            from utils.plugin_games import load_plugin_library

            return str(appid) in load_plugin_library()
        except Exception as e:
            logger.debug("[CefStoreInjector] load_plugin_library failed: %s", e)
            return False

    async def _send_cdp(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if self._ws is None or self._ws.closed:
            return
        self._msg_id += 1
        payload: Dict[str, Any] = {"id": self._msg_id, "method": method}
        if params:
            payload["params"] = params
        await self._ws.send_str(json.dumps(payload))

    async def _inject(self, appid: str, label_override: Optional[str] = None, locked_override: Optional[bool] = None) -> None:
        if not appid:
            return
        added = self._is_app_added(appid)
        pending = appid in self._pending
        if label_override is not None:
            label = label_override
        elif pending:
            label = "Adding..."
        elif added:
            label = "In at0-m Library"
        else:
            label = "Add to at0-m"

        locked = locked_override if locked_override is not None else (pending or added)
        action_tag = _ASSELLA_REFETCH_TAG if added else _ASSELLA_ADD_TAG
        tooltip = "Already in at0-m library" if added else "Add game to at0-m library"

        state_js = (
            "(function(){try{"
            "var S=window.__assellaStoreState;if(!S){S={};window.__assellaStoreState=S;}"
            f"S.appId={json.dumps(appid)};"
            f"S.label={json.dumps(label)};"
            f"S.tooltip={json.dumps(tooltip)};"
            f"S.actionTag={json.dumps(action_tag)};"
            "S.clickLabel='Adding...';"
            f"S.locked={json.dumps(bool(locked))};"
            "}catch(e){}})();"
        )
        await self._send_cdp("Runtime.evaluate", {"expression": state_js})
        await self._send_cdp("Runtime.evaluate", {"expression": _build_inject_script()})

    async def _remove(self) -> None:
        expr = (
            "(function(){try{"
            "var s=document.querySelectorAll('#assella-purchase-slot, #Uran-btns');"
            "for(var i=0;i<s.length;i++){s[i].remove();}"
            "}catch(e){}})();"
        )
        await self._send_cdp("Runtime.evaluate", {"expression": expr})

    def _register_game_sync(self, appid: str) -> bool:
        """Download Hubcap bundle and hand off to AT0-M on a worker thread."""
        try:
            from core.morrenus_api import download_manifest
            from core.native_steam.native_steam_handoff import handoff_to_native_steam

            res = download_manifest(appid)
            if not res or not res.get("success"):
                logger.warning("[CefStoreInjector] Manifest download failed for %s: %s", appid, res)
                return False
            handoff = handoff_to_native_steam(appid, auto_install=False)
            return bool(handoff and handoff.get("success"))
        except Exception as e:
            logger.error("[CefStoreInjector] Failed to add %s: %s", appid, e, exc_info=True)
            return False

    async def _handle_add_click(self, appid: str) -> None:
        if appid in self._pending:
            return
        self._pending.add(appid)
        await self._inject(appid, label_override="Adding...", locked_override=True)
        try:
            ok = await asyncio.to_thread(self._register_game_sync, appid)
        finally:
            self._pending.discard(appid)
        if ok:
            await self._inject(appid, label_override="In at0-m Library", locked_override=True)
        else:
            await self._inject(appid, label_override="Failed — Retry", locked_override=False)

    async def _main_loop(self) -> None:
        import aiohttp
        from utils import store_patch

        self._loop = asyncio.get_running_loop()
        while not self._stop_event.is_set():
            if not self._should_run():
                await asyncio.sleep(_POLL_INTERVAL_SEC)
                continue

            status = await asyncio.to_thread(store_patch.cef_status, 0.45)
            ws_url = status.get("websocket_url") or ""
            if not status.get("store_tab") or not ws_url:
                await asyncio.sleep(_POLL_INTERVAL_SEC)
                continue

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, heartbeat=20.0) as ws:
                        self._ws = ws
                        await self._send_cdp("Page.enable")
                        await self._send_cdp("Runtime.enable")
                        appid = _extract_appid(status.get("url", ""))
                        if appid:
                            await asyncio.sleep(0.5)
                            await self._inject(appid)

                        async for msg in ws:
                            if self._stop_event.is_set() or not self._should_run():
                                break
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue
                            try:
                                data = json.loads(msg.data)
                            except ValueError as e:
                                logger.debug("[CefStoreInjector] Ignoring non-JSON CDP message: %s", e)
                                continue
                            method = data.get("method")
                            if method == "Page.frameNavigated":
                                frame = (data.get("params") or {}).get("frame") or {}
                                if not frame.get("parentId"):
                                    await asyncio.sleep(1.5)
                                    nav_app = _extract_appid(frame.get("url", ""))
                                    if nav_app:
                                        await self._inject(nav_app)
                                    else:
                                        await self._remove()
                            elif method == "Runtime.consoleAPICalled":
                                args = (data.get("params") or {}).get("args") or []
                                for arg in args:
                                    val = arg.get("value") if arg.get("type") == "string" else ""
                                    if val.startswith(_ASSELLA_ADD_TAG):
                                        clicked_id = val[len(_ASSELLA_ADD_TAG):].strip()
                                        if clicked_id.isdigit():
                                            asyncio.create_task(self._handle_add_click(clicked_id))
            except Exception as e:
                logger.debug("[CefStoreInjector] WebSocket session ended: %s", e)
            finally:
                self._ws = None

            await asyncio.sleep(_POLL_INTERVAL_SEC)


_injector_instance: Optional[CefStoreInjector] = None


def get_cef_injector() -> CefStoreInjector:
    global _injector_instance
    if _injector_instance is None:
        _injector_instance = CefStoreInjector()
    return _injector_instance
