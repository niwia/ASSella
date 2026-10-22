# Uran (Decky Plugin)

<img width="100%" alt="Uran Decky Plugin Banner" src="https://github.com/user-attachments/assets/b664f362-4412-4bb6-9924-6121a6eecb10" style="border-radius: 8px; margin-bottom: 1.5rem;" />

**Uran** is the official [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) companion plugin for [ASSella](https://github.com/niwia/ASSella) and [SLSsteam](https://github.com/AceSLS/SLSsteam).

It brings the full power of ASSella's **AT0-M** (native Steam download) mode directly into SteamOS Game Mode. Search games, inject depot keys, manage DLCs, and apply multiplayer patches right from your Steam Deck's Quick Access Menu or directly from the Steam Store page.

---

## Which Setup Describes You?

::: tip Path 1: Already using ASSella Desktop?
If you already installed and configured ASSella on your Steam Deck or Linux PC, **you are practically ready to go!**
- Uran automatically detects and reads your existing SLSsteam `config.yaml`.
- Uran automatically shares your Hubcap API key from ASSella's configuration (`~/.config/assella`).
- Injected games and library items stay perfectly in sync between ASSella and Uran (`plugin_library.json`).

**Skip straight to [Installation via Decky Loader](#installation-via-decky-loader).**
:::

::: info Path 2: Starting Fresh on Steam Deck?
If you are starting on a clean Steam Deck without ASSella installed:
1. **Install Decky Loader**: If you haven't yet, install Decky Loader from [decky.xyz](https://decky.xyz/).
2. **Install SLSsteam (Headcrab)**: SLSsteam is required to manage decryption keys and manifests. Follow our [Headcrab Setup Guide](/install#_3-reset-steam-to-vanilla) to install SLSsteam.
3. **Get a Hubcap Manifest API Key**: Get your free API key at [hubcapmanifest.com](https://hubcapmanifest.com/) (see the [Hubcap Key Setup Guide](/install#_1-get-your-hubcap-manifest-api-key)).
:::

---

## Installation via Decky Loader

Uran is distributed as a pre-packaged plugin zip that you can install directly using Decky Loader's built-in manual installer.

### Step 1: Download the Plugin ZIP

Download the latest `Uran.zip` from the official GitHub releases:

**[Download Uran.zip on GitHub Releases](https://github.com/niwia/Uran/releases)**

Save `Uran.zip` to your Steam Deck's `Downloads` folder (or any accessible folder).

### Step 2: Install via Decky Loader Settings

You can perform this step in **Game Mode** or in **Desktop Mode** (via Steam Big Picture mode):

1. Open the Decky Loader menu by pressing the **Quick Access Menu button** (`...`) on your Steam Deck and navigating to the **Plug** icon.
2. Select the **Settings (gear icon)** at the top of the Decky menu.
3. Go to the **Developer** tab in the left sidebar.
4. Scroll down to **"Install plugin from ZIP"** (or *Manual Plugin Install*).
5. Browse your filesystem and select the `Uran.zip` file you downloaded.
6. Confirm the installation. Decky Loader will extract and activate Uran immediately!

::: tip Desktop Mode Tip
If you are in Desktop Mode, you don't need to switch back to Game Mode just to install plugins. Simply launch **Steam Big Picture Mode** (the icon in the upper-right corner of Steam), open Decky Loader, and install the ZIP through Decky's Developer settings.
:::

---

## Initial Setup

Once installed, open the Decky menu (`...`), select the Uran plugin, and head over to the **Settings** tab.

### 1. Hubcap API Key Entry (Phone QR Setup)

Entering a long API key with the Steam Deck's on-screen keyboard can be tedious. Uran includes a built-in LAN setup server so you can paste your key from your phone or PC:

1. In Uran, go to the **Settings** tab and locate the **Hubcap API Key** section.
2. Select **Setup via QR Code**. A QR code will appear on your Steam Deck screen.
3. Make sure your phone is connected to the same Wi-Fi network as your Steam Deck, then scan the QR code with your phone camera.
4. A secure, token-protected web page served directly by your Steam Deck will open in your mobile browser.
5. Paste your Hubcap API key and tap **Save Key**.
6. Uran automatically validates the key against Hubcap, writes it to the shared ASSella config, and closes the mobile page.

::: info Already an ASSella user?
If your Hubcap key is already configured in ASSella, Uran reads it automatically. You will see your username, remaining daily quota, and key expiration date immediately displayed in the Settings tab.
:::

### 2. ISP Bypass & Route Configuration

Some Internet Service Providers (ISPs) or regional firewalls block or throttle access to `hubcapmanifest.com`. Uran features an automatic Edge Proxy fallback to ensure reliable downloads:

In **Settings &rarr; Hubcap Connection**, choose your preferred route:

- **Automatic (Recommended)**: Tries connecting directly to Hubcap first. If your connection encounters transport-level failures (DNS blocking, connection reset, or TLS interception), it automatically retries via the Cloudflare Edge Proxy.
- **Edge proxy only**: Forces all API requests through the proxy. Ideal if your ISP blocks Hubcap by default.
- **Direct only**: Disables the proxy fallback completely.

Click **Test Connection** at any time to test reachability for both routes.

---

## How to Install Games (2 Ways)

Uran gives you two seamless ways to search, register, and queue games into Steam.

### Method 1: The Steam Store In-Page Button

Uran can inject an **"Inject with Uran"** button directly into the official Steam Store page in Game Mode. You can browse the Steam Store as usual and install titles with a single click.

#### Enabling CEF Debugging (Required for Store Button)
For Uran to communicate with the Steam Store webview, Steam's Chrome Embedded Framework (CEF) debugging port must be enabled:

1. Open the Decky Loader menu (`...`) &rarr; select the **Settings (gear icon)**.
2. Navigate to the **Developer** tab.
3. Toggle **Enable CEF Debugging** to **ON**.
4. *(Optional / Alternative)* In Steam Settings &rarr; **System** &rarr; toggle **Enable Developer Mode** ON, then go to the new **Developer** menu at the bottom and enable **CEF Remote Debugging**.
5. Restart your Steam Deck if prompted.

#### Using the Store Button:
1. Open the **Steam Store** in Game Mode and browse to any game page.
2. Once the page loads, look for the injected Uran action button alongside the store purchase options.
3. Click the button to automatically fetch the manifests, verify depot keys against Hubcap, atomic-write the configuration to SLSsteam, and queue the game for download in your Steam library.

::: tip Store Button Status
You can check if CEF debugging is active at any time by going to **Uran Settings &rarr; Steam Store Status**. It will show whether the CEF debugging port (8080–8082) is open and display recent injection diagnostic logs.
:::

---

### Method 2: Search Tab in the Plugin

If you prefer searching directly without opening the Steam Store webview:

1. Press the Quick Access button (`...`) and open **Uran**.
2. Select the **Search** tab.
3. Type the name of any game or enter its exact **Steam AppID** (e.g. `1245620` for Elden Ring).
4. Select the game from the search results to inspect its metadata and depots.
5. Tap **Inject Game**. Uran will:
   - Connect to Hubcap to download the manifest bundle and decryption keys.
   - Parse comment-aware depot definitions (ignoring depots marked with missing keys).
   - Atomically register depots and keys into `config.yaml`.
   - Send an instantaneous hot-reload signal to `/tmp/SLSsteam.API` (`RELOAD`).
   - Extract manifest files into Steam's `depotcache/`.
6. Open your Steam Library &rarr; the game is now registered and ready to download!

---

## AT0-M Library Management

The **AT0-M** tab is your central hub for managing games registered through AT0-M mode.

### Instant Game Overview
- View all your injected games, current installation status, and disk sizes.
- Select any game to open its dedicated management page.
- Metadata is cached locally on disk with a TTL so game pages render instantly, refreshing in the background only when needed.

### Granular DLC Toggles
- The base game AppID is always registered.
- Every available DLC is listed with an easy on/off toggle.
- **Zero leftovers**: Toggling a DLC off cleanly removes exactly the depots and decryption keys that DLC introduced — leaving your `config.yaml` tidy and preventing Steam `DepotWithoutKey` errors.

### Refresh Keys & Depots
If a newly released game didn't have decryption keys for certain depots when you first injected it, simply tap **Refresh Keys & Depots**. Uran queries Hubcap again, retrieves the newly published keys, and registers their corresponding depots into your configuration in one atomic pass.

### Clean Uninstall
Need to free up storage or remove a title? Uran provides a safe, complete uninstall:
1. Open the game in the **AT0-M** tab and select **Uninstall Game**.
2. Uran confirms the action and safely cleans up:
   - Game install folder and files.
   - Steam metadata (`appmanifest_<appid>.acf`, `downloading/`, `temp/`).
   - Proton prefix (`compatdata/<appid>/`).
   - All references from `config.yaml` and `plugin_library.json`.
   - *Note: Shared Steamworks redistributables are safely preserved for other games.*

---

## Per-Game Integrations Explained

Every game page in the AT0-M tab features an **Integrations** section to enable multiplayer and compatibility patches with a single click:

| Integration | What It Does | When to Use It |
| :--- | :--- | :--- |
| **SLSonline** | Maps the game's network identity to *Spacewar* (AppID `480`) via `FakeAppIds`. | Use for games that support peer-to-peer multiplayer or Steamworks online lobbies with friends. |
| **NetSock** | Applies a Steam Networking Sockets compatibility patch via `LD_AUDIT` launch options. | Use for games that experience network disconnects or multiplayer lobby handshake failures under Wine/Proton. |
| **EOS Proxy** | Injects an Epic Online Services proxy DLL into the game's prefix. | Use for titles requiring Epic Online Services authentication or crossplay matchmaking. |
| **EOS + NetSock** | Enables both the EOS proxy and NetSock patches simultaneously. | Use for games that rely on both Epic Online Services crossplay and Steam socket networking. |

---

## Steam Library "Manage" Menu Hooks

Uran integrates directly into Steam's native context menus. In your Steam Library, select any game, click the **Gear (Manage)** icon (or press `Menu` &rarr; `Manage`), and you'll find custom Uran shortcuts:

- **Why isn't this downloading?**: Runs an instant diagnostic check against your SLSsteam configuration, manifest files, and depot keys, explaining exactly what is preventing Steam from completing the download.
- **Add / Remove FakeAppId (480)**: Quick shortcut to toggle SLSonline Spacewar multiplayer without opening the plugin.
- **Fetch DLCs**: Automatically scans Hubcap for newly added DLCs and offers to add them.
- **Fetch / Update Manifest**: Downloads the latest manifest versions from Hubcap and updates Steam's depot cache.
- **Remove Pinned Manifests**: Clears any pinned manifest IDs in `config.yaml`.

---

## Troubleshooting & FAQ

::: details The "Inject with Uran" button doesn't appear on Steam Store pages
1. Ensure CEF Debugging is turned on in Decky Loader Settings &rarr; **Developer** &rarr; **Enable CEF Debugging**.
2. Restart your Steam Deck to ensure Steam launched with `--cef-enable-debugging`.
3. Check **Uran Settings &rarr; Steam Store Status**: it will report whether ports 8080–8082 are open and display recent injection logs.
4. If Valve recently updated the Steam Store layout, the button placement might be adjusted in an upcoming Uran release. You can always use the **Search** tab inside the plugin in the meantime!
:::

::: details "Connection Failed" or "Route Blocked" when testing Hubcap
1. Go to **Settings &rarr; Hubcap Connection**.
2. Change the route setting from *Automatic* to **Edge proxy only**.
3. Tap **Test Connection**. The Cloudflare Edge Proxy will bypass ISP-level DNS filtering and regional connection drops.
4. Verify your internet connection and ensure your Hubcap API key is valid.
:::

::: details Steam displays "Depot without key" or download pauses
- Uran automatically filters out depots that do not have valid decryption keys in Hubcap.
- If a download pauses, use the Steam Library gear &rarr; **Manage &rarr; Why isn't this downloading?** option to inspect the exact depot status.
- Tap **Refresh Keys & Depots** in the AT0-M tab to verify if new keys have arrived.
:::

::: details Do I need to restart Steam after injecting a game?
No! Uran writes changes atomically to `config.yaml` and immediately transmits a `RELOAD` signal to `/tmp/SLSsteam.API`. SLSsteam applies configuration changes instantly without needing a Steam restart.
:::

---

## Updates

Uran keeps track of new GitHub releases. Go to **Settings &rarr; Check for Updates** to compare your installed version against the latest release. 

When an update is available, you can update it directly through Decky Loader's plugin management or download the new `Uran.zip` release.
