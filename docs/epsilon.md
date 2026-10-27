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

### Uninstall Epsilon

To completely remove Epsilon and its systemd socket:

```bash
~/epsilon/epsilon/uninstall.sh
```

---

## Troubleshooting & FAQ

### The button says "Add to at0-m" but clicking does nothing or shows "Bridge offline"

1. Check if the socket is listening:
   ```bash
   systemctl --user status epsilon.socket
   ```
2. If inactive, start it with:
   ```bash
   systemctl --user enable --now epsilon.socket
   ```
3. Test if the bridge responds locally:
   ```bash
   curl -s http://127.0.0.1:1952/ping
   ```
   It should return JSON containing `"service": "epsilon"`.

### The installer warns about missing prerequisites

Run the diagnostic script inside your installation directory:

```bash
python3 ~/epsilon/epsilon/check_prereqs.py
```

It will report the exact status of:
- SLSsteam config (`~/.config/SLSsteam/config.yaml`)
- Lua plugins (`~/.config/SLSsteam/plugins/`)
- Hubcap Manifest API key

Follow the [Headcrab Guide](/install#_3-reset-steam-to-vanilla) or [Hubcap Key Guide](/install#_1-get-your-hubcap-manifest-api-key) to configure any missing component.
