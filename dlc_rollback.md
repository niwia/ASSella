# DLC-Only Mode Version Rollback Specification

## 1. Overview
In ASSella, **DLC-Only Mode** (`is_dlc_only_mode(appid) == True`) is activated when a user legally owns or externally manages the base game, using ASSella strictly to install, decrypt, and manage supplementary DLC depots.

This document details the architectural plan to support historical version rollbacks and updates for DLC depots without disturbing the base game installation.

---

## 2. Current Architecture & Limitations

### Current Behavior:
1. **Depot Downloader & Base Manifests**:
   - In normal rollback mode, ASSella assumes the target is an executable base game depot (e.g. Windows OS depot `367521`).
   - In DLC-Only mode, base game `.acf` creation and Steamless processing are suppressed (`is_dlc_only` checks in `task_manager.py` and `cli_manager.py`).
2. **Current Rollback Failure in DLC-Only Mode**:
   - `_on_builds_download_clicked` currently queries `self.game_data.get("installed_depots", {})` and picks the first matching depot.
   - If multiple DLC depots are installed (e.g. 3 separate DLCs), only one is queued, leaving the rest out of sync.
   - If none match, it falls back to the main base game OS depot (`367521`), which would unintentionally attempt to download the full base game files.
   - The job submission triggers `_do_package_and_submit_manual_job`, which must explicitly mark the rollback job as `dlc_only=True` to prevent creating base game manifests.

---

## 3. Implementation Blueprint

### Workflow Diagram:
```
User selects historical build in Builds Tab
                 │
                 ▼
       is_dlc_only_mode(appid)?
        ├── No  ──► Standard Base Game Rollback
        └── Yes ──► DLC-Only Rollback Flow
                         │
                         ▼
        Read installed DLC depots from {appid}.depot
                         │
                         ▼
     Match against Selected Build's Depots (SteamDB)
                         │
        ┌────────────────┴────────────────┐
        ▼                                 ▼
 Single DLC Depot                  Multiple DLC Depots
        │                                 │
        │                         Prompt User Dialog:
        │                         - "Rollback All Installed DLCs"
        │                         - "Select Specific DLC Depot"
        └────────────────┬────────────────┘
                         │
                         ▼
         Package Target DLC Manifest(s)
                         │
                         ▼
       Submit Task with dlc_only=True flag
                         │
                         ▼
  DepotDownloader fetches only target DLC files
  (Preserves base game files, skips .acf generation)
                         │
                         ▼
  Update {appid}.depot with rolled-back manifest IDs
  Set pin_build/{appid} = True to suppress update alerts
```

### Key Technical Steps:
1. **Target Depot Filtering**:
   - Inspect `get_dlc_only_info(self.appid)` or read `{base_path}/depots/{appid}.depot`.
   - Filter the SteamDB build's depots to include *only* the user's installed DLC depots.
2. **Multi-Depot Rollback Modal**:
   - If a build contains updates for more than one installed DLC, present a choice:
     - `[Rollback All DLCs to this Build]` (recommended default)
     - A checkbox selection list allowing granular depot rollback.
3. **Execution Pipeline**:
   - Download the corresponding manifest(s) for the selected DLC depots.
   - Call DepotDownloader / SteamCMD with explicit `-depot <dlc_depot_id> -manifest <manifest_id>`.
   - Ensure the download destination targets the game root or DLC subfolder without recreating the base game manifest.
4. **Configuration & State Synchronization**:
   - Overwrite the manifest ID lines inside `{base_path}/depots/{appid}.depot` with the newly installed manifest IDs so SLSsteam unlocks the correct DLC assets.
   - Set `pin_build/{appid} = True` so `_check_dlc_only_update` in `manifest_check_task.py` will not immediately flag the rolled-back DLC as out-of-date.
