# ASSella Installation Guide

Read the instructions thoroughly before proceeding!

::: tip Already using ACCELA or previously ran the Into the Wired (ETW) installation script?
You can skip the Headcrab reset and setup steps and go directly to **[Step 5 — Install ASSella](#_5-install-assella)** to replace ACCELA with ASSella.

- **Want to install or troubleshoot Headcrab?** Start at [Step 3 — Reset Steam to vanilla](#_3-reset-steam-to-vanilla), then continue through the following steps.
- **Only want to replace ACCELA with ASSella?** Go directly to Step 5.
:::

---

## 1. Get your Hubcap Manifest API key

Before using ASSella to download games, you need to obtain your **Hubcap Manifest API key**. ASSella relies heavily on this API, so make sure you complete this step first.

### Step 1: Connect your Discord account

1. Visit [hubcapmanifest.com](https://hubcapmanifest.com/).
2. Select **Continue to Discord** and follow the instructions to connect your account.

::: warning Verified Discord Required
You need a verified Discord account that is at least 24 hours old. This requirement helps prevent scammers and abuse.
:::

::: details Having trouble accessing the website?
If the website isn't accessible, try switching to a different DNS provider (such as Cloudflare `1.1.1.1` or Google `8.8.8.8`) and accessing it again.
:::

### Step 2: Generate your API key

1. Return to the [Hubcap Manifest website](https://hubcapmanifest.com/).
2. Click the **API** button in the top-right corner.
3. Click **Generate Key**.
4. Your Hubcap Manifest API key should now be available.

::: danger Keep your API key safe
Treat it like a password and never share it publicly or include it in screenshots. You will need this key when configuring ASSella.
:::

::: details I need a photo tutorial

#### Step 1: Visit Hubcap and click Continue to Discord
Navigate to [hubcapmanifest.com](https://hubcapmanifest.com/) and click the **Continue to Discord** button.

<img src="/images/hubcap/site_1.png" alt="Hubcap - Continue to Discord" style="border-radius: 8px; margin: 0.8rem 0; max-width: 100%;" />

#### Step 2: Authorize Discord account
Authorize the Hubcap Web Login application to link your verified Discord account.

<img src="/images/hubcap/site_2.png" alt="Discord Authorization Screen" style="border-radius: 8px; margin: 0.8rem 0; max-width: 100%;" />

#### Step 3: Click API in the top navigation bar
Once logged in, click the **API** link in the top menu bar.

<img src="/images/hubcap/site_3.png" alt="Hubcap Navigation - Click API" style="border-radius: 8px; margin: 0.8rem 0; max-width: 100%;" />

#### Step 4: Click Generate Key
Under the **My Key** tab, click the **Generate Key** button.

<img src="/images/hubcap/site_4.png" alt="Hubcap API Access - Generate Key" style="border-radius: 8px; margin: 0.8rem 0; max-width: 100%;" />

#### Step 5: Copy your generated API key
Copy your key by clicking the copy icon next to the key field.

<img src="/images/hubcap/site_5.png" alt="Hubcap - Copy API Key" style="border-radius: 8px; margin: 0.8rem 0; max-width: 100%;" />

:::

::: details Bonus: Where to enter or check your API key in ASSella

#### Step 1: Open Settings
In the main ASSella window, click the **Settings** (gear) icon located in the upper right header.

<img src="/images/hubcap/assella_settings_step1.png" alt="ASSella Main Window - Settings Gear Icon" style="border-radius: 8px; margin: 0.8rem 0; max-width: 100%;" />

#### Step 2: Paste your API Key and verify connection
In the Settings dialog:
1. Locate the **Hubcap API Key** input field.
2. Click **Paste** (or press Ctrl+V) to insert your API key.
3. Click **OK** to save. The Hubcap stats indicator below will reflect your active connection status.

<img src="/images/hubcap/assella_settings_step2.png" alt="ASSella Settings - Hubcap API Key Input and Status" style="border-radius: 8px; margin: 0.8rem 0; max-width: 100%;" />

:::

---

## 2. Choose your installation path

Before proceeding, choose the path that applies to you:

- **Existing ACCELA / ETW installation:** Skip directly to [Step 5 — Install ASSella](#_5-install-assella). You do not need to reset Steam or reinstall Headcrab just to replace the application.
- **Fresh installation or Headcrab troubleshooting:** Continue to Step 3 below.

---

## 3. Reset Steam to vanilla

This step is intended for users who need to reset their Steam setup before reinstalling Headcrab.

::: tip Stuck on loading screen in Game Mode?
If your Steam Deck or Linux PC is stuck in an infinite loading loop in Game Mode and you cannot access Desktop Mode, follow our **[Game Mode Boot Loop Fix (TTY Recovery Guide)](/recovery-tty)** to recover your system using a plain-text TTY terminal.
:::

### SteamOS preparation

*Users on standard desktop Linux distributions can skip this subsection.*

1. Open **Konsole**.
2. If your terminal prompt looks like this:
   ```text
   (1)deck@steamdeck
   ```
   Run:
   ```bash
   kate ~/.bashrc
   ```
3. Look for the following line:
   ```bash
   [[ $- != *i* ]] && return
   ```
4. Replace it with:
   ```bash
   if [[ $- != *i* ]]; then
       return
   fi
   ```
5. **Save the file.**
6. Your Konsole prompt should now look like this:
   ```text
   deck@steamdeck
   ```
   You can now proceed.

### Reset Steam

Run the following command in Konsole:

```bash
curl -fsSL "https://raw.githubusercontent.com/Deadboy666/h3adcr-b-modul3s/refs/heads/main/reset2vanilla.sh" | bash
```

::: info Note
This will revert Steam back to vanilla. You will not lose your installed games or library files.
:::

Once the process finishes:
1. Start Steam and **let it load completely**.
2. This allows Steam to reconfigure its bootstrap.
3. Once Steam has finished loading, **exit Steam again**.

---

## 4. Install or reinstall Headcrab

With Steam closed, run Headcrab in Konsole using either of the following commands:

::: code-group

```bash [Option 1 — Primary Command]
curl -fsSL headcrab.pages.dev | bash
```

```bash [Option 2 — Alternative CDN]
curl -fsSL "https://cdn.jsdelivr.net/gh/Deadboy666/h3adcr-b@refs/heads/main/headcrab.sh" | bash
```

:::

Let the installation finish. You can now proceed to Step 5 to install ASSella.

---

## 5. Install ASSella

Official releases: **[ASSella GitHub Releases](https://github.com/niwia/ASSella/releases)**

::: code-group

```bash [Option A (Recommended) — One-Line Installer]
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

```bash [Option B — Manual AppImage]
# Download ASSella.AppImage from GitHub Releases
chmod +x ASSella.AppImage
./ASSella.AppImage
```

:::

### Installer Notes
- The installer downloads and installs ASSella into `~/.local/share/ACCELA`.
- If an existing ACCELA installation is detected, the script backs up the previous application binary and creates a compatibility symlink.
- The installer **does not** delete your installed game files.
- The installer script does not explicitly migrate application settings, so make sure you retain a backup of any important configuration before proceeding.

---

## 6. Final Checklist

- [x] Obtained your Hubcap Manifest API key.
- [x] Installed or updated Headcrab if required.
- [x] Installed ASSella.
- [x] Configured your Hubcap Manifest API key in ASSella.
- [x] Confirmed that the application launches correctly.

---

### Need Help?

For questions regarding setup, join the [Hubcap Discord Server](https://discord.gg/hubcapsmanifest) and check `#help` &rarr; `#support-steamdeck-and linux`.

*Guide credits: @darealdari*
