<p align="center">
  <img src="src/res/logo/icon.png" width="128" height="128" alt="ASSella Logo" />
</p>

# ASSella

ASSella is a lightweight fork of ACCELA designed for Linux and Steam Deck.

ASSella 1.0 (Main Branch) is built on top of standard ACCELA with strictly 3 targeted changes:
1. **Workshop Downloader (beta) bundled** (`workshop_downloader_linux`).
2. **Steamless AIO (beta) bundled** (`steamless-aio.sh`).
3. **Library View Filtering**: Removed showing installed Steam games in the ACCELA library view so only games downloaded via ACCELA/ASSella are displayed.

> **Note:** You can always uninstall ASSella or revert back to standard ACCELA at any time using the installer menu.

---

## 📦 Beta Branch Installation (Feature-Rich Build)

For advanced features including Import Mode (user-provided LUA/manifests), Smart Depot Selection, Version Rollbacks, SLS Denuvo management, and thread-safe library scanning, install the **Beta Branch**:

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

> **⚠️ Warning:** The Beta branch is an active work-in-progress with approximately **90% stability**. If you encounter any unexpected issues, you can easily switch back to the stable ASSella 1.0 main branch or original ACCELA using the installer options.

---

## 📋 Requirements
* **Headcrab (SLSsteam)**: Required to intercept and download depots:
  `curl -fsSL headcrab.pages.dev | bash`
* **.NET 9 Runtime**: Automatically installed if missing, required for Steamless and DepotDownloader tools.
* **Hubcap API Key**: Required to query manifest histories.

---

*god is in the ass*
