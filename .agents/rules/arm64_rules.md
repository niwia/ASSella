# ARM64 & Armada OS Development Rules

This document outlines the architecture, invariants, and merge rules for the `arm64` branch of ASSella. Any agent or developer working on or merging into the `arm64` branch must follow these guidelines.

---

## 1. Branch Strategy & Merge Invariants

- **Isolated Branch**: The `arm64` branch is maintained as an independent, deliberate branch. Automated background syncing (`sync-arm64.yml`) has been removed from `canary`, `beta`, and `arm64` to prevent blind merges from clobbering ARM64 adaptations.
- **Merging from `beta`**:
  - When merging new features or fixes from `beta` into `arm64` (`git merge origin/beta`), **always review shared files**:
    - `src/core/steam_helpers.py` (ensure `steamrtarm64` candidate checks and direct binary execution remain intact)
    - `src/utils/helpers.py` (ensure bundled `.dotnet` priority in `get_dotnet_env()` and `get_dotnet_path()` is preserved)
    - `src/utils/yaml/modes.py` & `src/utils/slssteam_integration.py` (ensure dynamic SLS detection via `SteamEnv.sls_installed()` is preserved)
    - `src/ui/dialogs/settings_sls.py` (ensure SLS is not hard-disabled on ARM64)
    - `src/ui/main_window.py` (ensure handheld screen boundary clamping via `availableGeometry()` remains)
    - `requirements.in` (ensure `requests>=2.32.0` and `pycryptodome>=3.20.0` remain pinned)
    - `src/utils/game_cache.py` (ensure `import threading # noqa: TC003` is preserved so `smoke_import_check.py` does not fail on runtime annotation resolution)
- **Dedicated ARM64 Files**:
  Never overwrite or delete these ARM64-specific files when merging:
  - `.github/workflows/build-arm64.yml` (Native aarch64 GitHub Actions CI builder)
  - `scripts/build_arm64_appimage.sh` (Stand-alone AppImage builder for ARM64)
  - `scripts/install-armada.sh` (Installer optimized for Armada OS / Fedora bootc)
  - `scripts/test_arm64_gating.py` (Hermetic unit test suite for ARM64 gating logic)
  - `docs/arm64.md`

---

## 2. Armada OS & Environment Quirks

Armada OS is an image-based gaming Linux OS built on Fedora `bootc`, booting into Gamescope (Wayland) in Game Mode and KDE Plasma in Desktop Mode.

1. **FUSE2 vs. FUSE3**:
   - Fedora bootc removes `libfuse.so.2` by default.
   - The AppImage runtime must use a static FUSE3 runtime or compatible runner so users do not hit `dlopen(): error loading libfuse.so.2`.
2. **Wayland & Gamescope Compatibility**:
   - Game Mode runs pure Wayland via Gamescope.
   - The ARM64 AppImage bundles both `libqwayland-egl.so` and `libqxcb.so` under `usr/plugins/platforms/`.
   - `AppRun` must export `QT_QPA_PLATFORM="wayland;xcb"` so the application renders cleanly inside Gamescope without falling back to a missing Xwayland server.
3. **Bundled .NET 9 Runtime**:
   - Because Fedora bootc is immutable and users should not have to manually run `dotnet-install.sh` on handhelds, `scripts/build_arm64_appimage.sh` bundles the .NET 9 ARM64 runtime (`dotnet-runtime-9.0.0-linux-arm64`) directly at `AppDir/bin/.dotnet`.
   - `src/utils/helpers.py` prioritizes `AppDir/bin/.dotnet` before checking `~/.dotnet/dotnet`.
4. **Native Steam Path (`steamrtarm64`)**:
   - On ARM64 Linux, Steam runs native ARM64 components located at `~/.local/share/Steam/steamrtarm64/steam`.
   - `src/core/steam_helpers.py` resolves `steamrtarm64` first when `is_arm64()` is true.
   - Client version detection checks for `steam_client_arm64.manifest`.
5. **SLSsteam on ARM64**:
   - SLSsteam has an experimental `aarm64` branch that builds `~/.local/share/SLSsteam/SLSsteam.so` and hooks Steam via `LD_AUDIT`.
   - ASSella must dynamically detect `SteamEnv.sls_installed()` (`~/.local/share/SLSsteam/SLSsteam.so`) instead of hardcoding ARM64 as unsupported. When absent, it safely falls back to Standalone Mode.
6. **Handheld Fractional Scaling & Window Clamping**:
   - Handheld ARM64 screens are typically 720p or 800p with 125%–150% fractional scaling.
   - `src/ui/main_window.py` clamps main window geometry against `QApplication.primaryScreen().availableGeometry()` so dialogs and bottom action bars are never clipped offscreen.

---

## 3. Building & Testing ARM64

- **GitHub Actions**:
  - Workflow: `.github/workflows/build-arm64.yml`
  - Runner: `ubuntu-24.04-arm` (Native GitHub aarch64 runner; never use QEMU emulation due to compilation timeouts with gevent/cryptography).
  - Artifact: Uploads `ASSella-arm64-AppImage` containing `ASSella-arm64.AppImage`, `.sha256`, and `.zsync`. Does not publish public releases unless explicitly dispatched.
- **Local Unit Testing**:
  - Run `python3 scripts/test_arm64_gating.py` to verify architecture gating, `.dotnet` path resolution, and candidate priority without needing physical ARM64 hardware.
- **Lint & CI Invariants**:
  - Always run `uvx ruff check src/` and `python3 -u scripts/smoke_import_check.py` before pushing.
  - If `scripts/check_silent_exceptions.py` fails due to new silent handlers from upstream merges, update the baseline with `python3 scripts/check_silent_exceptions.py --write`.
