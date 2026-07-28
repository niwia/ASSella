<p align="center">
  <img src="src/res/logo/icon.png" width="128" height="128" alt="ASSella Logo" />
</p>

# ASSella (Beta Branch)

ASSella is a powerful, feature-rich fork of ACCELA — a Steam game downloader and launcher designed for Linux and Steam Deck. It bundles crucial quality-of-life additions, automated game post-processing tools, and thread-safe backend improvements.

![ASSella Banner](./assela_banner_v2.png)

---

## 🚀 Key Features (ASSella vs. 1.0 Stable ACCELA)

### 📥 Core Downloading & Rollbacks
* **Import Mode (User-Provided LUA / Manifests)**: Drop your own `.lua` files (into `cached_luas/`) or `.zip` files (into `hubcap_manifests/`), then click **Scan for LUA** in the library. ASSella parses the files, populates your depot keys database, and runs a free staleness check against Steam PICS. Fresh LUAs fetch manifests via the cheap `/generate/appmanifest` endpoint, saving your Hubcap API usage.
* **Version Rollback (Build History)**: Automatically retains backup manifests for the last `N` builds (configurable, defaults to 3). Rollback to previous builds using a simple dropdown in the Game Details panel.
* **Smart Depot Memory (Smart Selection)**: Remembers your selected depots (such as language files or DLCs) and automatically applies them to future game updates. You are only prompted if new depots are added.
* **Auto-Skip Single Depot**: Skips the depot selection pop-up entirely when a game has only one depot, speeding up installations.
* **Pause, Resume & Stop Controls**: Pause, resume, or terminate active downloads directly from the home screen.
* **Modern Download Interface (Screen 2.0)**: A clean, card-based checklist showing active game names (instead of raw depot IDs), download speeds, remaining queue count, and active stage indicators.

### ⚙️ Post-Processing & Steam Integration
* **ASShead Config Fixer**: Performs dynamic template alignment, keys merging, formatting, and deduplication on SLSsteam `config.yaml` to ensure clean configurations without manual updates.
* **Steam Workshop Downloader**: Search and download Steam Workshop items directly within the client.
* **Automated Achievement Generation**: Integrates SLScheevo to generate Steam achievements and statistics upon download completion. Displays exact counts of generated or up-to-date achievements.
* **One-Click DRM Removal**: Automates DRM patching using Steamless and Steamless-AIO for downloaded game binaries.
* **Linux Compatibility Features**: 
  * Automatically sets binary execution permissions (`chmod +x`).
  * Displays native Linux status (`No DRM (Linux)`) when downloading Linux-native depots.
* **GreenLuma Configurator**: Generates and manages GreenLuma AppList directories automatically.
* **Steam Auto-Update Blocker Monitor**: Displays if Steam auto-updates are blocked via `/home/deck/.steam/steam/steam.cfg`.

### 🗃️ Library Management & Performance
* **Thread-Safe Library Scanning**: Replaced list mutations with atomic operations, eliminating UI flickering or blank lists when the manager scans Steam folders.
* **Exclusions Support**:
  * Exclude specific games from background auto-update checks.
  * Exclude specific games from the global **"Update All"** queue.
* **Live Manifest Age Tracking**: Displays precise, human-readable manifest ages (in minutes, hours, days, or months) calculated directly from the manifest files on disk.
* **Hubcap API Dashboard**: Shows daily API usage limits, remaining key expiration, and reset details.

---

## 📦 Installation & Setup

Install or update ASSella on your system (such as Steam Deck) with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

The installer script provides interactive menus to:
1. Install / Update ASSella (packaged as a local AppImage).
2. Uninstall ASSella (restores original ACCELA configurations if backups are found).
3. Install or run **Headcrab** (sets up SLSsteam and patches Steam for compatibility).

---

## 🛠️ Testing & Web UI (Alpha/Remote Features)

The testing branch introduces a **remote WiFi Web UI** and a **headless mode** to control game downloads from other devices:

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/alpha/install_testing.sh | bash
```

* **Remote Web UI**: Access game grids, triggers updates, and check status from your phone or tablet on the same local network.
* **Offscreen Headless Mode**: Runs ASSella as a user service in the background using the systemd runtime.

---

## 📋 Requirements
* **Headcrab (SLSsteam)**: Required to intercept and download depots:
  `curl -fsSL headcrab.pages.dev | bash`
* **.NET 9 Runtime**: Automatically installed if missing, required for Steamless and DepotDownloader tools.
* **Hubcap API Key**: Required to query manifest histories.

---

*god is in the ass*
