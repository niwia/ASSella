# AGENTS.md — Guidelines for ASSella Agentic Development & Releases

This file defines essential guidelines, build rules, versioning conventions, and release procedures for automated coding agents working in the ASSella repository.

---

## 1. Core Principles

- **No Hardcoded User Home Paths**: Never hardcode `/home/deck/` or `/home/<username>/`. Always use `os.path.expanduser("~")` or `Path.home()`.
- **System Inode Preservation**: When modifying SLSsteam configuration files (`~/.config/SLSsteam/config.yaml`), write updates **in-place** (or via `BatchConfigEditor` / `_atomic_write`) to preserve the file's system inode and prevent breaking SLSsteam's `inotify` file watcher.
- **ACF-Independent Architecture & Smart Deletion**: ASSella writes and relies on local `metadata.json` files inside `{game_dir}/.DepotDownloader/`. During uninstall, poll Steam client up to 10s to delete the `.acf` manifest; if Steam fails to delete it, automatically discover and clean up candidate manifests across all Steam libraries.
- **Spliced Ticket Plugin**: Managed under **Settings → ASSella tab** (below the SteamAPI provider selector). When toggling, enforce the legality warning dialog strictly without emojis: `"Bypassing DRM is illegal in many countries, make sure you are aware of this!"`. Automatically enables `Plugins: yes` and `SmartTickets: 0x1` in SLSsteam config.yaml.
- **Pre-release Verification Suite**: Run `python3 scripts/prerelease_check.py` when explicitly validating builds. Do not execute it automatically after every minor task.

---

## 2. Release & Versioning Guidelines

### A. Release Channels & Tag Naming Conventions
ASSella enforces strict update channel isolation:
- **Canary Channel**: Tags must contain `canary` or `testing` (e.g. `v3.0.0testing200926001` or `v2.8.0-canary.1`).
  - *Note on `3.` Prefix*: Matching `3.` alone was a temporary development measure. While `3.` is temporarily tolerated during 3.0 development, official canary tags and releases should explicitly include `testing` or `canary` identifiers.
- **Beta Channel**: Tags must contain `beta`, `dev`, or `rc` (e.g. `v2.7.0beta`, `v2.7.5-beta.1`, `2.7.0dev`, `v2.7.0rc1`).
  - *Strict Rule*: Do not use `alpha`.
- **Stable Channel**: Official releases tagged with `stable` or clean semantic version tags without pre-release suffixes (e.g. `v2.7.0`, `v2.7.0-stable`).

### B. Automated GitHub Actions CI/CD Pipeline
Releases are automated via [.github/workflows/build-appimage.yml](file:///.github/workflows/build-appimage.yml):
1. **Trigger**: Push a version tag (e.g. `git tag v2.7.0beta && git push origin v2.7.0beta`) or trigger manually with `workflow_dispatch` in the GitHub Actions UI.
2. **Automated AppImage & ZSync Build**:
   - Compiles AppImage in a clean `ubuntu-22.04` environment using `appimagetool`.
   - Embeds update string: `gh-releases-zsync|niwia|ASSella|latest|ASSella.AppImage.zsync`.
   - Automatically generates `ASSella.AppImage.zsync` enabling delta updates where users download only changed blocks (~5-15MB instead of 300MB).
   - Generates SHA-256 checksums (`ASSella.AppImage.sha256`).
3. **Automated GitHub Release Publishing**:
   - Automatically marks Beta and Canary releases as pre-releases on GitHub.
   - Attaches `ASSella.AppImage` (capital `.AppImage` required for v2.6.5 compatibility), `.zsync`, and checksum files.
4. **Automated Arch Linux Repository Publishing**:
   - Automatically packages `assella-<version>-1-x86_64.pkg.tar.zst` via `makepkg`.
   - Generates and updates `assella.db.tar.gz` via `repo-add`.
   - Deploys the repository directly to the `gh-pages` branch (`https://niwia.github.io/ASSella/arch/$arch`) for direct `sudo pacman -S assella` installation.

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
