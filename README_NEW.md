<p align="center">
  <img src="src/res/logo/icon.png" width="140" height="140" alt="ASSella Logo" />
</p>

<h1 align="center">ASSella</h1>

<p align="center">
  <b>A powerful Steam game downloader, manager & post-processor for Linux & Steam Deck</b>
</p>

<p align="center">
  <a href="#-installation"><img src="https://img.shields.io/badge/Platform-Linux_%7C_Steam_Deck-informational?style=for-the-badge&logo=linux&logoColor=white&color=0d1117" alt="Platform" /></a>
  <a href="#-installation"><img src="https://img.shields.io/badge/Version-2.5.1-blue?style=for-the-badge&logo=github&logoColor=white&color=0d1117" alt="Version" /></a>
  <a href="#-installation"><img src="https://img.shields.io/badge/Python-3.10+-green?style=for-the-badge&logo=python&logoColor=white&color=0d1117" alt="Python" /></a>
  <a href="#-installation"><img src="https://img.shields.io/badge/UI-PyQt6-purple?style=for-the-badge&logo=qt&logoColor=white&color=0d1117" alt="PyQt6" /></a>
  <a href="#-installation"><img src="https://img.shields.io/badge/License-Private-red?style=for-the-badge&color=0d1117" alt="License" /></a>
</p>

<br/>

<p align="center">
  <img src="./assela_banner_v2.png" alt="ASSella Banner" width="100%" />
</p>

---

## 📖 Overview

**ASSella** is a feature-rich fork of [ACCELA](https://github.com/niwia/ASSella) — a Steam game downloader and all-in-one game manager designed for **Linux** and **Steam Deck**. It wraps the entire download-to-play pipeline into a single desktop application: fetch manifests, download depots, remove DRM, generate achievements, manage your library, and handle Steam Workshop content — all from one interface.

ASSella ships as a portable **AppImage** and integrates deeply with **SLSsteam** and **Headcrab** for full Steam depot interception.

---

## ✨ Feature Overview

| Category | Highlights |
|:---|:---|
| 📥 **Downloading** | Manifest fetching, depot downloading, smart updates, version rollbacks |
| 📚 **Library** | Full game library with cover art, sorting, filtering, update checking |
| 🔓 **DRM Removal** | Automated Steamless & Steamless-AIO integration |
| 🏆 **Achievements** | Auto-generated Steam achievements & stats via SLScheevo |
| 🔧 **Workshop** | Search & download Steam Workshop items by ID or URL |
| 🎨 **Theming** | Material You dynamic themes, custom fonts, accent colors |
| 🌐 **Remote Access** | Built-in Web UI for remote game management over WiFi |
| 💻 **CLI & Headless** | Full command-line mode, headless background service, `accela://` URL handler |
| 🛡️ **ISP Bypass** | DNS-over-HTTPS + optional Tor/SOCKS5 proxy for blocked regions |

---

## 📥 Downloading & Manifest System

<table>
<tr><td width="50%">

### Hubcap API Integration
Search for any Steam game by name or AppID directly from the **Fetch Manifest** panel. ASSella queries the Hubcap API, filters out soundtracks/DLCs/demos automatically, and presents clean game results with cover art.

- Real-time search with intelligent result filtering  
- Cover art thumbnails with Spotify-style fading headers  
- Daily API usage dashboard (calls used / remaining / reset time)

</td><td width="50%">

### Smart Depot Selection
Choose exactly which depots (language packs, DLCs, platform files) to download. ASSella remembers your choices and auto-applies them on future updates.

- **Smart Depot Memory** — Remembers selected depots per game  
- **Auto-Skip Single Depot** — Skips selection when only one depot exists  
- **Manual Depot-BuildID-Manifest Selection** — Pick specific historical manifests  

</td></tr>
</table>

### 🔄 Version Rollback (Build History)
ASSella retains backup manifests for the last **N** builds (configurable, default: 3). Roll back to any previous version from the **Game Details** panel dropdown.

### ⚡ Smart Update Mode
When enabled, ASSella uses cached **AES depot keys** and **AppTokens** stored in a local SQLite database to assemble updates without re-downloading the full manifest ZIP — saving an API call per update while always fetching the latest Steam manifest files on demand. Falls back to the full endpoint automatically when needed.

### 📊 Download Interface 2.0
A modern, card-based download screen replacing the original raw depot-ID view:

- Active game name display (not depot IDs)  
- Real-time download speed & ETA  
- Remaining queue counter  
- Stage indicators (Fetching → Downloading → Processing → Installing)  
- **Pause / Resume / Stop** controls directly from the home screen  

---

## 📚 Game Library Management

<table>
<tr><td width="50%">

### Material Design 3 Library
A fully redesigned game library with elevated card components, capsule-style search bars, and rounded suggestion chips for update status. Each game card shows:

- Steam cover art (fetched & cached locally)  
- Game name with branch suffixes (e.g., `(beta)`)  
- Installation size  
- Update status chips: `Up to Date` / `Update Available`  
- DLC mode indicator `[DLC MODE]`  

</td><td width="50%">

### Library Operations
- **Update All** — Batch-update every installed game  
- **Exclusions** — Exclude specific games from auto-update checks or the Update All queue  
- **Manifest Age Tracking** — Shows how old each game's manifest is (minutes/hours/days/months)  
- **Thread-Safe Scanning** — Atomic operations eliminate UI flickering during library scans  
- **Multi-Library Support** — Scans all Steam library folders  

</td></tr>
</table>

### Game Details Panel
Right-click or select any game to access:
- Version rollback dropdown  
- Branch switching (public / beta / etc.)  
- DLC management & uninstallation  
- Open game directory  
- SLSsteam config management (FakeAppID, AppTokens)  
- GreenLuma AppList management  

---

## 🔓 DRM Removal (Steamless)

ASSella integrates **Steamless** and **Steamless-AIO** for automated DRM removal:

- Scans game directories for `.exe` files with DRM protection  
- Prioritizes likely game executables by name matching  
- Runs Steamless via **.NET 9 Runtime** directly (no Wine/Proton needed)  
- Creates backups of original binaries  
- Supports batch processing across all detected executables  
- **Resume Support** — Interrupted Steamless operations can be resumed  

> **Linux compatibility**: Automatically sets `chmod +x` on downloaded binaries and displays `No DRM (Linux)` for native Linux depots.

---

## 🏆 Achievement & Stats Generation

Automated achievement generation using **SLScheevo** and **Schema-Grabber**:

- Detects Steam credentials from settings  
- Runs `schema-grabber` to fetch and generate achievement schemas  
- Reports exact counts: `Generated 47 achievements` or `All 47 achievements up to date`  
- Supports batch generation across all installed games  
- Stats stored locally for offline use  

---

## 🔧 Steam Workshop Downloader

Download Workshop items directly within ASSella:

| Feature | Description |
|:---|:---|
| **URL or ID Input** | Paste Workshop URLs or raw item IDs |
| **Batch Downloads** | Comma/space-separated lists for multiple items |
| **Auto-Detection** | Parses `?id=` parameters from Steam URLs |
| **Depot Key Management** | Fetches and caches required manifest & depot keys |
| **Library Targeting** | Choose which Steam library to install workshop items into |

---

## 🎨 Theming & Customization

ASSella features a fully dynamic theming engine built on Material Design 3 principles:

```
┌──────────────────────────────────────────┐
│  🎨  Dynamic Color System               │
│                                          │
│  Accent Color    ████████  #C06C84       │
│  Background      ████████  #000000       │
│  Surface         ████████  (auto)        │
│  Surface Variant ████████  (auto)        │
│  Container       ████████  (auto)        │
│  Hover/Selected  ████████  (auto)        │
└──────────────────────────────────────────┘
```

- **Custom Accent & Background Colors** — Full color picker  
- **Dynamic Surface Generation** — Tonal containers, hover states, and outlines auto-calculated from your accent color  
- **Custom Fonts** — System fonts, bundled fonts (TrixieCyrG, Google Sans), or load any `.otf`/`.ttf`  
- **Roboto Default** — Falls back to Roboto → Google Sans → bundled TrixieCyrG  
- **Theme Presets** — Including "Ocean Breeze (Monet Blue)" default  
- **Sonic Mode** 🎵 — A special UI mode with custom Sonic font and color scheme  
- **Titlebar Position** — Top or bottom custom titlebar placement  

---

## 🌐 Remote Web UI

Access and control ASSella from any device on your local network:

```
http://<your-deck-ip>:8765
```

| Endpoint | Description |
|:---|:---|
| `GET /` | Full Web UI (HTML dashboard) |
| `GET /api/library` | Game library data |
| `GET /api/status` | Current download/task status |
| `GET /api/search?q=` | Search games |
| `POST /api/download` | Trigger a download |
| `POST /api/update` | Trigger an update |

- Responsive web dashboard accessible from phone, tablet, or PC  
- CORS-enabled API for custom integrations  
- Configurable port via settings or `--port` flag  

---

## 💻 CLI, Headless & URL Protocol

### CLI Mode
Process downloads entirely from the terminal with an `urwid`-based TUI:

```bash
# Download by AppID
assella --cli --appid 730

# Process a manifest ZIP
assella --cli /path/to/manifest.zip
```

Auto-detects and launches in available terminal emulators (WezTerm, Konsole, Alacritty, Kitty, xterm, and more).

### Headless Mode
Run ASSella as a background service with no display:

```bash
assella --headless --port 8765
```

- Sets `QT_QPA_PLATFORM=offscreen` automatically  
- Web UI available for remote control  
- Suitable for running as a `systemd` user service  

### URL Protocol Handler
Trigger downloads from the browser or other apps:

```
accela://download/730        → Download AppID 730
accela://cli/download/730    → CLI mode download
accela://helper/download/730 → Headless helper download
accela://zip/<path>          → Process a local ZIP file
```

### Headless Helper
Fully automated download pipeline for a single AppID — no UI, no prompts:

```bash
assella --helper --appid 730
```

Runs the complete flow: manifest download → ZIP processing → depot selection → installation.

### Background Update Checker

```bash
assella --check-updates
```

Performs a headless update check across all installed games and exits.

---

## 🛡️ ISP Bypass & Network

For users in regions where Hubcap API access is restricted:

- **DNS-over-HTTPS (DoH)** — Resolves `hubcapmanifest.com` via Cloudflare (1.1.1.1) and Google DNS  
- **Tor/SOCKS5 Proxy** — Optional managed background Tor process on port 9050  
- **Automatic Fallback** — Tries direct → DoH → Tor/SOCKS5 in sequence  
- **Connection Status Tracking** — UI shows current connection method  

---

## ⚙️ SLSsteam Config Management

ASSella includes **ASSfixer** — a built-in config cleanup & update tool for SLSsteam's `config.yaml`:

- Fetches the latest default config template from GitHub  
- Merges your personal values into the new template structure  
- Deduplicates entries, normalizes formatting  
- Validates config against known keys & types  
- Auto-backup before every write  
- Manages **FakeAppIDs**, **AppTokens**, **AdditionalApps** entries  
- **GreenLuma Configurator** — Auto-generates AppList directories  
- **Steam Auto-Update Blocker** — Monitors and controls `steam.cfg`  
- **SLSsteam API Integration** — Sends config commands directly to the SLSsteam API  

---

## 🗄️ Data & Caching

| Component | Storage | Purpose |
|:---|:---|:---|
| `steam_headers.db` | SQLite (~38 MB) | Cached Steam app metadata for instant search |
| `depot_keys.db` | SQLite | AES depot keys & AppTokens for smart updates |
| `hubcap_manifests/` | ZIP files | Cached manifest ZIPs from Hubcap API |
| `image_cache/` | PNG files | Cached Steam cover art |
| `install_history.json` | JSON | Installation history log |
| `update_status_cache.json` | JSON | Cached update check results |

---

## 📦 Installation

### One-Line Install (Linux / Steam Deck)

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

The interactive installer provides:

| Option | Description |
|:---|:---|
| **Install / Update** | Downloads the latest AppImage release |
| **Uninstall** | Removes ASSella and restores original ACCELA configs |
| **Headcrab** | Installs SLSsteam and patches Steam for compatibility |

### Testing / Alpha Branch

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/alpha/install_testing.sh | bash
```

Includes experimental features like the Remote Web UI and headless mode.

---

## 📋 Requirements

| Requirement | Purpose | Install |
|:---|:---|:---|
| **Headcrab (SLSsteam)** | Steam depot interception | `curl -fsSL headcrab.pages.dev \| bash` |
| **.NET 9 Runtime** | Steamless & DepotDownloader | Auto-installed if missing |
| **Hubcap API Key** | Manifest history queries | Configured in Settings |

### Python Dependencies

```
PyQt6 · PyYAML · requests · zstandard · steam · cryptography
protobuf · vdf · psutil · configobj · urwid
```

---

## 🏗️ Architecture

```
src/
├── main.py                    # Entry point, argument parsing, mode dispatch
├── core/
│   ├── steam_api.py           # Steam PICS & depot info
│   ├── steam_helpers.py       # Library detection, GreenLuma, ACF parsing
│   ├── morrenus_api.py        # Hubcap API client
│   └── tasks/
│       ├── download_depots_task.py       # Core depot downloader
│       ├── download_workshop_task.py     # Workshop item downloader
│       ├── generate_achievements_task.py # Achievement generation
│       ├── manifest_check_task.py        # Update checking
│       ├── process_zip_task.py           # Manifest ZIP processing
│       ├── smart_update_task.py          # Smart update (cached keys)
│       └── steamless_task.py             # DRM removal
├── managers/
│   ├── game_manager.py        # Game library state & operations
│   ├── task_manager.py        # Download queue orchestration
│   ├── job_queue_manager.py   # Job queue for sequential processing
│   ├── cli_manager.py         # CLI/TUI mode
│   ├── helper_manager.py      # Headless helper mode
│   ├── depot_key_manager.py   # SQLite depot key cache
│   ├── db_manager.py          # Steam headers database
│   └── ui_state_manager.py    # UI state persistence
├── ui/
│   ├── main_window.py         # Main application window
│   ├── theme.py               # Dynamic theming engine
│   ├── bottom_titlebar.py     # Custom frameless titlebar
│   ├── status_pager.py        # LCD-style status display
│   └── dialogs/
│       ├── gamelibrary.py     # Game library dialog
│       ├── fetchmanifest.py   # Manifest search & fetch
│       ├── settings.py        # Settings (multiple tabs)
│       ├── workshop.py        # Workshop downloader dialog
│       ├── depotselection.py  # Depot picker
│       ├── lain.py            # 🎮 The Wired Terminal minigame
│       └── credits.py         # Credits & version info
└── utils/
    ├── assfixer.py            # SLSsteam config manager
    ├── isp_bypass.py          # DoH + Tor proxy
    ├── web_server.py          # Built-in HTTP server
    ├── yaml_config_manager.py # SLSsteam YAML management
    ├── image_fetcher.py       # Cover art fetcher
    └── logger.py              # Logging framework
```

---

## 🕹️ Easter Egg

ASSella includes **The Wired Terminal** — a Serial Experiments Lain themed minigame hidden in the credits. Find it if you can.

---

<p align="center">
  <i>god is in the ass</i>
</p>

<p align="center">
  <sub>Built with ❤️ for the Steam Deck community</sub>
</p>
