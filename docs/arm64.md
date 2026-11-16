# ASSella for ARM64 & Armada OS

::: warning Early Testing Preview
Native ARM64 support is currently in **early testing and active development**. Features may be experimental, incomplete, or behave unpredictably. Expect occasional manual troubleshooting as the ecosystem and tools mature.
:::

::: danger Critical Prerequisite: SLSsteam ARM64 Release Preview Required
To use ASSella's game management and native Steam integration on ARM64, you **must** be running an early **SLSsteam ARM64 release preview** build (the `aarm64` branch / preview builds providing `SLSsteam.so` for aarch64). 

Standard x86 SLSsteam binaries or older releases will **not** inject into native ARM64 Steam. Without an ARM64-compatible SLSsteam release preview, ASSella will safely fall back to Standalone Mode.
:::

---

## Overview

ASSella provides native 64-bit ARM (`aarch64`) builds designed specifically for modern ARM handheld devices running **Armada OS** (a Fedora `bootc` immutable gaming distribution) and other 64-bit ARM Linux environments.

The ARM64 port delivers the same Material You interface, downloading workflows, and library management features available on x86, while adapting under the hood to the unique requirements of ARM handheld hardware and immutable system architectures.

---

## Key Handheld & OS Adaptations

### 1. Bundled .NET 9 Runtime
Armada OS utilizes an immutable root filesystem (`bootc`), meaning users cannot easily install packages into system directories.
- ASSella bundles the native ARM64 **.NET 9** runtime internally.
- Features requiring .NET work immediately without requiring host-level modifications, root privileges, or running manual install scripts into system paths.

### 2. Static FUSE3 Runtime Support
Modern Linux distributions and immutable operating systems no longer ship legacy `libfuse.so.2` by default.
- Traditional AppImages fail on modern images with `dlopen(): error loading libfuse.so.2`.
- The ARM64 AppImage is built with static FUSE3 compatibility so it runs directly on double-click without needing extra compatibility packages.

### 3. Gamescope & Wayland Native Windowing
Armada OS boots directly into a Gamescope Wayland session in Game Mode and KDE Plasma in Desktop Mode.
- ASSella includes native Wayland and XCB display platform plugins.
- Operates smoothly when launched as a Non-Steam Game inside Gamescope Game Mode without relying on an external Xwayland bridge.

### 4. Handheld Fractional Scaling & Screen Clamping
ARM64 handheld screens frequently use 720p or 800p displays paired with 125%–150% fractional scaling.
- The window geometry dynamically detects available screen bounds and clamps dimensions to fit the display.
- Action bars, sidebars, and dialog buttons remain accessible without clipping offscreen.

### 5. Native ARM64 Steam Detection
Steam on ARM64 Linux resides in dedicated ARM runtime directories:
- ASSella automatically resolves and hooks native ARM Steam at `~/.local/share/Steam/steamrtarm64`.
- Manifest discovery and client version probing natively inspect ARM64 manifest structures.

---

## Requirements Checklist

Before testing ASSella on ARM64, ensure your environment meets these requirements:

- [x] **64-bit ARM Handheld**: Powered by an ARM64 (aarch64) SoC.
- [x] **Supported OS**: Armada OS or compatible ARM64 Linux distribution.
- [x] **Native ARM Steam Client**: Native Steam installed under `~/.local/share/Steam/steamrtarm64/steam`.
- [x] **SLSsteam ARM64 Preview**: Early preview build of `SLSsteam.so` built for ARM64 installed in `~/.local/share/SLSsteam/SLSsteam.so`.
- [x] **Hubcap Manifest API Key**: Connected Discord account and active Hubcap API key (see [Installation Guide](/install)).

---

## How to Run

### Step 1: Download the ARM64 AppImage Preview
Obtain the latest preview binary (`ASSella-arm64.AppImage`) for your ARM64 device.

### Step 2: Grant Execution Permissions
Open your terminal in Desktop Mode and make the AppImage executable:

```bash
chmod +x ASSella-arm64.AppImage
```

### Step 3: Launch in Desktop Mode
You can launch ASSella directly from the terminal or file manager:

```bash
./ASSella-arm64.AppImage
```

### Step 4: (Optional) Add to Steam for Game Mode
To access ASSella directly from the handheld gaming UI:
1. Open Steam in Desktop Mode.
2. Select **Games** $\rightarrow$ **Add a Non-Steam Game to My Library...**
3. Browse and select `ASSella-arm64.AppImage`.
4. Return to Gaming Mode / Gamescope and launch ASSella whenever needed.

---

## Current Status & Feedback

Because ARM64 gaming on Linux is an actively evolving space, certain features may undergo adjustments between releases. If you run into issues, unexpected behaviors, or visual glitches on your specific handheld hardware, sharing testing feedback and logs helps stabilize the experience for everyone.
