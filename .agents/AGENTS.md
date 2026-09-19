# AGENTS.md — Guidelines for ASSella Agentic Development & Releases

This file defines essential guidelines, build rules, versioning conventions, and release procedures for automated coding agents working in the ASSella repository.

---

## 1. Core Principles

- **No Hardcoded User Home Paths**: Never hardcode `/home/deck/` or `/home/<username>/`. Always use `os.path.expanduser("~")` or `Path.home()`.
- **System Inode Preservation**: When modifying SLSsteam configuration files (`~/.config/SLSsteam/config.yaml`), write updates **in-place** (`open(..., "r+")` followed by `f.truncate()`) to preserve the file's system inode and prevent breaking SLSsteam's `inotify` file watcher.
- **ACF-Independent Architecture**: ASSella writes and relies on local `metadata.json` files inside `{game_dir}/.DepotDownloader/`. Delegate `.acf` file creation/maintenance natively to Steam client via SLS config updates and `install|appid|0` named pipe signals.
- **Pre-release Verification Suite**: Do not run `scripts/prerelease_check.py` or full pre-release test suites automatically after tasks or in the future. Only run them when explicitly requested by the user.

---

## 2. Release & Versioning Guidelines

### A. Tag Naming & Branching Conventions
- Pushes and releases should target the **`beta` / pre-release branch** unless the user explicitly requests main/stable.
- With every release, automatically increment the semantic version to the next version tag following the last release (e.g. `v2.5.7` following `v2.5.6`).
- Ensure the local version (`src/res/version`) matches the new version tag pushed to GitHub.
- GitHub release tags must be prefixed with `v` (e.g., `v2.5.7`).

### B. AppImage Binaries
- The final production AppImage file is named exactly **`ASSella.AppImage`**.
- The local development AppImage is named `ASSella.AppImage.dev`.
- During active development and testing, ONLY build and modify `ASSella.AppImage.dev`. Never overwrite or change the stable `ASSella.AppImage`.
- Rebuild command for development:
  ```bash
  ARCH=x86_64 /home/aiwin/.local/share/ACCELA/appimagetool squashfs-root /home/aiwin/.local/share/ACCELA/ASSella.AppImage.dev.new && mv -f /home/aiwin/.local/share/ACCELA/ASSella.AppImage.dev.new /home/aiwin/.local/share/ACCELA/ASSella.AppImage.dev
  ```

### C. Source Code Release Packaging
- With every release, compile a companion source code archive (e.g. `ASSella-<version>-linux-source.tar.gz`) containing:
  ```text
  ASSella-<version>-linux-source/
  ├── install.sh
  ├── uninstall.sh
  └── bin/
      ├── src/           # Python codebase (excluding __pycache__)
      ├── run.sh         # Venv python launcher wrapper
      ├── requirements.txt
      └── icon.png
  ```

### D. Changelog Guidelines
- Changelogs must **never repeat** items from past releases.
- List only the new features, tweaks, and bug fixes added since the immediately preceding release.

---

## 3. Installer Script (`install.sh`) Rules

- Installer scripts MUST query GitHub API (`https://api.github.com/repos/niwia/ASSella/releases`) to dynamically resolve `browser_download_url` for `ASSella.AppImage`. This ensures both stable releases and pre-releases on the `beta` branch download correctly without throwing 404 HTTP errors.
- Always use `curl -fL` to ensure non-200 HTTP responses raise an error instead of saving HTML 404 pages as binary files.
