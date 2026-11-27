# AGENTS.md — Guidelines for ASSella Agentic Development & Releases

This file defines essential guidelines, build rules, versioning conventions, and release procedures for automated coding agents working in the ASSella repository.

---

## 1. Core Principles

- **No Hardcoded User Home Paths**: Never hardcode `/home/deck/` or `/home/<username>/`. Always use `os.path.expanduser("~")` or `Path.home()`.
- **System Inode Preservation**: When modifying SLSsteam configuration files (`~/.config/SLSsteam/config.yaml`), write updates **in-place** (or via `BatchConfigEditor` / `_atomic_write`) to preserve the file's system inode and prevent breaking SLSsteam's `inotify` file watcher.
- **ACF-Independent Architecture & Strict No-ACF Invariant**: In ACF-Independent mode, ASSella delegates manifest creation entirely to Steam natively via SLSsteam (`install|<appid>|<library_index>`). ASSella must **NEVER** write or generate `.acf` manifest files under any circumstance (including emergency timeouts, pipe failures, or offline Steam sessions). When Steam starts, it natively discovers installed files and creates the manifest. The exceptions where ASSella edits an `.acf` file are: explicitly pinned builds (`pin_build=True`), preserving the pinned `buildid` against Steam clobbering it; and AT0-M migration, which sets an existing ACF's `StateFlags` to `6` (see below). During uninstall, poll Steam client up to 10s to delete the `.acf` manifest; if Steam fails to delete it, automatically discover and clean up candidate manifests across all Steam libraries.
- **Spliced Ticket Plugin**: Managed under **Settings → ASSella tab** (below the SteamAPI provider selector). When toggling, enforce the legality warning dialog strictly without emojis: `"Bypassing DRM is illegal in many countries, make sure you are aware of this!"`. Automatically enables `Plugins: yes` in SLSsteam config.yaml (`SmartTickets` was removed in modern SLSsteam upstream and is no longer managed).
- **SLSsteam Config Section Rules & Invariants**:
  - **DecryptionKeys**: If an AppID has a decryption key (root AppKey) or depot key, it **MUST** be added to `DecryptionKeys` 100% of the time when adding the game. Never refuse, block, or sanitize valid 64-hex keys in `DecryptionKeys`. Users have full freedom to toggle keys on/off in the Depots tab without self-sanitation resetting them.
  - **AdditionalDepots**: Parent/Base AppIDs must **NEVER** be added to `AdditionalDepots` under any circumstances (only content depot IDs and build download IDs belong here). Any entry present in `AdditionalApps` or `AppIds` is strictly guarded from entering `AdditionalDepots`.
  - **AdditionalApps**: Normal games must have their base AppID added. DLC-only games must have only their DLC AppIDs added (never base AppID).
  - **Plugin Config Auto-Seeding & Gates**: Deploying plugins or enabling AT0-M mode must automatically ensure `Plugins: yes`, `AdditionalDepots:`, and `DecryptionKeys:` exist in `config.yaml`. AT0-M moves/installs must strictly gate on these sections and an active interceptor.
  - **AT0-M ACF Adoption**: During AT0-M migration (single or batch), an existing `.acf` is NOT deleted: its `StateFlags` is set to `6` (UpdateRequired | FullyInstalled) so Steam verifies and adopts the files already on disk after a restart, without re-downloading. "Fix Installation" on an AT0-M game asks the user to either delete the ACF or set `StateFlags=6`. The SLSsteam install API (`install_via_sls`) must ONLY be invoked for "Fix Installation", never during game transfers.
  - **Migration Depot Scope**: Standard game AT0-M migrations inject all keyed depots and keys from the Hubcap bundle without smart filtering, while DLC-only games strictly preserve the mapped DLC depot scope. A selected depot with no key is never silently dropped: refresh the Hubcap bundle once, keep only the still-unkeyed depots out, and surface a themed warning listing them. If no keyed content remains, the hand-off must fail with that reason.
  - **Pre-flight Manifest Bundle**: When transferring to AT0-M, verify that the Hubcap ZIP bundle exists locally; auto-fetch it if missing, and abort cleanly (preserving ASSella markers) if Hubcap fetch fails.
- **Build ID Stamping Invariant**: During verify, update, or package installation, the newly parsed package/manifest build ID (`new_bid` / `parsed_data["buildid"]`) must always take precedence over stale installed metadata (`game_data["buildid"]`) across `actions.py`, `task_manager.py`, and `post_install.py`, unless an explicit rollback build is pinned (`pin_build` / `_is_rollback`).
- **Library Action Dialog Independence**: Library actions (e.g. `_on_manifest_download_complete`) must never require an active Game Details dialog instance (`self._details_dialog`), ensuring direct actions from the Steam Library tab function identically.
- **DLC-Only Depot Selection Guard**: Any DLC-only operation (`is_dlc_only=True`) must always prompt the user with `DepotSelectionDialog` (`should_prompt = True`, `auto_skip = False`) and must not silently reuse cached base-game selections.
- **External Metadata & Fix Caching**: External API lookups (e.g. `lua.tools` community fixes) must use persistent disk and memory caching (7-day default TTL) with 404/empty-result caching to prevent UI rate-limiting, backed by a manual refresh button.
- **Pre-release Verification Suite (`scripts/prerelease_check.py`)**: Do NOT run this test suite automatically. Never run it during development, after tasks, or on the `canary` branch unless the user explicitly and deliberately requests it. Automated CI also skips this suite on the `canary` branch.
- **AT0-M DLC-Only & Complication Invariants**:
  - **DLC-Only AT0-M Mapping Invariant**: In DLC-only mode, only the user's selected DLC depots and their mapped DLC AppIDs (resolved via `build_app_to_depots_map`) are injected into `AdditionalApps`, `AdditionalDepots`, and `DecryptionKeys`. The parent/base AppID must **NEVER** enter `AdditionalApps` for a DLC-only game, neither on initial AT0-M migration nor when converting back to ACCELA mode. If a selected DLC depot lacks a decryption key, trigger an emergency manifest refresh before completing mapping; if it is still unkeyed, exclude it and tell the user which depots were skipped instead of silently producing a partial install.
  - **AT0-M Pre-Handoff Sanitization**:
    - **Goldberg**: If Goldberg emulator is detected (`_is_goldberg_applied`), restore `.dll.valve` / `.so.valve` backups and delete `steam_settings/` / `steam_appid.txt` before Steam handoff (ignoring non-fatal errors).
    - **EOS Proxy**: Revert EOS proxy DLL to genuine binaries (`EOSDetector.remove_proxy`) prior to handoff, as Steam file integrity checks overwrite modified DLLs.
    - **Manifest Pinning**: For games with pinned builds, write the depot-to-manifest mapping to `config.yaml` (`ManifestIds`) via `set_manifest_ids` to lock the build in SLSsteam.
    - **No Delayed Install API**: Never trigger delayed `slssteam_api_send("install|...")` after migration. Prompt the user with a dialog to restart Steam so Steam client safely adopts files without unexpected redownloads.
    - **Complications Preflight Review**: If a game being migrated has any complications (pinned build, DLC-only, EOS proxy, Goldberg), display a preflight review window detailing all config additions and reversions before proceeding. Clean games proceed directly.

- **Performance: keep startup, Settings and Game Details instant.** ASSella opens its main window in under a second; every new feature must keep it that way. Measure before and after (`python -X importtime`, or `cProfile` around `MainWindow()`), and follow these rules:
  - **Nothing slow on the GUI thread.** Network requests, `systemctl`/subprocess calls, large file scans and SQLite work run on a `QThread` / `TaskRunner` / background thread. Results come back through a Qt signal or `utils.main_thread.post_to_main_thread` (a `QTimer.singleShot` started from a plain Python thread never fires).
  - **Import heavy modules where they are used, not at module top level** of anything the main window, Settings or Game Details imports: `curl_cffi`, `bs4`, `core.morrenus_api`, scrapers and other large packages. When a heavy import feeds a worker, do the import inside the worker function. `core/steamdb_scraper.py` loads `curl_cffi` and `bs4` on first use for this reason (`_LazyModule`); test availability with `bool(cffi_requests)`, never `is not None`.
  - **Build pages lazily.** Settings pages are registered with `tab_widget.addLazyTab(label, build)` and built the first time they are opened; the build function passes its page to `tab_widget.addTab(page, label)`. A page draws first from local data (cache, bundled manifest, config files) and fills in network-dependent parts from a background job afterwards (see the AT0-M plugin buttons with `offline_manifest()` and Settings -> Companions).
  - **Game Details opens first, fetches second.** Artwork, logos, SteamGridDB, ProtonDB and similar lookups start after the dialog is shown (e.g. `QTimer.singleShot` from `showEvent`, guarded so it runs once) and must check that the dialog still exists before touching it (`sip.isdeleted`).
  - **Cache misses as well as hits.** A lookup that failed or found nothing (no logo, no depots for an AppID, 404) is remembered with a TTL so it is not retried on every start or every open.
  - **No work at import time** beyond constants: no file reads, network, or subprocesses when a module is imported.
- **Companions (Uran / Epsilon)**: Settings -> Companions (after at0-m) manages the Uran Decky plugin (`~/homebrew/plugins/Uran`, settings `~/homebrew/settings/Uran/settings.json`) and the Epsilon bridge (systemd user units `epsilon.socket` / `epsilon-updater.timer`, settings `~/.config/assella-decky/settings.json`, status on `127.0.0.1:1952/status`). Both keep their own settings and must keep working without ASSella; ASSella only edits their files (merge, keep unknown keys, atomic replace) through `utils/companions.py`.

---

## 2. Working in This Repository

### A. Orientation Map

- `src/main.py` starts the PyQt6 application. The project uses **PyQt6** (`PyQt6==6.11.x`), not PySide6, despite what some older docs may say.
- `src/managers/task_manager.py` orchestrates the current job (download, verify, hand-off), job status, history and cancel/error handling. `src/managers/finalize/post_install.py` owns the post-download finalize chain and ACF/registration side effects.
- `src/managers/game_manager_mixins/scanning.py` discovers Steam/ACCELA/AT0-M/plugin/standalone installs. `src/utils/game_modes.py` is the single source of truth for `is_accela_managed` / `is_atom_managed` classification — do not reimplement those checks locally.
- `src/utils/yaml_config_manager.py` is the public facade for SLSsteam `config.yaml`. `src/utils/yaml/core.py` owns parsing, section bounds, flow normalization, `BatchConfigEditor`, `config_write_lock` and `_atomic_write`; one module per section lives beside it. Sibling modules in `src/utils/yaml/` may import only `core`; everything outside the package imports the facade. `src/utils/_yaml_public_api.json` plus `scripts/smoke_import_check.py` enforce that contract.
- `src/core/native_steam/native_steam_handoff.py` performs AT0-M hand-off; `src/core/native_steam/native_steam_download_task.py` performs native Steam downloads and config patching; `steam_manifest_pinning.py` owns `ManifestIds`/build pins. `src/utils/steam_acf.py` and `src/utils/steam_manifest.py` own ACF parsing/repair/writing.
- `src/utils/plugin_games.py` is the AT0-M/plugin library (`plugin_library.json`); `src/utils/dlc_helpers.py` owns DLC-only scope/mapping; `src/utils/assella_pending.py` builds the pending-library view.
- Runtime state lives under `get_base_path()` / `get_data_file_path()` (normally `~/.local/share/ACCELA`): `plugin_library.json`, `db/depot_keys.db`, `cached_luas/<appid>.lua`, and downloaded Hubcap bundles. Always resolve these through the helpers, never with a literal home path.
- Tests are hermetic scripts under `scripts/test_*.py`. The authoritative CI list and static gates are in `.github/workflows/ci.yml`; docs live in `docs/`.

### B. Local Verification (run this before claiming a fix)

Set the test environment once:

```bash
export PYTHONPATH=src
export QT_QPA_PLATFORM=offscreen
export XDG_RUNTIME_DIR=/tmp
```

Then run the narrow suite first, followed by the import/static gates:

```bash
python3 -m compileall -q src scripts
python3 scripts/smoke_import_check.py
python3 scripts/test_config_write_safety.py
python3 scripts/test_yaml_and_ratings_fixes.py
python3 scripts/test_atom_config_sync.py
python3 scripts/check_native_dialogs.py
python3 scripts/check_themed_dialogs.py
python3 scripts/check_silent_exceptions.py
```

For a full behavioural pass, run the suites in `.github/workflows/ci.yml` (they are listed in the "Behavioural Test Suites" step). Use a unique temporary `HOME`/cache directory when testing config or database code; tests must never touch the developer's real `~/.config/SLSsteam` or `~/.local/share/ACCELA`.

CI suites must not call live Hubcap/GitHub endpoints, require a Steam install, or depend on optional `src/deps` binaries, cached Lua files or an existing SLSsteam config. Seed a temporary config/fixture and fake the network instead; keep live-data checks behind an explicit opt-in environment variable. A clean runner has no SLSsteam config, so any test that writes through the config layer must enable `utils.yaml.core._config_management_enabled` itself (or patch the live core function).

Before committing:

- Run `git -c core.whitespace=cr-at-eol diff --check`. `src/utils/steam_manifest.py` is intentionally CRLF; plain `git diff --check` reports its CR characters as trailing whitespace. Never rewrite that whole file to LF just to silence the check.
- Do not run `scripts/prerelease_check.py` automatically. It is a deliberate, user-requested pre-release suite (and CI skips it on `canary`).
- A `SIGSEGV` that appears only after a suite prints `Ran N tests ... OK` is the known offscreen Qt teardown flake. Re-run that suite once; a failure inside the tests is real and must be fixed.

### C. SLSsteam Config Writer Contract

- Every read-modify-write of `config.yaml` goes through `batch_config_edit(path)`. That gives the per-file lock, duplicate-key validation, one write, and inode preservation for SLSsteam's `inotify` watcher.
- Use `editor.ensure_plugins()` inside the edit block. Do not call a separate `ensure_plugins_enabled()` before the block: that creates a second write and a second watcher reload.
- Do not build YAML by string splicing or append a second section header. Use the section helpers (`add_app`, `add_depot`, `add_key`, `add_dlc_batch`, `remove_*`, etc.) or the in-memory helpers from `core`.
- `_normalize_flow_sections()` runs before section bounds are used. It expands empty `Section: {}` / `[]` to a bare header and non-empty flow collections to block style. Inserting indented keys under `{}` produces invalid YAML and is the root cause of several "add did nothing" bugs.
- Readers/writers must be quote-tolerant (`- "123"`, `621: Portal`) and preserve inline comments. `_add_map_item_in_memory` treats `#` as a comment only after whitespace; preserve that behavior.
- Public `add_*` helpers return `True` only when the file changed (`WriteOutcome.CHANGED`), except explicitly idempotent helpers such as `add_launch_option` and `add_dlc_data_batch`, which return `True` when the desired state already holds. Never use `False` for both "already present" and "refused/failed".
- `_atomic_write` validates the full YAML document before writing and writes in place with fsync. It is **not** a crash-atomic replace: a process kill mid-write can still leave a partial file. Do not claim otherwise or change its crash semantics without an explicit design decision.
- Large DLC sets switch to `DlcData` at `DLC_DATA_THRESHOLD` (64). Reuse that constant; do not re-hardcode `64` in a new writer.
- Automatic cleanup must not delete user data. Depot/key removal should check sharing (`check_shared`, `excluding_appid`); automatic `LaunchOptions` cleanup must pass ASSella markers and leave a user's own `-dx11` / `PROTON_*` options alone. An explicit "remove launch option" action may remove the whole entry.
- Add a regression test when touching this layer. `scripts/test_config_write_safety.py` covers the editor/lock/validation; `scripts/test_yaml_and_ratings_fixes.py` covers flow/empty sections, quoted IDs and single-write behavior.

### D. Mode, Status and AT0-M Invariants Learned From Real Bugs

- **`is_accela_install` means exactly that**: an ACCELA/ASSella-managed install (physical marker or explicit ACCELA source). It must never be set to `is_accela_install or is_vapor or is_plugin_game`; AT0-M/plugin installs must remain classified as AT0-M so `At0mUpdateChecker`, not the ACCELA update path, owns them.
- **A failed job is failed**: worker errors and failed finalize steps set `_job_failed` and must not increment the completed counter, write `"success": True` in history, or mark the game `up_to_date`. `_create_acf_file()` returns a bool; a failed ACF/registration step must propagate.
- **Unkeyed depots are a user-visible condition**: refresh the Hubcap Lua once (the cached Lua is refreshed after 24 hours or when a selected depot is missing a key), then exclude only depots that still have no key. Show a themed warning with the depot IDs and keep the warning in the hand-off message. Never silently install a partial game.
- **Re-scoping is a diff, not an append**: re-registering a plugin game or re-running DLC-only setup must compare the previous `plugin_library.json`/config record and remove DLC AppIDs, depots, keys, tokens and `ManifestIds` that are no longer selected, while preserving ids shared with other games. Pending-library cards must skip DLC AppIDs found in `DlcData`, plugin records and `[DLC]` comments.
- **`plugin_library.json` is not disposable**: a corrupt file must not be read as `{}` and overwritten. Writers use `strict=True`, refuse unreadable files, and save through a validated, locked, atomic replace. Reads are cached by path/mtime/size; invalidate the cache on save. `load_plugin_library()` returns a copy, so mutate that copy and call `save_plugin_library()` explicitly; mutating a returned record alone does not persist.
- **Keys and manifest request codes are validated at the boundary**: cached depot keys must be 64-character, non-placeholder AES keys; MRC responses must be HTTP 2xx and a realistic uint64, never a bare `403`/`404`/`500` body; a manifest GID of `0` is "unknown" and must never be pinned. Cache fallbacks must not bypass placeholder filtering.
- **ACF helpers are shared**: depot discovery from `app<appid>_depot<depotid>.manifest` must use the full filename regex, not `split("_")`. The non-ACF-Independent fallback ACF write must include `InstalledDepots` on every platform and go through the atomic ACF writer. In ACF-Independent mode the no-ACF invariant above still wins.

### E. Small Traps That Waste Agent Time

- Do not add native `QMessageBox`/`QFileDialog`; use `ThemedMessageBox` / `ThemedFileDialog` (enforced by `check_native_dialogs.py` and `check_themed_dialogs.py`).
- From a plain Python thread use `utils.main_thread.post_to_main_thread`; a `QTimer.singleShot` created on that thread will not fire on the GUI thread.
- Keep heavy imports (`curl_cffi`, `bs4`, scrapers) inside the worker or behind a lazy module. No file/network/subprocess work at import time.
- Never commit the large prebuilt binaries (`assella3.0.1.tar.gz`, `.AppImage`, `node_modules/`, `__pycache__/`) or generated logs.
- Do not "fix" a CRLF file by rewriting it to LF; that creates a whole-file diff and hides the real change.

---

## 3. Release & Versioning Guidelines

### A. Release Channels & Tag Naming Conventions
ASSella enforces a clean 4-branch workflow: `stable`, `beta`, `canary`, and `arm64`:
- **Stable Channel**: Official releases tagged with clean semantic version tags without pre-release suffixes (e.g. `v3.0.0`, `v3.0.1`). Clean versions with no suffix are strictly treated as official Stable releases (`prerelease: false`).
- **Beta Channel**: Active preview releases. Tags must contain `beta`, `dev`, or `rc` (e.g. `v3.1.0-beta.1`).
  - *Strict Rule*: Do not use `alpha`.
- **Canary Channel**: Bleeding-edge / experimental releases. Tags must contain `canary` or `testing` (e.g. `v3.1.0-canary.1`).
- **ARM64 Branch**: Auto-synced ARM64 port.

### B. Automated GitHub Actions CI/CD Pipeline
Releases are automated via [.github/workflows/build-appimage.yml](file:///.github/workflows/build-appimage.yml):
1. **Trigger**: Push a version tag (e.g. `git tag v3.0.0 && git push origin v3.0.0`) or trigger manually with `workflow_dispatch` in the GitHub Actions UI.
2. **Automated AppImage & ZSync Build**:
   - Compiles AppImage in a clean `ubuntu-22.04` environment using `appimagetool`.
   - Embeds update string: `gh-releases-zsync|niwia|ASSella|latest|ASSella.AppImage.zsync`.
   - Automatically generates `ASSella.AppImage.zsync` enabling delta updates where users download only changed blocks (~5-15MB instead of 300MB).
   - Generates SHA-256 checksums (`ASSella.AppImage.sha256`).
3. **Automated GitHub Release Publishing**:
   - Automatically marks Beta and Canary releases as pre-releases on GitHub. Stable releases (`v3.0.0`) are published as official full releases.
   - Attaches `ASSella.AppImage` (capital `.AppImage` required for compatibility), `.zsync`, companion source archives (`.tar.gz` and `.zip`), and checksum files (`.sha256`).

### C. Companion Source Code Release Packaging (Automated)
Every GitHub Actions release run automatically generates and publishes standalone source code archives (`ASSella-<tag>-linux-source.tar.gz` and `ASSella-<tag>-linux-source.zip`) alongside SHA-256 checksums (`.sha256`).
- Archive layout:
  ```text
  ASSella-<version>-linux-source/
  ├── install.sh
  ├── uninstall.sh
  └── bin/
      ├── src/           # Python codebase (excluding __pycache__ & heavy optional deps)
      ├── run.sh         # Venv/system python launcher wrapper
      ├── requirements.txt
      └── icon.png
  ```
- Packaging rules:
  - Prunes `__pycache__` directories and `*.pyc` bytecode files.
  - Strips heavy on-demand binaries (`Goldberg`, `Steamless`, `SLScheevo` under `src/deps/`) which are fetched dynamically from R2, keeping the source distribution lightweight (~38MB).
  - Computes and publishes `.sha256` checksum files alongside each archive.

### D. Local AppImage Builds (When Testing Locally)
- Production binary: **`ASSella.AppImage`** (`~/.local/share/ACCELA/ASSella.AppImage`).
- Local beta development binary: **`ASSella-2.7dev.AppImage`** (`~/.local/share/ACCELA/ASSella-2.7dev.AppImage`).
- Local canary development binary: **`assella3.0canary.appimage`** (`~/.local/share/ACCELA/assella3.0canary.appimage`).
- Build command using local AppDir:
  ```bash
  ARCH=x86_64 "$HOME/.local/share/ACCELA/appimagetool" -n AppDir ASSella.AppImage
  ```

---

## 4. Installer Script (`install.sh`) Rules

- Installer scripts must query GitHub API (`https://api.github.com/repos/niwia/ASSella/releases`) with channel filtering (`--beta`, `--canary`, `--stable`).
- Auto-detect channel from `$LOCAL_VER` (e.g. `2.7.0dev` → beta channel) when run interactively or without arguments.
- Always verify assets end with `.AppImage` and exclude `.zsync` files from binary download targets.
