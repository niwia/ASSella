# AGENTS.md — Guidelines for ASSella Agentic Development & Releases

This file defines essential guidelines, build rules, versioning conventions, and release procedures for automated coding agents working in the ASSella repository.

---

## 1. Core Principles

- **No Hardcoded User Home Paths**: Never hardcode `/home/deck/` or `/home/<username>/`. Always use `os.path.expanduser("~")` or `Path.home()`.
- **System Inode Preservation**: When modifying SLSsteam configuration files (`~/.config/SLSsteam/config.yaml`), write updates **in-place** (or via `BatchConfigEditor` / `_atomic_write`) to preserve the file's system inode and prevent breaking SLSsteam's `inotify` file watcher.
- **ACF-Independent Architecture & Smart Deletion**: ASSella writes and relies on local `metadata.json` files inside `{game_dir}/.DepotDownloader/`. During uninstall, poll Steam client up to 10s to delete the `.acf` manifest; if Steam fails to delete it, automatically discover and clean up candidate manifests across all Steam libraries.
- **Spliced Ticket Plugin**: Managed under **Settings → ASSella tab** (below the SteamAPI provider selector). When toggling, enforce the legality warning dialog strictly without emojis: `"Bypassing DRM is illegal in many countries, make sure you are aware of this!"`. Automatically enables `Plugins: yes` and `SmartTickets: 0x1` in SLSsteam config.yaml.
- **SLSsteam Config Section Rules & Invariants**:
  - **DecryptionKeys**: If an AppID has a decryption key (root AppKey) or depot key, it **MUST** be added to `DecryptionKeys` 100% of the time when adding the game. Never refuse, block, or sanitize valid 64-hex keys in `DecryptionKeys`. Users have full freedom to toggle keys on/off in the Depots tab without self-sanitation resetting them.
  - **AdditionalDepots**: Parent/Base AppIDs must **NEVER** be added to `AdditionalDepots` under any circumstances (only content depot IDs and build download IDs belong here). Any entry present in `AdditionalApps` or `AppIds` is strictly guarded from entering `AdditionalDepots`.
  - **AdditionalApps**: Normal games must have their base AppID added. DLC-only games must have only their DLC AppIDs added (never base AppID).
- **Pre-release Verification Suite (`scripts/prerelease_check.py`)**: Do NOT run this test suite automatically. Never run it during development, after tasks, or on the `canary` branch unless the user explicitly and deliberately requests it. Automated CI also skips this suite on the `canary` branch.

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
   - Attaches `ASSella.AppImage` (capital `.AppImage` required for compatibility), `.zsync`, and checksum files.

### C. Local AppImage Builds (When Testing Locally)
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
