# Smart Depot & Decryption Key Mapping Specification

This document details how **ASSella** maps **AppIDs**, **AdditionalDepots**, and **DecryptionKeys**, and defines best practices for implementing and improving the **DLC-Only Mode** in **AT0-M** and SLSsteam integrations.

---

## 1. Problem Statement & Architecture Context

In the Steam content delivery ecosystem and SLSsteam emulation layer:
- **AppIDs**: Represent the base game, downloadable content (DLC), companion applications, soundtrack packages, or secondary executables (e.g., dedicated servers, multiplayer clients). Configured under `AdditionalApps` in `~/.config/SLSsteam/config.yaml`.
- **Depots**: Actual content containers containing game files, platform binaries (Windows, Linux, macOS), languages, high-resolution textures, or DLC assets. Configured under `AdditionalDepots`.
- **Decryption Keys**: 64-character hexadecimal AES-256 symmetric keys required to decrypt encrypted depot manifests and chunk data. Configured under `DecryptionKeys: { "<depot_id_or_app_id>": "<hex_key>" }`.

### The Challenge
Steam manifests (`.acf`) and depot lists do not have a uniform 1:1 mapping with AppIDs:
1. **Base Game Depots**: Usually do not declare a `dlcappid` attribute in Steam metadata. They are simply listed under the game's depot list.
2. **DLC Depots**: Some DLCs declare `dlcappid: <dlc_id>` on their depots; others share the depot ID with the DLC AppID; others have multiple numbered depots (e.g., assets depot + audio depot).
3. **License-Only DLCs**: Many DLCs contain no depots or files at all—they only unlock a feature flag, skin, or script inside the base game files. They need an entry in `AdditionalApps` but **no** entries in `AdditionalDepots` or `DecryptionKeys`.
4. **Decryption Key Associations**: Keys are keyed by **Depot ID**, not AppID. If a user enables or disables an AppID in `AdditionalApps`, SLSsteam needs to know exactly which Depot IDs and which Decryption Keys must be toggled in tandem.

---

## 2. The 7-Tier Smart Mapping Pipeline

ASSella implements a multi-tier heuristic pipeline in `_build_app_to_depots_map()` (`src/ui/dialogs/game_details/depots_tab.py`). It builds a directed mapping:
```
AppID (str) -> Set[DepotID (str)]
```

```
                       ┌──────────────────────────────────────────────┐
                       │ State AppIDs (Base Game, DLCs, Secondary)    │
                       └──────────────────────┬───────────────────────┘
                                              │
    ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
    ▼                                         ▼                                         ▼
[Tier 1: SQLite DB]                  [Tier 2: ACF & Manifests]               [Tier 3: Lua Script AST]
• get_app_info(appid)                • installed_depots                     • "(AppID: <id>)" blocks
• dlcappid matches                   • game_data["depots"]                  • "-- MAIN GAME DEPOTS"
• [DLC <id>] regex                   • dlcappid matches                     • addappid(<depotid>, key)
    │                                         │                                         │
    └─────────────────────────────────────────┼─────────────────────────────────────────┘
                                              │
    ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
    ▼                                         ▼                                         ▼
[Tier 4: Plugin Library]             [Tier 5: Secondary Luas]               [Tier 6: Identity Match]
• plugins.json game profiles         • cached_luas/<aid>.lua                • did == aid
• game_record["depots"]              • secondary app addappid()             • 1:1 DLC/Depot identity
    │                                         │                                         │
    └─────────────────────────────────────────┼─────────────────────────────────────────┘
                                              │
                                              ▼
                                 [Tier 7: Unclaimed Catch-All]
                                 • Any remaining depot in game
                                   not claimed by any DLC/app
                                   belongs to Base Game AppID
                                              │
                                              ▼
                                 ┌─────────────────────────┐
                                 │ Complete App->Depots Map│
                                 │ Base App -> {Depots}    │
                                 │ DLC Apps -> {Depots}    │
                                 │ Other Apps-> {Depots}   │
                                 └─────────────────────────┘
```

### Tier 1: SQLite App & Depot Database (`db_manager.py`)
Queries the local SQLite database (`depot_keys.db` and `steam_headers.db`):
- For `appid_str` (the primary game):
  - Iterates over all depots in `app_info["depots"]`.
  - If `d_meta.get("dlcappid")` is present and valid, maps the depot to that DLC AppID.
  - If `d_meta.get("is_dlc")` is true or description contains `[DLC <id>]`, extracts the DLC AppID via regex `\[DLC\s*(\d+)\]` and maps to it.
  - Otherwise (if no DLC attribution exists), maps the depot to the **base game AppID** (`appid_str`).
- For other AppIDs in `state_apps` (secondary apps or expansions):
  - Queries `db.get_app_info(aid)`. If records exist, maps its depots to `aid`.

### Tier 2: Installed Depots & Manifests (`appmanifest_<appid>.acf` & `metadata.json`)
Reads active installation records from Steam library manifests and DepotDownloader metadata:
- If a depot entry contains `dlcappid`, maps it to that DLC AppID.
- If not, maps it to the base game AppID (`appid_str`).

### Tier 3: Cached Lua Script AST Parsing (`cached_luas/<appid>.lua`)
Lua unlock scripts (generated by Steam tools/AT0-M) provide authoritative structural grouping:
- Inspects comments and section headers line by line:
  - `(AppID: (\d+))` (case-insensitive): Sets `current_target_appid = <id>` for subsequent lines.
  - `-- MAIN` or `MAIN GAME`: Sets `current_target_appid = appid_str`.
  - `-- SHARED` or `SHARED REDIST`: Sets `current_target_appid = None` (avoids claiming shared runtimes like DirectX/VC++).
- Whenever `addappid(<depotid>, ...)` is encountered under an active `current_target_appid`, maps `<depotid>` to that target AppID.
- Extracts decryption keys simultaneously from `addappid(<depotid>, <manifest>, "<64_char_key>")`.

### Tier 4: SLSsteam Plugin Library (`plugins/plugins.json`)
For games integrated via SLSsteam custom plugins:
- Reads the plugin library record for the base game and each app in `state_apps`.
- Maps any depots explicitly registered under `game_record.get("depots", [])` to that AppID.

### Tier 5: Secondary App Lua Scripts (`cached_luas/<aid>.lua`)
If `state_apps` contains companion apps or secondary AppIDs (e.g., dedicated server, beta client), checks if `cached_luas/<aid>.lua` exists on disk and parses its `addappid` statements directly into `mapping[aid]`.

### Tier 6: Identity Match (`DepotID == AppID`)
Steam frequently assigns the identical integer ID to a DLC package and its primary content depot:
- For every item in `state_apps`:
  ```python
  mapping.setdefault(aid, set()).add(aid)
  ```

### Tier 7: Unclaimed Catch-All Resolution
**Critical for Base Games (e.g., Dome Keeper `1637320`):**
Base game depots (such as OS-specific binaries: Windows `1637321`, Linux `1637322`, macOS `1637323`) do not have a `dlcappid` tag and are often not wrapped in DLC comments.
- Computes `claimed_by_others`: the union of all depots claimed by DLCs and secondary apps (`aid != appid_str`).
- Any depot in `state_depots` that is **not** in `claimed_by_others` is assigned to the **base game AppID** (`appid_str`).
- Result: Toggling the base game AppID off cleanly disables all base game depots and their decryption keys; toggling it on reenables them.

---

## 3. Decryption Key Mapping & Cascade Logic

### How Decryption Keys are Linked
Decryption keys are collected across four sources:
1. Local SQLite database (`DepotKeyManager`).
2. Cached Lua script `addappid` arguments.
3. SLSsteam Plugin Library (`plugins.json`).
4. Tagged inline comments in `~/.config/SLSsteam/config.yaml`.

Keys are categorized:
- **Root App Key**: Key where `key_id == appid_str` (rare, used by select Steam titles with root encrypted executables).
- **Depot Keys**: Keys where `key_id == depot_id` (standard 64-char AES keys).
- **DLC Keys**: Keys where `key_id == dlc_id` (when depot ID equals DLC AppID).

### One-Way Dynamic Cascade Rules
To prevent inconsistent states in `config.yaml`, ASSella enforces a strict **one-way cascade**:
```
User toggles AppID  ──►  Auto-toggles associated Depots & Decryption Keys
User toggles Depot  ──X  DOES NOT touch AppIDs (allows manual depot overrides)
User toggles Key    ──X  DOES NOT touch AppIDs or Depots
```

When an AppID (`aid`) is toggled:
1. **If Enabled (`checked = True`)**:
   - If `aid` has a valid key in `DecryptionKeys`, enable it (`desired_keys[aid] = True`).
   - For every depot `did` in `mapping[aid]`:
     - Enable the depot in `AdditionalDepots` (`desired_depots[did] = True`).
     - If `did` has a valid key in `DecryptionKeys`, enable the key (`desired_keys[did] = True`).
2. **If Disabled (`checked = False`)**:
   - If `aid` has a key in `DecryptionKeys`, disable it (`desired_keys[aid] = False`).
   - For every depot `did` in `mapping[aid]`:
     - Disable the depot in `AdditionalDepots` (`desired_depots[did] = False`).
     - Disable the key in `DecryptionKeys` (`desired_keys[did] = False`).

---

## 4. Best Practices for AT0-M "DLC-Only Mode"

When designing and improving the **DLC-Only Mode** in AT0-M, apply the following design rules:

### Rule 1: Parent AppID & Base Depot Safeguards
In DLC-Only mode, users are not artificially prevented from adding or retaining the parent/base game AppID in `AdditionalApps` if they wish to do so (e.g., for unlocking base features, family sharing licenses, or manual user preferences). However, the following safeguards strictly apply:
- **Strict Safeguard in `AdditionalDepots`**: The parent AppID number is **NEVER** placed into `AdditionalDepots`.
- **Safeguard for Base Depots**: Base game content/executable depots (e.g., Windows/Linux/Mac OS binaries) are not injected into `AdditionalDepots` in DLC-only mode.
- **Safeguard in `DecryptionKeys`**: The base game AppKey is only retained if the user explicitly included the parent AppID; otherwise it is cleanly omitted.

### Rule 2: Separate "Content DLCs" vs "License-Only DLCs"
- **Content DLCs** (e.g., expansion packs with custom levels/textures):
  - Must add DLC AppID to `AdditionalApps`.
  - Must add associated DLC depots to `AdditionalDepots`.
  - Must add DLC depot keys to `DecryptionKeys`.
- **License-Only DLCs** (e.g., Deluxe edition skins, soundtrack licenses, season passes):
  - Must add DLC AppID to `AdditionalApps` (or SLSsteam `UnlockAll: yes`).
  - **Must NOT** inject orphan depot entries into `AdditionalDepots` or fake keys into `DecryptionKeys`.

### Rule 3: Use the Multi-Tier Heuristic to Avoid Stale Depots
When AT0-M prepares a game for DLC-only downloading:
1. Inspect the game's manifest or database to extract all DLC AppIDs owned or targeted.
2. For each DLC AppID, find its depots using:
   - `dlcappid` in appinfo/manifest.
   - Lua `(AppID: <dlc_id>)` block parsing.
   - Identity fallback (`depot_id == dlc_id`).
3. Only stage depots that specifically match those DLC AppIDs.
4. Exclude all depots mapped to `appid_str` (the base game).

### Rule 4: Clean Atomic Cascade on Removal
When removing or deselecting a DLC:
- Find all depots mapped to that DLC AppID.
- Verify whether any other active DLC also references that depot ID (shared DLC depots).
- If no other active DLC references the depot, remove it from `AdditionalDepots` and remove its key from `DecryptionKeys`.
- Write `~/.config/SLSsteam/config.yaml` in-place preserving system inodes.

### Reference Implementation: `resolve_dlc_mapping_for_selection()`
The standard pipeline in `src/utils/dlc_helpers.py` provides:
```python
sel_dlc_apps, sel_dlc_depots = resolve_dlc_mapping_for_selection(
    base_appid, selected_items=user_selection, game_data=game_data
)
```
- `sel_dlc_apps`: `{ dlc_appid: comment_label }` containing strictly resolved DLC AppIDs (never base appid, never raw depot IDs).
- `sel_dlc_depots`: `{ dlc_depot_id, ... }` containing strictly the content depots belonging to those selected DLCs.
- `DecryptionKeys`: Scoped strictly to `sel_dlc_depots` and keyed DLC AppIDs, purging root `base_appid` key and base game depot keys.

---

## 5. Concrete Examples

### Example A: Dome Keeper (`1637320`)
- **Base AppID**: `1637320`
- **Base Depots** (no `dlcappid`, unclaimed):
  - `1637321` (Dome Keeper Windows)
  - `1637322` (Dome Keeper Linux)
  - `1637323` (Dome Keeper Mac)
- **DLCs**:
  - `2156820` (Pioneer Pack) -> Depot `2156820`
  - `2168340` (Soundtrack) -> Depot `2168340`
- **Mapping Result**:
  - `1637320` -> `{1637321, 1637322, 1637323}`
  - `2156820` -> `{2156820}`
  - `2168340` -> `{2168340}`
- **Behavior**:
  - In Full Mode: Toggling `1637320` toggles all base depots `1637321`, `1637322`, `1637323`.
  - In DLC-Only Mode: Only `2156820` and `2168340` are managed; `1637320` and its base depots are left untouched.

### Example B: Escape Simulator (`1435700`)
- **Base AppID**: `1435700`
- **Base Depots**: `1435701`, `1435702`, `1435703` (Windows/Mac/Linux binaries)
- **DLCs**:
  - `1942280` (Steampunk DLC) -> Depots mapped via `dlcappid == 1942280`
  - `2868390` (Mayan DLC) -> Depots mapped via `dlcappid == 2868390`
- **Behavior**:
  - Toggling `1942280` off disables only Steampunk depots and Steampunk decryption keys.
  - Base game depots remain untouched.

---

## 6. Depots Tab Eligibility for ASSella-Mode Games

Historically, the **Depots** tab in `GameDetailsDialogV2` was restricted exclusively to AT0-M / Vapor / Plugin games (`is_atom = True`). For standard ASSella-mode games (downloaded via DepotDownloader or managed locally), the tab was hidden.

### Qualification Criteria (`has_game_config_entries`)
ASSella-mode games now qualify to display the **Depots** tab if they have active, game-specific entries in `~/.config/SLSsteam/config.yaml`:
1. **AdditionalApps**: The game's base AppID or any of its DLC AppIDs is listed.
2. **AdditionalDepots**: Any non-shared depot belonging to the game is present (excluding universal shared redists).
3. **DecryptionKeys**: The game's root AppKey or any non-shared depot decryption key is present.
4. **dlc_data**: A batch DLC block for the AppID exists in `config.yaml`.
5. **Tagged Comments**: Any entry in `AdditionalApps`, `AdditionalDepots`, or `DecryptionKeys` has comments explicitly referencing the game's AppID or title.
6. **Plugin Registration**: The game is registered in `plugin_library.json`.

If a game meets any of these criteria (excluding common shared redists like `228980`), the Depots tab is automatically displayed in the game details dialog.

---

## 7. Shared Depot Architecture & Protection

### Definition of Shared Depots
Steam games frequently share common runtime environments, DirectX installers, and Visual C++ redistributable packages:
- **`ALL_SHARED_REDISTS`**: Unified set combining `SHARED_REDISTS` (e.g., `228980` Steamworks Shared, `228981`...`229032`) and `DEPOT_BLACKLIST`.

### Protection Principles
1. **Exclusion from Game-Specific Tables**:
   - Shared redists are strictly excluded from the game's Depots table in the UI. This prevents clutter and prevents users from accidentally disabling shared runtimes while configuring a specific game.
2. **Guaranteed Auto-Enable on Sync (`game_shared_depots`)**:
   - When a game is synced (locked or unlocked), ASSella inspects all depots referenced by the game (via Lua metadata, game data, or plugin definitions).
   - If the game requires any shared redistributables, ASSella automatically ensures they are added to `AdditionalDepots` with comment `# Steamworks Shared`.
3. **Multi-Layer Preservation Guard (`is_depot_shared_with_other_games`)**:
   - Whenever any depot or key removal is requested (via toggle or DLC sanitization), `is_depot_shared_with_other_games` enforces strict protection:
     * **Known Common Redists**: Never removed.
     * **Other Registered Games**: If another game in `plugin_library.json` references the depot or key, removal is aborted.
     * **Installed Steam Manifests**: If another game's `appmanifest_*.acf` lists the depot under `InstalledDepots`, removal is aborted.
     * **Cross-Game Config Comments**: If the comment in `config.yaml` tags another AppID, removal is aborted.
4. **UI Indicator**:
   - Depots shared with other registered games are clearly marked with `[Shared]` in the Depots table, with a tooltip explaining that removal is protected.

