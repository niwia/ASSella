"""
Ticket Manager module for SLSsteam ticket import, export, sanitation, and validation.
Manages AppOwnershipTicket and EncryptedAppTicket YAML files in ~/.config/SLSsteam/Tickets/
"""

import os
import re
import shutil
import logging
import base64
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger(__name__)

def get_sls_config_dir() -> Path:
    """Return the SLSsteam configuration directory, respecting Native/Flatpak Steam environments."""
    try:
        from core.steam_helpers import get_steam_env
        return get_steam_env().sls_config_dir
    except Exception as e:
        logger.warning(f"Error resolving SteamEnv for tickets: {e}")
        return Path.home() / ".config" / "SLSsteam"


def get_tickets_dir() -> Path:
    """Get the path to SLSsteam Tickets directory, creating it if needed."""
    tdir = get_sls_config_dir() / "Tickets"
    try:
        tdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create tickets directory {tdir}: {e}")
    return tdir


SLS_CONFIG_DIR = Path.home() / ".config" / "SLSsteam"
TICKETS_DIR = SLS_CONFIG_DIR / "Tickets"


def get_ticket_status(appid: str) -> Dict[str, Any]:
    """
    Check if ticket files exist for an AppID and return details.
    
    Returns:
        Dict containing:
            'exists': bool
            'has_ownership_ticket': bool
            'has_encrypted_ticket': bool
            'ownership_path': Optional[str]
            'encrypted_path': Optional[str]
            'steam_id': Optional[str]
            'updated_at': Optional[str]
    """
    appid_str = str(appid).strip()
    tdir = get_tickets_dir()
    cdir = get_sls_config_dir() / "cache"

    candidates = [
        tdir / f"ticket_{appid_str}.yaml",
        tdir / f"encryptedTicket_{appid_str}.yaml",
        tdir / f"{appid_str}.yaml",
        tdir / f"app_{appid_str}.ticket",
        cdir / f"ticket_{appid_str}.yaml",
        cdir / f"encryptedTicket_{appid_str}.yaml",
        cdir / f"{appid_str}.yaml",
    ]

    actual_ownership_path = None
    for c in candidates:
        if c.exists():
            actual_ownership_path = str(c)
            break

    has_ownership = actual_ownership_path is not None
    has_encrypted = (tdir / f"encryptedTicket_{appid_str}.yaml").exists() or (cdir / f"encryptedTicket_{appid_str}.yaml").exists()

    steam_id = None
    updated_at = None

    if actual_ownership_path and os.path.exists(actual_ownership_path):
        try:
            mtime = os.path.getmtime(actual_ownership_path)
            from datetime import datetime
            updated_at = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

            with open(actual_ownership_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                m = re.search(r"steamId:\s*(\d+)", content)
                if m:
                    steam_id = m.group(1)
        except Exception as e:
            logger.debug(f"Error reading ticket metadata from {actual_ownership_path}: {e}")

    return {
        "exists": has_ownership or has_encrypted,
        "has_ownership_ticket": has_ownership,
        "has_encrypted_ticket": has_encrypted,
        "ownership_path": actual_ownership_path,
        "encrypted_path": str(cdir / f"encryptedTicket_{appid_str}.yaml") if has_encrypted else None,
        "steam_id": steam_id,
        "updated_at": updated_at,
    }


def _get_active_steam_id_64() -> str:
    """Retrieve active user's SteamID64 from loginusers.vdf, or fallback.

    Uses SteamEnv for Flatpak-aware path detection.
    """
    try:
        from core.steam_helpers import get_steam_env
        env = get_steam_env()
        candidate_paths = []
        if env.loginusers_path:
            candidate_paths.append(env.loginusers_path)
        # Extra fallbacks in case env resolution missed something
        candidate_paths += [
            Path.home() / ".local" / "share" / "Steam" / "config" / "loginusers.vdf",
            Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "config" / "loginusers.vdf",
        ]
    except Exception:
        candidate_paths = [
            Path.home() / ".local" / "share" / "Steam" / "config" / "loginusers.vdf",
            Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam" / "config" / "loginusers.vdf",
        ]

    for vdf_path in candidate_paths:
        try:
            if vdf_path.exists():
                content = vdf_path.read_text(encoding="utf-8", errors="ignore")
                matches = re.findall(r'"(7656119\d{10})"', content)
                if matches:
                    return matches[0]
        except Exception:
            continue
    return "76561198000000000"


def validate_ticket_content(raw_data: str, target_appid: Optional[str] = None) -> Dict[str, Any]:
    """
    Sanitize and validate ticket data (raw text, YAML, or base64 string).
    Converts 32-bit AccountIDs to valid SteamID64 format.
    """
    if not raw_data or not raw_data.strip():
        return {"valid": False, "error": "Empty ticket data provided."}

    text = raw_data.strip()
    steam_id = None
    ticket_b64 = None
    ticket_type = "ticket"
    detected_appid = None

    # 1. Parse YAML format (e.g. steamId: ..., ticket: ...)
    steam_match = re.search(r"steamId:\s*(\d+)", text)
    if steam_match:
        parsed_id = int(steam_match.group(1))
        if parsed_id < 76561197960265728:
            # 32-bit AccountID detected — convert to SteamID64 or active user SteamID
            active_id = _get_active_steam_id_64()
            steam_id = active_id if active_id != "76561198000000000" else str(parsed_id + 76561197960265728)
        else:
            steam_id = str(parsed_id)
    else:
        steam_id = _get_active_steam_id_64()

    ticket_match = re.search(r"(ticket|encryptedTicket):\s*([A-Za-z0-9+/=\s]+)", text)
    if ticket_match:
        ticket_type = ticket_match.group(1)
        ticket_b64 = re.sub(r"\s+", "", ticket_match.group(2))
    else:
        # Check if the raw text itself is base64
        cleaned_b64 = re.sub(r"\s+", "", text)
        if len(cleaned_b64) > 16 and re.match(r"^[A-Za-z0-9+/=]+$", cleaned_b64):
            ticket_b64 = cleaned_b64

    if not ticket_b64:
        return {"valid": False, "error": "Could not locate valid base64 ticket payload in file/text."}

    # Verify base64 decoding
    try:
        raw_bytes = base64.b64decode(ticket_b64)
        if len(raw_bytes) < 8:
            return {"valid": False, "error": "Decoded ticket payload is too short or invalid."}
    except Exception as e:
        return {"valid": False, "error": f"Invalid base64 payload: {e}"}

    # Attempt to extract AppID from payload if detectable
    if target_appid:
        detected_appid = str(target_appid)

    return {
        "valid": True,
        "steam_id": steam_id,
        "ticket_b64": ticket_b64,
        "ticket_type": ticket_type,
        "detected_appid": detected_appid,
        "error": None,
    }


def validate_ticket_file(file_path: str, target_appid: Optional[str] = None) -> Dict[str, Any]:
    """Sanitize and validate a ticket file on disk."""
    path = Path(file_path)
    if not path.exists():
        return {"valid": False, "error": f"File does not exist: {file_path}"}

    fn = path.name
    m = re.search(r"(\d{4,9})", fn)
    filename_appid = m.group(1) if m else None

    try:
        try:
            content_text = path.read_text(encoding="utf-8")
            res = validate_ticket_content(content_text, target_appid or filename_appid)
            res["filename_appid"] = filename_appid
            return res
        except UnicodeDecodeError:
            # Binary ticket file: convert to base64
            with open(path, "rb") as f:
                b_data = f.read()
            b64_str = base64.b64encode(b_data).decode("ascii")
            return {
                "valid": True,
                "steam_id": _get_active_steam_id_64(),
                "ticket_b64": b64_str,
                "ticket_type": "ticket",
                "detected_appid": target_appid or filename_appid,
                "filename_appid": filename_appid,
                "error": None,
            }

    except Exception as e:
        return {"valid": False, "error": f"Failed to read file: {e}"}


def import_ticket(file_path_or_text: str, appid: str, steam_id: Optional[str] = None) -> Tuple[bool, str]:
    """
    Import a ticket file into ~/.config/SLSsteam/Tickets/ticket_{appid}.yaml
    and register the AppID in AdditionalApps. Preserves original ticket steamId intact.
    """
    appid_str = str(appid).strip()
    if not appid_str or appid_str in ("0", "N/A", "unknown"):
        return False, "Invalid AppID specified."

    tdir = get_tickets_dir()
    dest_path = tdir / f"ticket_{appid_str}.yaml"

    try:
        if os.path.exists(file_path_or_text):
            # Direct file copy to preserve exact formatting and original steamId
            shutil.copy2(file_path_or_text, dest_path)
        else:
            # Raw text payload import
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(file_path_or_text)

        logger.info(f"Imported ticket for AppID {appid_str} to {dest_path}")

        # Automatically ensure AppID is listed in SLSsteam config.yaml
        try:
            from utils.yaml_config_manager import get_user_config_path, add_additional_app
            cfg_path = get_user_config_path()
            add_additional_app(cfg_path, appid_str)
        except Exception as _e:
            logger.warning(f"Could not auto-add AppID {appid_str} to config.yaml: {_e}")

        return True, f"Successfully imported ticket to {dest_path.name}"

    except Exception as e:
        logger.error(f"Failed to write ticket file {dest_path}: {e}")
        return False, f"Failed to save ticket file: {e}"

def verify_ticket_activation(appid: str) -> Dict[str, Any]:
    """
    Verify if an imported ticket is valid and actively loaded in SLSsteam.

    Returns:
        Dict containing:
            'working': bool (True if ticket is installed, valid, and active in SLSsteam)
            'installed': bool
            'base64_valid': bool
            'sls_active': bool (True if confirmed in ~/.SLSsteam.log)
            'message': str
    """
    appid_str = str(appid)

    status = get_ticket_status(appid_str)

    if not status["exists"] or not status["ownership_path"]:
        return {
            "working": False,
            "installed": False,
            "base64_valid": False,
            "sls_active": False,
            "message": f"No ticket file found for AppID {appid_str}."
        }

    # 1. Base64 payload validation
    b64_valid = False
    try:
        content = Path(status["ownership_path"]).read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"(ticket|encryptedTicket):\s*([A-Za-z0-9+/=\s]+)", content)
        if m:
            b64_str = re.sub(r"\s+", "", m.group(2))
            raw_bytes = base64.b64decode(b64_str)
            if len(raw_bytes) >= 32:
                b64_valid = True
    except Exception as e:
        logger.debug(f"Payload check error for {appid_str}: {e}")

    if not b64_valid:
        return {
            "working": False,
            "installed": True,
            "base64_valid": False,
            "sls_active": False,
            "message": "Ticket file exists, but base64 payload is invalid or corrupted."
        }

    # 2. Check SLSsteam engine log for active verification
    sls_active = False
    log_path = Path.home() / ".SLSsteam.log"
    if log_path.exists():
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            has_depots = bool(re.search(rf"BuildDepotDependency.*{appid_str}.*->\s*1", log_text))
            has_schema = bool(re.search(rf"Using schema.*{appid_str}", log_text))
            if has_depots or has_schema:
                sls_active = True
        except Exception as e:
            logger.debug(f"Error reading SLSsteam log: {e}")

    if sls_active:
        msg = f"Ticket active and verified working in SLSsteam for AppID {appid_str}!"
    else:
        msg = f"Ticket file installed (Base64 Valid). Trigger install via SLS to verify active state."

    return {
        "working": b64_valid and sls_active,
        "installed": True,
        "base64_valid": b64_valid,
        "sls_active": sls_active,
        "message": msg
    }


def export_ticket(appid: str, dest_path: str) -> Tuple[bool, str]:
    """Export an installed or cached ticket file for an AppID to dest_path."""
    appid_str = str(appid).strip()
    status = get_ticket_status(appid_str)

    if status["exists"] and status["ownership_path"]:
        try:
            # Confirm payload is not empty
            content = Path(status["ownership_path"]).read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"(ticket|encryptedTicket):\s*([A-Za-z0-9+/=]+)", content)
            if m and len(m.group(2).strip()) > 16:
                shutil.copy2(status["ownership_path"], dest_path)
                logger.info(f"Exported ticket for AppID {appid_str} to {dest_path}")
                return True, f"Exported ticket successfully to {Path(dest_path).name}"
        except Exception as e:
            logger.error(f"Failed to export ticket: {e}")
            return False, f"Export failed: {e}"

    return False, f"No captured ticket found for AppID {appid_str}.\nRun/launch the game on Steam with SLSsteam enabled to capture its ticket."


def get_ticket_b64_payload(appid: str) -> Optional[str]:
    """Get the raw base64 ticket string for copying/sharing."""
    status = get_ticket_status(appid)
    if not status["exists"] or not status["ownership_path"]:
        return None

    try:
        with open(status["ownership_path"], "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        val = validate_ticket_content(content)
        return val.get("ticket_b64")
    except Exception as e:
        logger.error(f"Error reading ticket payload: {e}")
        return None


def scan_all_available_tickets() -> Dict[str, str]:
    """
    Scan for all installed/cached ticket files in SLSsteam directories.
    Returns a dict mapping appid -> path.
    """
    tdir = get_tickets_dir()
    cdir = get_sls_config_dir() / "cache"
    tickets = {}

    for d in (cdir, tdir):
        if d.exists():
            for p in d.glob("*.yaml"):
                m = re.search(r"(\d{4,9})", p.name)
                if m:
                    # Only include files with non-empty base64 ticket payload
                    try:
                        c = p.read_text(encoding="utf-8", errors="ignore")
                        if re.search(r"(ticket|encryptedTicket):\s*([A-Za-z0-9+/=]+)", c):
                            tickets[m.group(1)] = str(p)
                    except Exception:
                        pass
    return tickets


def get_available_ticket_games() -> List[Dict[str, str]]:
    """
    Scan all available tickets in ~/.config/SLSsteam/ and resolve their game titles.
    Returns a list of dicts: [{'appid': '108600', 'name': 'Project Zomboid', 'display': 'Project Zomboid (108600)'}, ...]
    Sorted alphabetically by game name.
    """
    try:
        tickets = scan_all_available_tickets()
        if not tickets:
            return []

        local_names = {}

        # 1. Scan Steam library appmanifests
        try:
            from core.steam_helpers import get_steam_libraries
            for lib in get_steam_libraries():
                apps_dir = Path(lib) / "steamapps"
                if apps_dir.exists():
                    for acf_file in apps_dir.glob("appmanifest_*.acf"):
                        try:
                            content = acf_file.read_text(encoding="utf-8", errors="ignore")
                            m_appid = re.search(r'"appid"\s+"(\d+)"', content)
                            m_name = re.search(r'"name"\s+"([^"]+)"', content)
                            if m_appid and m_name:
                                local_names[m_appid.group(1)] = m_name.group(1)
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Error scanning steam libraries for game names: {e}")

        # 2. Check install history for cached game names
        try:
            import json
            from utils.helpers import get_data_file_path
            hist_file = get_data_file_path("install_history.json")
            if hist_file.exists():
                hdata = json.loads(hist_file.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(hdata, list):
                    for entry in hdata:
                        if isinstance(entry, dict):
                            aid = str(entry.get("appid") or entry.get("app_id") or "")
                            gname = entry.get("game_name")
                            if aid and aid.isdigit() and gname and aid not in local_names:
                                local_names[aid] = gname
        except Exception as e:
            logger.debug(f"Error reading install history: {e}")

        results = []
        for appid in tickets.keys():
            name = local_names.get(appid)
            display_str = f"{name} ({appid})" if name else f"AppID {appid}"
            results.append({
                "appid": str(appid),
                "name": name or f"AppID {appid}",
                "display": display_str,
                "path": tickets[appid]
            })

        results.sort(key=lambda x: str(x.get("name", "")).lower())
        return results
    except Exception as e:
        logger.error(f"Error in get_available_ticket_games: {e}")
        return []


def export_all_tickets(dest_dir: str) -> Tuple[bool, str, int]:
    """
    Export all ownership tickets found in ~/.config/SLSsteam/Tickets/ to dest_dir.
    Returns (success, message, count).
    """
    tickets = scan_all_available_tickets()
    if not tickets:
        return False, "No ownership ticket files found in ~/.config/SLSsteam/Tickets/.", 0

    out_path = Path(dest_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    exported_count = 0
    for appid, src_file in tickets.items():
        try:
            dest_file = out_path / f"ticket_{appid}.yaml"
            shutil.copy2(src_file, dest_file)
            exported_count += 1
        except Exception as e:
            logger.error(f"Failed exporting ticket for {appid}: {e}")

    if exported_count > 0:
        return True, f"Successfully exported {exported_count} ticket file(s) to {dest_dir}", exported_count
    return False, "Failed to export ticket files.", 0


def remove_ticket(appid: str) -> Tuple[bool, str]:
    """Delete ticket files for an AppID from ~/.config/SLSsteam/Tickets/."""
    status = get_ticket_status(appid)
    if not status["exists"]:
        return False, f"No tickets found for AppID {appid}."

    removed_files = []
    tdir = get_tickets_dir()

    for fn in [f"ticket_{appid}.yaml", f"encryptedTicket_{appid}.yaml", f"{appid}.yaml", f"app_{appid}.ticket"]:
        p = tdir / fn
        if p.exists():
            try:
                p.unlink()
                removed_files.append(p.name)
            except Exception as e:
                logger.error(f"Failed to delete ticket file {p}: {e}")

    if removed_files:
        return True, f"Removed ticket file(s): {', '.join(removed_files)}"
    return False, "Failed to remove ticket files."
