# AGENTS.md — Guidelines for ASSella Agentic Development & Releases

This file defines essential guidelines, build rules, versioning conventions, and release procedures for automated coding agents working in the ASSella repository.

---

## 1. Core Principles

- **No Hardcoded User Home Paths**: Never hardcode `/home/deck/` or `/home/<username>/`. Always use `os.path.expanduser("~")` or `Path.home()`.
- **System Inode Preservation**: When modifying SLSsteam configuration files (`~/.config/SLSsteam/config.yaml`), write updates **in-place** (or via `BatchConfigEditor` / `_atomic_write`) to preserve the file's system inode and prevent breaking SLSsteam's `inotify` file watcher.
- **ACF-Independent Architecture & Strict No-ACF Invariant**: In ACF-Independent mode, ASSella delegates manifest creation entirely to Steam natively via SLSsteam (`install|<appid>|<library_index>`). ASSella must **NEVER** write or generate `.acf` manifest files under any circumstance (including emergency timeouts, pipe failures, or offline Steam sessions). When Steam starts, it natively discovers installed files and creates the manifest. The **sole exception** where ASSella writes an `.acf` file is for explicitly pinned builds (`pin_build=True`), preserving the pinned `buildid` against Steam clobbering it. During uninstall, poll Steam client up to 10s to delete the `.acf` manifest; if Steam fails to delete it, automatically discover and clean up candidate manifests across all Steam libraries.
- **Spliced Ticket Plugin**: Managed under **Settings → ASSella tab** (below the SteamAPI provider selector). When toggling, enforce the legality warning dialog strictly without emojis: `"Bypassing DRM is illegal in many countries, make sure you are aware of this!"`. Automatically enables `Plugins: yes` and `SmartTickets: 0x1` in SLSsteam config.yaml.
- **SLSsteam Config Section Rules & Invariants**:
  - **DecryptionKeys**: If an AppID has a decryption key (root AppKey) or depot key, it **MUST** be added to `DecryptionKeys` 100% of the time when adding the game. Never refuse, block, or sanitize valid 64-hex keys in `DecryptionKeys`. Users have full freedom to toggle keys on/off in the Depots tab without self-sanitation resetting them.
  - **AdditionalDepots**: Parent/Base AppIDs must **NEVER** be added to `AdditionalDepots` under any circumstances (only content depot IDs and build download IDs belong here). Any entry present in `AdditionalApps` or `AppIds` is strictly guarded from entering `AdditionalDepots`.
  - **AdditionalApps**: Normal games must have their base AppID added. DLC-only games must have only their DLC AppIDs added (never base AppID).
  - **Plugin Config Auto-Seeding & Gates**: Deploying plugins or enabling AT0-M mode must automatically ensure `Plugins: yes`, `AdditionalDepots:`, and `DecryptionKeys:` exist in `config.yaml`. AT0-M moves/installs must strictly gate on these sections and an active interceptor.
  - **AT0-M ACF Cleanup & Discovery**: During AT0-M migration or "Fix Installation", stale `.acf` files must be deleted so Steam can discover files cleanly without carrying over corrupted state flags. The SLSsteam install API (`install_via_sls`) must ONLY be invoked for "Fix Installation"; during game transfers, only delete the old ACF without invoking the API.
  - **Migration Depot Scope**: Standard game AT0-M migrations inject all depots and keys from the Hubcap bundle without smart filtering, while DLC-only games strictly preserve the mapped DLC depot scope.
  - **Pre-flight Manifest Bundle**: When transferring to AT0-M, verify that the Hubcap ZIP bundle exists locally; auto-fetch it if missing, and abort cleanly (preserving ASSella markers) if Hubcap fetch fails.
- **Build ID Stamping Invariant**: During verify, update, or package installation, the newly parsed package/manifest build ID (`new_bid` / `parsed_data["buildid"]`) must always take precedence over stale installed metadata (`game_data["buildid"]`) across `actions.py`, `task_manager.py`, and `post_install.py`, unless an explicit rollback build is pinned (`pin_build` / `_is_rollback`).
- **Library Action Dialog Independence**: Library actions (e.g. `_on_manifest_download_complete`) must never require an active Game Details dialog instance (`self._details_dialog`), ensuring direct actions from the Steam Library tab function identically.
- **DLC-Only Depot Selection Guard**: Any DLC-only operation (`is_dlc_only=True`) must always prompt the user with `DepotSelectionDialog` (`should_prompt = True`, `auto_skip = False`) and must not silently reuse cached base-game selections.
- **External Metadata & Fix Caching**: External API lookups (e.g. `lua.tools` community fixes) must use persistent disk and memory caching (7-day default TTL) with 404/empty-result caching to prevent UI rate-limiting, backed by a manual refresh button.
- **Pre-release Verification Suite (`scripts/prerelease_check.py`)**: Do NOT run this test suite automatically. Never run it during development, after tasks, or on the `canary` branch unless the user explicitly and deliberately requests it. Automated CI also skips this suite on the `canary` branch.
- **AT0-M DLC-Only & Complication Invariants**:
  - **DLC-Only AT0-M Mapping Invariant**: In DLC-only mode, only the user's selected DLC depots and their mapped DLC AppIDs (resolved via `build_app_to_depots_map`) are injected into `AdditionalApps`, `AdditionalDepots`, and `DecryptionKeys`. The parent/base AppID must **NEVER** enter `AdditionalApps` for a DLC-only game, neither on initial AT0-M migration nor when converting back to ACCELA mode. If a selected DLC depot lacks a decryption key, trigger an emergency manifest fetch before completing mapping.
  - **AT0-M Pre-Handoff Sanitization**:
    - **Goldberg**: If Goldberg emulator is detected (`_is_goldberg_applied`), restore `.dll.valve` / `.so.valve` backups and delete `steam_settings/` / `steam_appid.txt` before Steam handoff (ignoring non-fatal errors).
    - **EOS Proxy**: Revert EOS proxy DLL to genuine binaries (`EOSDetector.remove_proxy`) prior to handoff, as Steam file integrity checks overwrite modified DLLs.
    - **Manifest Pinning**: For games with pinned builds, write the depot-to-manifest mapping to `config.yaml` (`ManifestIds`) via `set_manifest_ids` to lock the build in SLSsteam.
    - **No Delayed Install API**: Never trigger delayed `slssteam_api_send("install|...")` after migration. Prompt the user with a dialog to restart Steam so Steam client safely adopts files without unexpected redownloads.
    - **Complications Preflight Review**: If a game being migrated has any complications (pinned build, DLC-only, EOS proxy, Goldberg), display a preflight review window detailing all config additions and reversions before proceeding. Clean games proceed directly.

---

## 2. Release & Versioning Guidelines

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
  ARCH=x86_64 /home/aiwin/.local/share/ACCELA/appimagetool -n AppDir ASSella.AppImage
  ```

---

## 3. Installer Script (`install.sh`) Rules

- Installer scripts must query GitHub API (`https://api.github.com/repos/niwia/ASSella/releases`) with channel filtering (`--beta`, `--canary`, `--stable`).
- Auto-detect channel from `$LOCAL_VER` (e.g. `2.7.0dev` → beta channel) when run interactively or without arguments.
- Always verify assets end with `.AppImage` and exclude `.zsync` files from binary download targets.
