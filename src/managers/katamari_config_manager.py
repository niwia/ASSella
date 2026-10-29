"""Katamari Configuration Manager for ASSella and SLSsteam Lua Plugins.

Manages the Katamari 'mother config' (~/.config/SLSsteam/katamari.conf.lua).
Acts as the single source of truth for the Katamari family:
  - family: manifest delivery mode ('auto', 'bakeneko-first', 'spacebunny-only', 'bakeneko-only')
  - hubcap: shared credentials and resolver settings
  - proxy: Cloudflare worker fallback for Bakeneko and bypass transport
  - bakeneko: manifest pre-seeder settings
  - spacebunny: MRC provider racing and authority hierarchy
  - styx: ownership ticket splicing
  - lobster: automatic config.yaml recovery for depots and keys

Preserves existing user comments and table structure during in-place updates.
Strictly NO emojis used anywhere.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from utils.settings import get_settings

logger = logging.getLogger(__name__)


def get_sls_config_dir() -> Path:
    """Return the SLSsteam configuration directory, respecting Native/Flatpak Steam environments."""
    try:
        from core.steam_helpers import get_steam_env
        return get_steam_env().sls_config_dir
    except Exception as e:
        logger.debug(f"[KatamariConfigManager] Error resolving SteamEnv for SLS config dir: {e}")
        return Path(os.path.expanduser("~/.config/SLSsteam"))


def get_katamari_conf_path() -> Path:
    """Return canonical path to ~/.config/SLSsteam/katamari.conf.lua."""
    return get_sls_config_dir() / "katamari.conf.lua"


def get_mrc_legacy_path() -> Path:
    """Return path to legacy mrc_config.lua."""
    return get_sls_config_dir() / "mrc_config.lua"


DEFAULT_KATAMARI_CONFIG: Dict[str, Any] = {
    "family": {
        "order": ["bakeneko", "spacebunny", "hubcap", "styx", "lobster", "bypass"],
        "manifest_mode": "auto",
        "allow_unknown": True,
    },
    "hubcap": {
        "api_key": "",
        "base_url": "https://hubcapmanifest.com/api/v1",
        "timeout": 30,
        "enabled": False,
        "api_key_timestamp": 0,
        "timeout_sec": 5,
        "write_config": True,
        "notify": False,
        "preflight": "contents",
        "depot_index": False,
        "write_depot_comments": False,
    },
    "proxy": {
        "worker_url": "",
        "worker_token": "",
        "mode": "fallback",
        "cooldown": 60,
        "enabled": True,
        "transport": "auto",
        "intercept": True,
        "direct_fallback": True,
        "timeout": 10,
        "connect_timeout": 6,
        "probe_timeout": 5,
        "max_probe": 4,
        "subscription_refresh_sec": 3600,
        "proxy_cooldown_sec": 120,
    },
    "bakeneko": {
        "enabled": True,
        "mode": "before",
        "priority": 10,
        "apps": [],
        "auto_pending": False,
        "only_pending": False,
        "dry_run": True,
        "overwrite": False,
        "repair_missing": True,
        "max_apps": 8,
        "max_manifests": 32,
        "run_on_initialized": False,
        "include_uninstalled_depots": False,
        "debug": False,
    },
    "spacebunny": {
        "standalone": False,
        "mrc_enabled": True,
        "priority": 20,
        "authority": "manifestdex",
        "rescue": "wudrm",
        "full_timeout_ms": 2000,
        "probe_timeout_ms": 700,
        "probe_timeouts_to_mark_down": 2,
        "down_ttl_ms": 60000,
        "provisional_ttl_s": 300,
        "fail_ttl_s": 120,
    },
    "styx": {
        "enabled": True,
        "fallback_app": 480,
        "secondary_fallback": 291550,
        "splice_mode": "insert",
        "return_expanded_size": False,
        "max_ticket_size": 65536,
        "debug": False,
        "refresh_retry_seconds": 5,
        "probe_window_seconds": 60,
        "max_warmup_requests": 120,
        "account_info_msg_type": 768,
    },
    "lobster": {
        "enabled": True,
        "auto_recover": True,
        "notify": True,
        "run_on_reload": False,
    },
}


class KatamariConfigManager:
    """Manages reading, modifying, and syncing katamari.conf.lua."""

    @staticmethod
    def _extract_section_block(text: str, section_name: str) -> str:
        """Extract table block e.g. bakeneko = { ... } by counting brace depth."""
        m = re.search(r'\b' + re.escape(section_name) + r'\s*=\s*\{', text)
        if not m:
            return ""
        start = m.end() - 1
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        return text[start:end]

    @staticmethod
    def _parse_bool(val: str) -> Optional[bool]:
        v = val.strip().lower()
        if v.startswith("true"):
            return True
        if v.startswith("false"):
            return False
        return None

    @staticmethod
    def _parse_string(val: str) -> Optional[str]:
        m = re.search(r'"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\'', val)
        if m:
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            return raw.encode().decode('unicode_escape')
        return None

    @staticmethod
    def _parse_int(val: str) -> Optional[int]:
        m = re.search(r'(-?\d+)', val)
        if m:
            try:
                return int(m.group(1))
            except ValueError as e:
                logger.debug(f"[KatamariConfigManager] Failed to parse int from '{m.group(1)}': {e}")
        return None

    @classmethod
    def load_config(cls, conf_path: Optional[Path] = None) -> Dict[str, Any]:
        """Load configuration from katamari.conf.lua, falling back to defaults."""
        path = conf_path or get_katamari_conf_path()
        cfg = {
            "family": dict(DEFAULT_KATAMARI_CONFIG["family"]),
            "hubcap": dict(DEFAULT_KATAMARI_CONFIG["hubcap"]),
            "proxy": dict(DEFAULT_KATAMARI_CONFIG["proxy"]),
            "bakeneko": dict(DEFAULT_KATAMARI_CONFIG["bakeneko"]),
            "spacebunny": dict(DEFAULT_KATAMARI_CONFIG["spacebunny"]),
            "styx": dict(DEFAULT_KATAMARI_CONFIG["styx"]),
            "lobster": dict(DEFAULT_KATAMARI_CONFIG["lobster"]),
        }

        if not path.is_file():
            return cfg

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")

            # 1. Family section
            fam_block = cls._extract_section_block(content, "family")
            if fam_block:
                m_mode = re.search(r'\bmanifest_mode\s*=\s*([^,\n\r]+)', fam_block)
                if m_mode:
                    parsed_s = cls._parse_string(m_mode.group(1))
                    if parsed_s:
                        cfg["family"]["manifest_mode"] = parsed_s
                m_unk = re.search(r'\ballow_unknown\s*=\s*([^,\n\r]+)', fam_block)
                if m_unk:
                    parsed_b = cls._parse_bool(m_unk.group(1))
                    if parsed_b is not None:
                        cfg["family"]["allow_unknown"] = parsed_b

            # 2. Hubcap section
            hub_block = cls._extract_section_block(content, "hubcap")
            if hub_block:
                m_key = re.search(r'\bapi_key\s*=\s*([^,\n\r]+)', hub_block)
                if m_key:
                    parsed_s = cls._parse_string(m_key.group(1))
                    if parsed_s is not None:
                        cfg["hubcap"]["api_key"] = parsed_s
                m_url = re.search(r'\bbase_url\s*=\s*([^,\n\r]+)', hub_block)
                if m_url:
                    parsed_s = cls._parse_string(m_url.group(1))
                    if parsed_s:
                        cfg["hubcap"]["base_url"] = parsed_s
                m_en = re.search(r'\benabled\s*=\s*([^,\n\r]+)', hub_block)
                if m_en:
                    parsed_b = cls._parse_bool(m_en.group(1))
                    if parsed_b is not None:
                        cfg["hubcap"]["enabled"] = parsed_b

            # 3. Proxy section
            prx_block = cls._extract_section_block(content, "proxy")
            if prx_block:
                m_wurl = re.search(r'\bworker_url\s*=\s*([^,\n\r]+)', prx_block)
                if m_wurl:
                    parsed_s = cls._parse_string(m_wurl.group(1))
                    if parsed_s is not None:
                        cfg["proxy"]["worker_url"] = parsed_s
                m_wtok = re.search(r'\bworker_token\s*=\s*([^,\n\r]+)', prx_block)
                if m_wtok:
                    parsed_s = cls._parse_string(m_wtok.group(1))
                    if parsed_s is not None:
                        cfg["proxy"]["worker_token"] = parsed_s
                m_pmode = re.search(r'\bmode\s*=\s*([^,\n\r]+)', prx_block)
                if m_pmode:
                    parsed_s = cls._parse_string(m_pmode.group(1))
                    if parsed_s:
                        cfg["proxy"]["mode"] = parsed_s

            # 4. Bakeneko section
            bak_block = cls._extract_section_block(content, "bakeneko")
            if bak_block:
                m_en = re.search(r'\benabled\s*=\s*([^,\n\r]+)', bak_block)
                if m_en:
                    parsed_b = cls._parse_bool(m_en.group(1))
                    if parsed_b is not None:
                        cfg["bakeneko"]["enabled"] = parsed_b
                m_bmode = re.search(r'\bmode\s*=\s*([^,\n\r]+)', bak_block)
                if m_bmode:
                    parsed_s = cls._parse_string(m_bmode.group(1))
                    if parsed_s:
                        cfg["bakeneko"]["mode"] = parsed_s
                m_ap = re.search(r'\bauto_pending\s*=\s*([^,\n\r]+)', bak_block)
                if m_ap:
                    parsed_b = cls._parse_bool(m_ap.group(1))
                    if parsed_b is not None:
                        cfg["bakeneko"]["auto_pending"] = parsed_b
                m_dry = re.search(r'\bdry_run\s*=\s*([^,\n\r]+)', bak_block)
                if m_dry:
                    parsed_b = cls._parse_bool(m_dry.group(1))
                    if parsed_b is not None:
                        cfg["bakeneko"]["dry_run"] = parsed_b
                m_maxm = re.search(r'\bmax_manifests\s*=\s*([^,\n\r]+)', bak_block)
                if m_maxm:
                    parsed_i = cls._parse_int(m_maxm.group(1))
                    if parsed_i is not None:
                        cfg["bakeneko"]["max_manifests"] = parsed_i

            # 5. SpaceBunny section (including MRC sub-block)
            sb_block = cls._extract_section_block(content, "spacebunny")
            if sb_block:
                m_en = re.search(r'\bmrc_enabled\s*=\s*([^,\n\r]+)', sb_block)
                if m_en:
                    parsed_b = cls._parse_bool(m_en.group(1))
                    if parsed_b is not None:
                        cfg["spacebunny"]["mrc_enabled"] = parsed_b
                m_st = re.search(r'\bstandalone\s*=\s*([^,\n\r]+)', sb_block)
                if m_st:
                    parsed_b = cls._parse_bool(m_st.group(1))
                    if parsed_b is not None:
                        cfg["spacebunny"]["standalone"] = parsed_b

                # MRC providers hierarchy inside spacebunny
                mrc_block = cls._extract_section_block(sb_block, "mrc")
                if mrc_block:
                    provs = re.findall(r'name\s*=\s*["\']([^"\']+)["\']', mrc_block)
                    if len(provs) >= 1:
                        cfg["spacebunny"]["authority"] = provs[0]
                    if len(provs) >= 2:
                        cfg["spacebunny"]["rescue"] = provs[1]
                    m_fsec = re.search(r'\bFULL_SEC\s*=\s*([^,\n\r]+)', mrc_block)
                    if m_fsec:
                        sec_val = cls._parse_int(m_fsec.group(1))
                        if sec_val is not None:
                            cfg["spacebunny"]["full_timeout_ms"] = sec_val * 1000
                    m_psec = re.search(r'\bPROBE_SEC\s*=\s*([^,\n\r]+)', mrc_block)
                    if m_psec:
                        sec_val = cls._parse_int(m_psec.group(1))
                        if sec_val is not None:
                            cfg["spacebunny"]["probe_timeout_ms"] = sec_val * 1000

            # 6. Styx section
            styx_block = cls._extract_section_block(content, "styx")
            if styx_block:
                m_en = re.search(r'\benabled\s*=\s*([^,\n\r]+)', styx_block)
                if m_en:
                    parsed_b = cls._parse_bool(m_en.group(1))
                    if parsed_b is not None:
                        cfg["styx"]["enabled"] = parsed_b
                m_fa = re.search(r'\bfallback_app\s*=\s*([^,\n\r]+)', styx_block)
                if m_fa:
                    parsed_i = cls._parse_int(m_fa.group(1))
                    if parsed_i is not None:
                        cfg["styx"]["fallback_app"] = parsed_i
                m_sm = re.search(r'\bsplice_mode\s*=\s*([^,\n\r]+)', styx_block)
                if m_sm:
                    parsed_s = cls._parse_string(m_sm.group(1))
                    if parsed_s:
                        cfg["styx"]["splice_mode"] = parsed_s

            # 7. Lobster section
            lob_block = cls._extract_section_block(content, "lobster")
            if lob_block:
                m_en = re.search(r'\benabled\s*=\s*([^,\n\r]+)', lob_block)
                if m_en:
                    parsed_b = cls._parse_bool(m_en.group(1))
                    if parsed_b is not None:
                        cfg["lobster"]["enabled"] = parsed_b
                m_ar = re.search(r'\bauto_recover\s*=\s*([^,\n\r]+)', lob_block)
                if m_ar:
                    parsed_b = cls._parse_bool(m_ar.group(1))
                    if parsed_b is not None:
                        cfg["lobster"]["auto_recover"] = parsed_b
                m_not = re.search(r'\bnotify\s*=\s*([^,\n\r]+)', lob_block)
                if m_not:
                    parsed_b = cls._parse_bool(m_not.group(1))
                    if parsed_b is not None:
                        cfg["lobster"]["notify"] = parsed_b

        except Exception as exc:
            logger.warning(f"[KatamariConfigManager] Error reading katamari.conf.lua: {exc}")

        return cfg

    @classmethod
    def _update_key_in_section(cls, text: str, section_name: str, key: str, new_val_str: str) -> str:
        """Replace key = value in a table block in-place, preserving surrounding comments."""
        m = re.search(r'\b' + re.escape(section_name) + r'\s*=\s*\{', text)
        if not m:
            return text

        start_brace = m.end() - 1
        depth = 0
        end_brace = start_brace
        for i in range(start_brace, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end_brace = i + 1
                    break

        block = text[start_brace:end_brace]
        pattern = r'(\b' + re.escape(key) + r'\s*=\s*)([^,\n\r]+)(,?)'
        if re.search(pattern, block):
            new_block = re.sub(pattern, r'\g<1>' + new_val_str + r'\g<3>', block, count=1)
        else:
            # Insert before final closing brace
            last_brace_idx = block.rfind('}')
            if last_brace_idx != -1:
                new_block = block[:last_brace_idx] + f'\t\t{key} = {new_val_str},\n\t' + block[last_brace_idx:]
            else:
                new_block = block

        return text[:start_brace] + new_block + text[end_brace:]

    @classmethod
    def generate_canonical_template(cls, cfg: Dict[str, Any]) -> str:
        """Generate a complete canonical katamari.conf.lua template."""
        fam = cfg.get("family", {})
        prx = cfg.get("proxy", {})
        bak = cfg.get("bakeneko", {})
        sb = cfg.get("spacebunny", {})
        sty = cfg.get("styx", {})
        lob = cfg.get("lobster", {})
        hub = cfg.get("hubcap", {})

        auth_prov = sb.get("authority", "manifestdex")
        resc_prov = sb.get("rescue", "wudrm")
        full_sec = max(1, int(sb.get("full_timeout_ms", 2000)) // 1000)
        probe_sec = max(1, int(sb.get("probe_timeout_ms", 700)) // 1000)

        manifest_mode = fam.get("manifest_mode", "auto")
        bak_mode = bak.get("mode", "before")
        bak_enabled = "true" if bak.get("enabled", True) else "false"
        bak_autopending = "true" if bak.get("auto_pending", False) else "false"
        bak_dryrun = "true" if bak.get("dry_run", True) else "false"
        bak_max = int(bak.get("max_manifests", 32))

        styx_enabled = "true" if sty.get("enabled", True) else "false"
        styx_fallback = int(sty.get("fallback_app", 480))
        styx_mode = sty.get("splice_mode", "insert")

        lob_enabled = "true" if lob.get("enabled", True) else "false"
        lob_autorecover = "true" if lob.get("auto_recover", True) else "false"
        lob_notify = "true" if lob.get("notify", True) else "false"

        worker_url = prx.get("worker_url", "")
        worker_tok = prx.get("worker_token", "")
        prx_mode = prx.get("mode", "fallback")

        hub_key = hub.get("api_key", "")
        hub_url = hub.get("base_url", "https://hubcapmanifest.com/api/v1")

        template = f"""-- katamari.conf.lua (0.2)
--
-- MOTHER CONFIG for the Katamari family.
-- Managed automatically by ASSella.

return {{
	family = {{
		order = {{
			"bakeneko",
			"spacebunny",
			"hubcap",
			"styx",
			"lobster",
			"bypass",
		}},
		manifest_mode = "{manifest_mode}",
		allow_unknown = true,
	}},

	hubcap = {{
		api_key = "{hub_key}",
		base_url = "{hub_url}",
		timeout = 30,
		enabled = false,
		api_key_timestamp = 0,
		timeout_sec = 5,
		write_config = true,
		notify = false,
		preflight = "contents",
		depot_index = false,
		write_depot_comments = false,
	}},

	proxy = {{
		worker_url = "{worker_url}",
		worker_token = "{worker_tok}",
		mode = "{prx_mode}",
		cooldown = 60,
		enabled = true,
		proxy = nil,
		subscription = nil,
		transport = "auto",
		intercept = true,
		direct_fallback = true,
		hosts = {{
			"manifest.manifestdex.com",
			"gmrc.wudrm.com",
			"manifest.opensteamtool.com",
			"hubcapmanifest.com",
		}},
		timeout = 10,
		connect_timeout = 6,
		probe_url = "https://manifest.manifestdex.com/900001",
		probe_timeout = 5,
		max_probe = 4,
		subscription_refresh_sec = 3600,
		proxy_cooldown_sec = 120,
	}},

	bakeneko = {{
		enabled = {bak_enabled},
		mode = "{bak_mode}",
		priority = 10,
		apps = {{}},
		auto_pending = {bak_autopending},
		only_pending = false,
		dry_run = {bak_dryrun},
		overwrite = false,
		repair_missing = true,
		max_apps = 8,
		max_manifests = {bak_max},
		run_on_initialized = false,
		include_uninstalled_depots = false,
		cache_dir = "",
		depotcache_dirs = {{}},
		debug = false,
	}},

	spacebunny = {{
		standalone = false,
		mrc_enabled = true,
		priority = 20,
		mrc = {{
			providers = {{
				{{
					name = "{auth_prov}",
					url = "{'https://manifest.manifestdex.com/' if auth_prov == 'manifestdex' else 'https://gmrc.wudrm.com/manifest/'}",
					proxy_url = false,
					headers = {"{ 'User-Agent: ManifestDeX/1.0' }" if auth_prov == 'manifestdex' else 'nil'},
					provisional = true,
					timeout = 5,
				}},
				{{
					name = "{resc_prov}",
					url = "{'https://gmrc.wudrm.com/manifest/' if resc_prov == 'wudrm' else 'https://manifest.manifestdex.com/'}",
					proxy_url = false,
					headers = nil,
					provisional = false,
					timeout = 4,
				}},
			}},
			tuning = {{
				FULL_SEC = {full_sec},
				PROBE_SEC = {probe_sec},
				TOTAL_BUDGET_SEC = 10,
				MIN_ATTEMPT_SEC = 1,
				PROBE_TIMEOUTS_TO_MARK_DOWN = 2,
				DOWN_TTL_MS = 60000,
				PROVISIONAL_TTL = 300,
				FAIL_TTL = 120,
			}},
		}},
	}},

	styx = {{
		enabled = {styx_enabled},
		fallback_app = {styx_fallback},
		secondary_fallback = 291550,
		splice_mode = "{styx_mode}",
		return_expanded_size = false,
		max_ticket_size = 65536,
		debug = false,
		refresh_retry_seconds = 5,
		probe_window_seconds = 60,
		max_warmup_requests = 120,
		account_info_msg_type = 768,
		apps = nil,
	}},

	lobster = {{
		enabled = {lob_enabled},
		auto_recover = {lob_autorecover},
		notify = {lob_notify},
		run_on_reload = false,
	}},

	bypass = {{}},
}}
"""
        return template

    @classmethod
    def save_config(cls, cfg: Dict[str, Any], conf_path: Optional[Path] = None) -> bool:
        """Save configuration in-place preserving existing structure, or generate template."""
        path = conf_path or get_katamari_conf_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            if not path.is_file():
                content = cls.generate_canonical_template(cfg)
                path.write_text(content, encoding="utf-8")
                cls.archive_legacy_mrc_config()
                logger.info(f"[KatamariConfigManager] Created fresh katamari.conf.lua at {path}")
                return True

            content = path.read_text(encoding="utf-8", errors="ignore")

            # In-place updates
            fam = cfg.get("family", {})
            if "manifest_mode" in fam:
                content = cls._update_key_in_section(content, "family", "manifest_mode", f'"{fam["manifest_mode"]}"')

            prx = cfg.get("proxy", {})
            if "worker_url" in prx:
                content = cls._update_key_in_section(content, "proxy", "worker_url", f'"{prx["worker_url"]}"')
            if "worker_token" in prx:
                content = cls._update_key_in_section(content, "proxy", "worker_token", f'"{prx["worker_token"]}"')
            if "mode" in prx:
                content = cls._update_key_in_section(content, "proxy", "mode", f'"{prx["mode"]}"')

            bak = cfg.get("bakeneko", {})
            if "enabled" in bak:
                content = cls._update_key_in_section(content, "bakeneko", "enabled", "true" if bak["enabled"] else "false")
            if "mode" in bak:
                content = cls._update_key_in_section(content, "bakeneko", "mode", f'"{bak["mode"]}"')
            if "auto_pending" in bak:
                content = cls._update_key_in_section(content, "bakeneko", "auto_pending", "true" if bak["auto_pending"] else "false")
            if "dry_run" in bak:
                content = cls._update_key_in_section(content, "bakeneko", "dry_run", "true" if bak["dry_run"] else "false")
            if "max_manifests" in bak:
                content = cls._update_key_in_section(content, "bakeneko", "max_manifests", str(int(bak["max_manifests"])))

            sb = cfg.get("spacebunny", {})
            if "authority" in sb or "rescue" in sb or "full_timeout_ms" in sb or "probe_timeout_ms" in sb:
                # Update mrc block
                full_sec = max(1, int(sb.get("full_timeout_ms", 2000)) // 1000)
                probe_sec = max(1, int(sb.get("probe_timeout_ms", 700)) // 1000)
                auth_prov = sb.get("authority", "manifestdex")
                resc_prov = sb.get("rescue", "wudrm")

                content = cls._update_key_in_section(content, "spacebunny", "FULL_SEC", str(full_sec))
                content = cls._update_key_in_section(content, "spacebunny", "PROBE_SEC", str(probe_sec))

            sty = cfg.get("styx", {})
            if "enabled" in sty:
                content = cls._update_key_in_section(content, "styx", "enabled", "true" if sty["enabled"] else "false")
            if "fallback_app" in sty:
                content = cls._update_key_in_section(content, "styx", "fallback_app", str(int(sty["fallback_app"])))
            if "splice_mode" in sty:
                content = cls._update_key_in_section(content, "styx", "splice_mode", f'"{sty["splice_mode"]}"')

            lob = cfg.get("lobster", {})
            if "enabled" in lob:
                content = cls._update_key_in_section(content, "lobster", "enabled", "true" if lob["enabled"] else "false")
            if "auto_recover" in lob:
                content = cls._update_key_in_section(content, "lobster", "auto_recover", "true" if lob["auto_recover"] else "false")
            if "notify" in lob:
                content = cls._update_key_in_section(content, "lobster", "notify", "true" if lob["notify"] else "false")

            # Write in-place preserving inode
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            cls.archive_legacy_mrc_config()
            logger.info(f"[KatamariConfigManager] Successfully synced settings to {path}")
            return True
        except Exception as exc:
            logger.error(f"[KatamariConfigManager] Failed saving {path}: {exc}")
            return False

    @classmethod
    def archive_legacy_mrc_config(cls) -> bool:
        """Rename legacy mrc_config.lua to mrc_config.lua.bak if found."""
        try:
            mrc_path = get_mrc_legacy_path()
            if mrc_path.is_file():
                bak_path = mrc_path.with_name("mrc_config.lua.bak")
                if bak_path.is_file():
                    bak_path.unlink()
                mrc_path.rename(bak_path)
                logger.info(f"[KatamariConfigManager] Archived legacy {mrc_path.name} -> {bak_path.name}")
                return True
        except Exception as exc:
            logger.warning(f"[KatamariConfigManager] Error archiving legacy mrc_config.lua: {exc}")
        return False

