# ASSella on ARM64 & Armada OS

ASSella provides native 64-bit ARM (`aarch64`) support designed specifically for modern ARM handheld devices running [Armada OS](https://github.com/armada-os/armada) (a Fedora `bootc` immutable distribution running Gamescope and KDE Plasma).

---

## 1. Overview & Architecture

Mainstream x86_64 ASSella releases target standard Steam Deck (SteamOS) and generic x86 Linux distributions. The ARM64 port adapts ASSella to the architectural quirks of ARM64 handhelds while sharing 100% of the core functionality, Material You interface, and game management engines.

| Component | x86_64 Mainstream | ARM64 / Armada OS |
| :--- | :--- | :--- |
| **Target Architecture** | `x86_64` (AMD64) | `aarch64` (ARM64) |
| **Steam Client Root** | `~/.local/share/Steam` | `~/.local/share/Steam/steamrtarm64` |
| **Steam Executable** | `~/.local/share/Steam/steam` | `~/.local/share/Steam/steamrtarm64/steam` |
| **Display Session** | X11 / Xwayland / Gamescope | Pure Wayland Gamescope (`wayland;xcb`) |
| **AppImage FUSE Runtime** | Type 2 (dynamic `libfuse.so.2`) | Static FUSE3 runtime (for Fedora bootc) |
| **.NET 9 Dependency** | Host `~/.dotnet/dotnet` | Bundled directly in `AppDir/bin/.dotnet` |
| **CI Runner** | `ubuntu-22.04` (x86) | `ubuntu-24.04-arm` (Native GitHub aarch64) |
| **Primary Binary** | `ASSella.AppImage` | `ASSella-arm64.AppImage` |

---

## 2. Platform Quirks on Armada OS

### A. Fedora bootc Immutability & FUSE3
Armada OS is an image-based OS where `/usr` is read-only and traditional legacy libraries like `libfuse.so.2` are absent. 
- Traditional AppImages fail on double-click with `dlopen(): error loading libfuse.so.2`.
- ASSella's ARM64 AppImage is built using a static FUSE3 runtime, allowing direct execution without requiring host package layering or `fuse2` packages.

### B. Gamescope (Game Mode) Wayland Session
Armada OS boots directly into Gamescope Wayland in Game Mode and KDE Plasma in Desktop Mode.
- When launched as a Non-Steam Game inside Gamescope, relying solely on `libqxcb.so` can fail or cause black screens if Xwayland is uninitialized.
- The ARM64 AppImage bundles both `libqwayland-egl.so` and `libqxcb.so` in `usr/plugins/platforms/`, and exports:
  ```bash
  export QT_QPA_PLATFORM="wayland;xcb"
  ```

### C. Handheld Screen Geometry Clamping
ARM64 handheld screens typically operate at 720p or 800p resolutions with fractional scaling enabled (125%–150%). 
- In [src/ui/main_window.py](file:///home/aiwin/ASSella_beta_git/src/ui/main_window.py), `MainWindow.show()` dynamically inspects:
  ```python
  avail = QApplication.primaryScreen().availableGeometry()
  ```
  and clamps the application window to fit within available screen bounds, preventing control buttons or bottom tabs from clipping offscreen.

### D. Bundled .NET 9 Runtime
Features requiring .NET (such as specific decryption or manifest tools) would fail if the user had to install .NET 9 on an immutable OS.
- The build script (`scripts/build_arm64_appimage.sh`) bundles Microsoft's official `dotnet-runtime-9.0.0-linux-arm64` directly into `AppDir/bin/.dotnet`.
- [src/utils/helpers.py](file:///home/aiwin/ASSella_beta_git/src/utils/helpers.py) checks for this bundled runtime before falling back to `~/.dotnet/dotnet`.

### E. Native Steam Path Resolution
Steam on ARM64 Linux places its native runtime at `~/.local/share/Steam/steamrtarm64`.
- [src/core/steam_helpers.py](file:///home/aiwin/ASSella_beta_git/src/core/steam_helpers.py) prioritizes `steamrtarm64` whenever `is_arm64()` returns true, and executes `steamrtarm64/steam` directly.

### F. SLSsteam on ARM64
SLSsteam provides an experimental `aarm64` branch that builds `SLSsteam.so` injected via `LD_AUDIT`.
- Instead of hardcoding ARM64 as incompatible, [src/utils/slssteam_integration.py](file:///home/aiwin/ASSella_beta_git/src/utils/slssteam_integration.py) uses `SteamEnv.sls_installed()` to detect `~/.local/share/SLSsteam/SLSsteam.so`.
- When present, SLS mode is available; when absent, ASSella cleanly falls back to Standalone Mode.

---

## 3. Dedicated Scripts & Files

| Path | Purpose |
| :--- | :--- |
| `.github/workflows/build-arm64.yml` | Native `ubuntu-24.04-arm` CI workflow that compiles and verifies `ASSella-arm64.AppImage`. |
| `scripts/build_arm64_appimage.sh` | Shell script assembling the ARM64 AppDir, Python 3.13 aarch64 runtime, .NET 9 ARM64 runtime, and AppImage. |
| `scripts/install-armada.sh` | Userland installer script tailored for Armada OS. |
| `scripts/test_arm64_gating.py` | Unit test suite verifying architecture gating and path candidate logic. |
| `.agents/rules/arm64_rules.md` | Agent rules and guidelines for managing the `arm64` branch. |

---

## 4. Guidelines for Merging from `beta`

When merging features or bug fixes from `beta` to `arm64`:

1. **Do not use automated sync**: Pushes to `beta` must never blindly auto-merge into `arm64`. Merging must be performed manually and intentionally.
2. **Preserve Dynamic Gating**: Never remove `if is_arm64():` branches or bundled `.dotnet` lookups in `helpers.py`, `steam_helpers.py`, or `modes.py`.
3. **Keep `game_cache.py` Lint Annotation**: Ensure `import threading  # noqa: TC003` remains intact; moving it into `if TYPE_CHECKING:` causes `scripts/smoke_import_check.py` to fail at runtime.
4. **Run Verification**:
   ```bash
   uvx ruff check src/
   python3 -u scripts/smoke_import_check.py
   python3 scripts/test_arm64_gating.py
   python3 scripts/check_silent_exceptions.py
   ```
