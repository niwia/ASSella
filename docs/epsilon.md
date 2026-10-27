# Epsilon (Browser Bridge)

**Epsilon** is a lightweight, zero-idle-overhead background service for Linux and SteamOS. Working alongside the **Photar** browser script, it lets you register games and decryption keys directly into **SLSsteam** and **AT0-M** straight from the official Steam Store and SteamDB websites with a single click.

It is designed for desktop Linux users and Steam Deck owners who browse the store in Desktop Mode or on their PC, without needing to open Game Mode or launch a full GUI application.

---

## How It Works

Epsilon and Photar work seamlessly behind the scenes:

- **Photar (Browser Script)**: A Tampermonkey / Violentmonkey userscript that adds an **Add to at0-m** button to game pages on `store.steampowered.com` and `steamdb.info`.
- **Epsilon (System Service)**: A small, highly optimized companion bridge that communicates securely with your local Steam and SLSsteam installation on port `1952`.
- **Zero Idle Footprint**: Epsilon uses Linux systemd socket activation. The service consumes **0% CPU** and **0 MB RAM** while idle. It wakes up on demand when you click the button in your browser, runs the task, and powers down automatically after 120 seconds of inactivity.
- **Strict Localhost Security**: Epsilon listens exclusively on `127.0.0.1:1952`, enforces Host header verification to prevent DNS rebinding attacks, and restricts CORS strictly to official Steam Store and SteamDB domains.

---

## Which Setup Describes You?

::: tip Path 1: Already using ASSella or Uran?
If you already have ASSella or the Uran Decky plugin set up on your machine:
- SLSsteam is already installed and configured.
- Your Hubcap API key is already saved in your settings.
- Required Lua interceptor plugins are already deployed.

You can jump straight to **[Step 1: Install Epsilon Service](#step-1-install-epsilon-service)**.
:::

::: info Path 2: Starting Fresh on Linux or Steam Deck?
If you are configuring a fresh system without ASSella or Uran, ensure you complete these essential prerequisites first:
1. **Install SLSsteam (Headcrab)**: SLSsteam is required to manage decryption keys and manifests. Follow our [Headcrab Setup Guide](/install#_3-reset-steam-to-vanilla).
2. **Get a Hubcap Manifest API Key**: Get your free API key at [hubcapmanifest.com](https://hubcapmanifest.com/) (see the [Hubcap Key Setup Guide](/install#_1-get-your-hubcap-manifest-api-key)).
3. **Save your API Key**: Place your API key in `~/.config/assella-decky/settings.json` under `"hubcap_api_key"` or inside your SLSsteam directory in `spacebunny.conf.lua`.
:::

---

## Step 1: Install Epsilon Service

You can install Epsilon with a single terminal command.

Open a terminal (Konsole on Steam Deck) and run:

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/Uran/main/install_epsilon.sh | bash
```

### What This Does

1. Downloads the lightweight backend components to `~/epsilon`.
2. Verifies that python3 and required dependencies are available.
3. Automatically runs an environment check to verify that SLSsteam, plugins, and your Hubcap key are present.
4. Generates and activates a user systemd socket `epsilon.socket` listening on `127.0.0.1:1952`.

::: tip Custom Installation Directory
By default, Epsilon installs to `~/epsilon`. If you prefer a custom folder inside your home directory, pass it as an argument:
```bash
curl -fsSL https://raw.githubusercontent.com/niwia/Uran/main/install_epsilon.sh | bash -s -- "$HOME/my_epsilon"
```
:::

---

## Step 2: Install Photar Browser Script

Photar is the browser script that injects the **Add to at0-m** button into Steam and SteamDB.

1. **Install a Userscript Extension**:
   Install **Tampermonkey** or **Violentmonkey** in your web browser:
   - [Tampermonkey for Firefox](https://addons.mozilla.org/en-US/firefox/addon/tampermonkey/)
   - [Tampermonkey for Chrome / Brave / Edge](https://chromewebstore.google.com/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo)

2. **Install the Photar Script**:
   Click the link below to install the userscript:

   **[Install Photar Userscript](https://raw.githubusercontent.com/niwia/Uran/main/photar/photar.user.js)**

3. When prompted by Tampermonkey, click **Install**.

---

## Using Epsilon & Photar

Once installed, simply browse games in your web browser:

### 1. Steam Store Website

Navigate to any game page on `https://store.steampowered.com/app/<id>/`.

- An **Add to at0-m** button will appear in the queue actions bar alongside the Wishlist button, styled to match Steam's native theme.
- Clicking the button registers the game into SLSsteam and fetches decryption keys in the background.
- Once added or if already installed in your Steam library, the button automatically updates to **In Library**.
- Clicking **In Library** allows you to re-fetch keys or re-project configurations if needed.

### 2. SteamDB Website

Navigate to any game page on `https://steamdb.info/app/<id>/`.

- An **Add to at0-m** button will appear in the action buttons row.
- Games already present in your local SLSsteam configuration or Steam library will display with green borders and show **In Library**.

### 3. Missing Prerequisites Detection

If your SLSsteam setup, Lua plugins, or Hubcap API key are missing or not yet configured:
- The button detects this status automatically and shows a tooltip: `Setup required: SLSsteam, plugins, or Hubcap key missing. Click to open guide.`
- Clicking the button displays a confirmation dialog offering to open this guide so you can finish setting up.

---

## Managing the Background Service

Because Epsilon runs as a standard systemd user service, you can manage it with standard `systemctl` commands:

### Check Service & Socket Status

```bash
systemctl --user status epsilon.socket
```

### View Live Logs

```bash
journalctl --user -u epsilon.service -f
```

### Temporarily Stop Epsilon

```bash
systemctl --user stop epsilon.socket epsilon.service
```

### Restart Epsilon

```bash
systemctl --user restart epsilon.socket
```

### Updating Epsilon from GitHub

Epsilon can be kept updated in multiple ways:

1. **Automatic Daily Updates**:
   The installer automatically configures and enables a user systemd timer (`epsilon-updater.timer`) that silently checks GitHub main and applies code updates once a day in the background.

2. **Manual 1-Command Update**:
   To immediately pull the latest code and reload the bridge at any time, run:
   ```bash
   ~/epsilon/epsilon/update.sh
   ```
   Alternatively, you can re-run the remote curl installer at any time to refresh components in-place:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/niwia/Uran/main/install_epsilon.sh | bash
   ```

### Updating the Photar Browser Script

Photar includes built-in Tampermonkey auto-update URLs (`@updateURL` and `@downloadURL`).
Tampermonkey checks GitHub automatically in the background and updates Photar whenever a new version is published. You can also trigger an update check manually inside Tampermonkey's dashboard under **Installed Userscripts &rarr; Check for script updates**.

### Uninstall Epsilon

To completely remove Epsilon, its updater timer, and its systemd socket:

```bash
~/epsilon/epsilon/uninstall.sh
```

---

## Troubleshooting & Error Codes

When a prerequisite is missing or an error occurs during injection, the button in your browser displays a **3-digit error code in red** (such as `501`, `502`, `401`, or `505`). Clicking the button immediately opens the corresponding troubleshooting entry below.

### Error Codes Reference

| Code | Cause | Resolution |
| :--- | :--- | :--- |
| **[401](#err-401)** | Hubcap API Key Missing or Invalid | Save your Hubcap API key in settings or `spacebunny.conf.lua` |
| **[402](#err-402)** | Hubcap Daily Quota Exhausted | Wait for daily quota reset or upgrade tier on Hubcap |
| **[404](#err-404)** | Manifest Not Found on Hubcap | Game manifest bundle is not available on Hubcap |
| **[429](#err-429)** | Hubcap Rate Limit Reached | Wait a moment before requesting more games |
| **[501](#err-501)** | SLSsteam Offline or Inactive | Start Steam with SLSsteam active |
| **[502](#err-502)** | SLSsteam Lua Plugins Missing | Add your Lua interceptor plugin to `~/.config/SLSsteam/plugins/` |
| **[503](#err-503)** | Plugins Disabled in SLSsteam Config | Set `Plugins: yes` in `~/.config/SLSsteam/config.yaml` |
| **[504](#err-504)** | SLSsteam config.yaml Not Found | Launch Steam with SLSsteam once to generate config |
| **[505](#err-505)** | Epsilon Bridge Offline | Enable and start `epsilon.socket` via systemctl |
| **[701](#err-701)** | Decryption Keys Not Found | No candidate depot keys found for this title |
| **[707](#err-707)** | General Injection Error | Review daemon log via `journalctl --user -u epsilon.service` |

---

<a id="err-401"></a>
### Error 401: Hubcap API Key Missing or Invalid

**Cause**: No Hubcap API key is configured on your system, or the key has expired or is invalid.

**Resolution**:
1. Get a free API key at [hubcapmanifest.com](https://hubcapmanifest.com/).
2. Save your API key using any of the following methods:
   - If using ASSella / Uran: Enter it in plugin settings under Hubcap Key.
   - If using standalone Epsilon: Place it in `~/.config/assella-decky/settings.json`:
     ```json
     {
       "hubcap_api_key": "YOUR_KEY_HERE"
     }
     ```
   - Or add it directly to your SLSsteam configuration in `~/.config/SLSsteam/spacebunny.conf.lua`:
     ```lua
     hubcap = {
       api_key = "YOUR_KEY_HERE",
     }
     ```

---

<a id="err-402"></a>
### Error 402: Hubcap Daily Quota Exhausted

**Cause**: Your Hubcap API key has exceeded its allowed daily request limit (`can_make_requests: false` or `daily_usage >= daily_limit`).

**Resolution**:
1. Wait for your daily limit to reset at midnight UTC.
2. Check your current quota and tier usage on [hubcapmanifest.com/dashboard](https://hubcapmanifest.com/).

---

<a id="err-404"></a>
### Error 404: Manifest Not Found on Hubcap

**Cause**: Hubcap does not have manifest data or depot keys indexed for this specific AppID.

**Resolution**:
1. Verify that the AppID is a standalone base game.
2. Check if the game is newly released and not yet synced to Hubcap's repository.

---

<a id="err-429"></a>
### Error 429: Hubcap Rate Limit Reached

**Cause**: Too many requests were sent to the Hubcap API in a short burst.

**Resolution**:
1. Pause for 30 to 60 seconds before clicking inject again.
2. Avoid rapid repetitive clicks on store pages.

---

<a id="err-501"></a>
### Error 501: SLSsteam Offline or Inactive

**Cause**: Steam is running normally without the `SLSsteam.so` library loaded in memory, or SLSsteam is not installed.

**Resolution**:
1. Ensure SLSsteam is installed in your Steam environment. Refer to the [Headcrab Guide](/install#_3-reset-steam-to-vanilla).
2. Launch Steam via the SLSsteam wrapper script or Desktop shortcut so that `LD_PRELOAD` injects `SLSsteam.so`.
3. If Steam is already running, exit Steam completely and relaunch via SLSsteam.

---

<a id="err-502"></a>
### Error 502: SLSsteam Lua Plugins Missing

**Cause**: The SLSsteam `plugins/` directory contains no active `.lua` scripts.

**Resolution**:
1. Check your SLSsteam plugins folder:
   - Native: `~/.config/SLSsteam/plugins/`
   - Flatpak: `~/.var/app/com.valvesoftware.Steam/.config/SLSsteam/plugins/`
2. Place your preferred Lua download plugin (e.g. `spacebunny.lua` or `download.lua`) into that folder.

---

<a id="err-503"></a>
### Error 503: Plugins Disabled in config.yaml

**Cause**: The plugins toggle in SLSsteam configuration is explicitly set to `Plugins: no`.

**Resolution**:
1. Open `~/.config/SLSsteam/config.yaml` in a text editor.
2. Locate the line `Plugins: no` and change it to:
   ```yaml
   Plugins: yes
   ```
3. Save the file and restart Steam.

---

<a id="err-504"></a>
### Error 504: SLSsteam config.yaml Not Found

**Cause**: `~/.config/SLSsteam/config.yaml` does not exist. SLSsteam must be launched at least once to create its default configuration structure.

**Resolution**:
1. Launch Steam via SLSsteam once.
2. Wait for Steam to reach the main store or library view.
3. Verify that `~/.config/SLSsteam/config.yaml` has been generated.

---

<a id="err-505"></a>
### Error 505: Epsilon Bridge Offline

**Cause**: The browser script cannot connect to `http://127.0.0.1:1952`. The systemd user socket is either not started or Epsilon is not installed.

**Resolution**:
1. Check the socket status:
   ```bash
   systemctl --user status epsilon.socket
   ```
2. Enable and start the socket:
   ```bash
   systemctl --user enable --now epsilon.socket
   ```
3. Test connectivity locally:
   ```bash
   curl -s http://127.0.0.1:1952/ping
   ```
   If it returns JSON with `"service": "epsilon"`, refresh your store page.

---

<a id="err-701"></a>
### Error 701: Decryption Keys Not Found

**Cause**: No valid candidate depots or decryption keys could be resolved for this title.

**Resolution**:
1. Confirm whether this game requires third-party accounts or external launcher DRM.
2. Verify that manifest bundles exist on Hubcap for this AppID.

---

<a id="err-707"></a>
### Error 707: General Injection Error

**Cause**: An unexpected exception occurred while processing the injection request.

**Resolution**:
1. Inspect the live systemd service log for details:
   ```bash
   journalctl --user -u epsilon.service -n 50 --no-pager
   ```
2. If issues persist, test restarting the socket:
   ```bash
   systemctl --user restart epsilon.socket
   ```

