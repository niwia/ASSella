# Online Multiplayer & Proxy Guide

ASSella and SLSsteam provide built-in, native support for playing multiplayer games online via community multiplayer fixes, Steamworks matchmaking spoofing, dynamic socket routing, and Epic Online Services (EOS) emulation.

This guide explains how each online component works, how to enable them in ASSella's UI, how they modify your configuration under the hood, and how to safely edit `config.yaml` manually.

---

## Overview: The 3 Pillars of Online Play

Playing games with online fixes on Linux and SteamOS typically involves up to three distinct components:

| Component | Technology | Primary Purpose | When Needed |
| :--- | :--- | :--- | :--- |
| **SLSonline** | `FakeAppIds` in SLSsteam | Spoofs the game's AppID (typically to **Spacewar 480**) | Required for any Steamworks lobby matchmaking or friend invites. |
| **Netsock Proxy** | `steamnetsock-patch` (`LD_AUDIT`) | Injects a socket-routing library for Steam Networking Sockets | Required when Proton/Wine cannot establish P2P sockets under spoofed AppIDs. |
| **EOS Proxy** | `eos-proxy` (`EOSSDK-Win64-Shipping.dll`) | Intercepts Epic Online Services authentication calls | Required for crossplay or multiplayer titles relying on Epic Online Services. |

<img width="100%" alt="ASSella Online Tiles Grid" src="/images/online_tiles_cropped.png" style="border-radius: 8px; margin: 1.5rem 0;" />

---

## 1. SLSonline (FakeAppId Spoofing)

### How It Works Under the Hood
Most Steam online fixes work by tricking the Steam client and Steamworks API into believing you are running **Spacewar** (AppID `480`) or another free multiplayer base game. Because Steam sees both you and your friends playing the same AppID, you can:
- Discover lobbies hosted by other players using the same fix.
- Send and accept Steam friend game invites via the Steam Overlay.
- Utilize Steam Datagram Relay (SDR) and P2P communication.

SLSsteam handles this at the client hook level via the `FakeAppIds` configuration directive. When configured, SLSsteam dynamically intercepts game startup requests and reports the fake AppID to Steam while allowing the game to load its genuine assets and depots.

### Enabling SLSonline in ASSella

1. Open your **Steam Library** in ASSella and click on the game to open **Game Details**.
2. On the **Info** tab, scroll to the bottom section.
3. Click the **SLSonline** material tile:
   - The tile will highlight in your theme accent color and display **Active**.
   - An **FID** (Fake AppID) input box appears next to the tile, pre-filled with `480` (Spacewar).
4. *(Optional)* If your game's multiplayer fix uses a custom AppID (such as `251040` for PAYDAY 2 Demo or `1007` for Syndicate), type the target AppID into the **FID** box.
5. Close the dialog or launch the game through Steam.

::: tip Global SLSonline
If you want **all** your installed games to report through Spacewar automatically without toggling them individually, navigate to **Settings → SLS** and set FakeAppId globally (`0: 480`).
:::

---

## 2. Netsock Proxy (`steamnetsock-patch`)

### Why Netsock is Needed on Linux & SteamOS
Modern Steam games frequently use **Steam Networking Sockets** for encrypted, reliable P2P UDP networking. 

Under Linux and Proton, when a game's AppID is spoofed (e.g. to `480`), Steam Networking Sockets can fail to bind socket interfaces or drop handshake packets due to mismatching SDR certificates and app tickets.

[steamnetsock-patch](https://github.com/yesyes0649/steamnetsock-patch) (developed by `yesyes0649`) solves this. It compiles into a Linux shared object (`netsock.so`) that hooks into the dynamic linker via `LD_AUDIT`. It intercepts networking socket calls at runtime and ensures that socket endpoints and NAT punching communicate through Steam's routing architecture.

### Enabling Netsock in ASSella

::: warning Prerequisites & Anti-Cheat Notice
- **SLSonline must be enabled first**: Netsock is an audit bridge for spoofed sockets; activate SLSonline before turning on Netsock.
- **Anti-Cheat Warning**: Because Netsock relies on `LD_AUDIT`, it is **not compatible** with Easy Anti-Cheat (EAC), BattlEye, or strict kernel/runtime anti-cheat systems. Use Netsock only for peer-to-peer and co-op titles.
:::

1. In **Game Details → Info tab**, ensure **SLSonline** is active.
2. Click the **Netsock** tile.
3. ASSella will show a one-time safety and anti-cheat advisory. Click **Yes** to confirm.
4. ASSella will automatically:
   - Check for `netsock.so` locally (or download the latest release from GitHub).
   - Place the library in `~/.config/SLSsteam/tools/netsock/netsock.so`.
   - Add the required launch option override to SLSsteam's `config.yaml`.
5. The Netsock tile will turn active.

---

## 3. EOS Proxy (`eos-proxy`)

### What is EOS Proxy?
Many titles—even those purchased on Steam—integrate **Epic Online Services** (EOS) for crossplay, multiplayer backend services, and account management.

When running these games with an online fix, the game will attempt to authenticate with Epic's servers using an official Epic ticket. Without a genuine Epic Games account link and product purchase, authentication fails with errors like *"EOS Login Failed"* or *"Cannot connect to online services"*.

[eos-proxy](https://github.com/yesyes0649/eos-proxy) (by `yesyes0649`) is a specialized replacement library for `EOSSDK-Win64-Shipping.dll`. It intercepts the EOS SDK initialization, authentication, and overlay calls, bypassing Epic account linking while still allowing peer-to-peer multiplayer to function.

### How ASSella Handles EOS Proxy

ASSella features automatic file detection and state tracking:

```
[Genuine Game Files]
   └── EOSSDK-Win64-Shipping.dll (Original)
             │
             ▼ ASSella: "Enable Proxy"
   ├── EOSSDK-Win64-Shipping.yes (Original backed up safely)
   └── EOSSDK-Win64-Shipping.dll (Patched proxy DLL active)
```

1. **Automatic Detection**: When you open **Game Details**, ASSella scans the game's directory for `EOSSDK-Win64-Shipping.dll`. If found, the **EOS Proxy** tile automatically appears in the bottom grid.
2. **Safe Backups**: When toggled on, the genuine DLL is renamed with a `.yes` extension (`EOSSDK-Win64-Shipping.yes`) and the proxy DLL is dropped in place.
3. **Restoration**: Clicking **Remove Proxy** deletes the proxy and renames the genuine `.yes` backup back to `.dll`.
4. **Game Update Detection ("Stale" State)**: If the game updates and Steam overwrites the proxy with a genuine DLL, ASSella detects the hash mismatch and labels the button amber (**Reapply Proxy**). Clicking it restores the patch with one click.

---

## 4. Combined Setup: Netsock + EOS Proxy

Modern Unreal Engine 4/5 co-op games and multi-platform releases often use **both** systems:
- Epic Online Services for lobby matchmaking and auth.
- Steam Networking Sockets for game server traffic.

In ASSella, you can activate both simultaneously. When both are available and SLSonline is active, Game Details arranges the controls into an intuitive 2x2 grid:

<img width="100%" alt="ASSella Game Details Dialog with Full Online Setup" src="/images/online_game_details.png" style="border-radius: 8px; margin: 1.5rem 0;" />

### Recommended Setup Order:
1. Turn on **SLSonline** (confirm FID is `480`).
2. Turn on **EOS Proxy** (if the tile is visible for your game).
3. Turn on **Netsock** (accept the compatibility dialog).
4. Launch the game from Steam.

---

## 5. What Edits Happen in SLSsteam `config.yaml`?

When you toggle these features in ASSella, the application modifies `~/.config/SLSsteam/config.yaml` using safe atomic writes. Here is what is added:

### SLSonline Modification (`FakeAppIds`)
```yaml
FakeAppIds:
  1245620: 480  # ELDEN RING -> Spacewar
```

### Netsock Modification (`LaunchOptions`)
```yaml
LaunchOptions:
  1245620: "env LD_AUDIT=\"/home/deck/.config/SLSsteam/tools/netsock/netsock.so\" %command%"
```

*(Note: On Flatpak Steam environments, the path automatically maps to the Flatpak runtime's SLS directory).*

### EOS Proxy Modification
EOS Proxy modifies game files directly inside `{game_directory}` and does not add entries to `config.yaml`.

---

## 6. Manual Editing Guide: SLSsteam `config.yaml`

If you prefer to edit `~/.config/SLSsteam/config.yaml` manually or want to inspect your configuration, you can do so directly using any text editor of your choice.

::: tip Instant Hot Reloading
SLSsteam monitors `config.yaml` and hot-reloads its configuration automatically. As soon as you save your changes in your text editor, they take effect immediately!
:::

To open and edit your configuration:
- In ASSella: Navigate to **Settings → Tools → Open Config** to open `config.yaml` directly in your system's default text editor.
- Or open `~/.config/SLSsteam/config.yaml` in whatever text editor you have available (Kate, Gedit, VS Code, nano, etc.), make your edits, and save.

::: danger If You Encounter Config Errors
If you make a formatting mistake, experience YAML syntax errors, or SLSsteam acts unexpectedly:
1. Open **ASSella → Settings → Health**.
2. Run the **Config Fixer** / validation check to automatically repair structure, indentation, and missing sections.
3. You can also revert to an automatic timestamped backup at any time via **Settings → Tools → Restore Config Backup**.
:::

### Configuration File Layout

A standard SLSsteam configuration file with games, decryption keys, and online fixes looks like this:

```yaml
# ~/.config/SLSsteam/config.yaml

# Master switches
Plugins: yes
SmartTickets: 0x1
LogLevels: 0x2
API: yes

# Unlocked App IDs
AdditionalApps:
  - 1245620  # Base Game (e.g. ELDEN RING)
  - 2778580  # DLC AppID (Shadow of the Erdtree)

# Downloadable depot IDs (Parent AppIDs must NEVER be in this list!)
AdditionalDepots:
  - 1245621
  - 1245624
  - 2778581

# 64-character depot decryption keys
DecryptionKeys:
  1245621: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
  2778581: fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210

# Online fix spoofing
FakeAppIds:
  1245620: 480  # ELDEN RING -> Spacewar

# Runtime environment overrides
LaunchOptions:
  1245620: "env LD_AUDIT=\"/home/deck/.config/SLSsteam/tools/netsock/netsock.so\" %command%"
```

### Strict Configuration Rules & Invariants

1. **`AdditionalDepots` vs `AdditionalApps`**:
   - Parent / Base AppIDs must **NEVER** be placed under `AdditionalDepots`.
   - Only content depot IDs and build download IDs belong in `AdditionalDepots`.
   - Placing a base AppID into `AdditionalDepots` causes Steam client manifest resolution loops.
2. **`DecryptionKeys` Format**:
   - Decryption keys must be valid 64-character hexadecimal strings.
   - Do not wrap hex keys in quotes or add trailing spaces.
3. **DLC-Only Mode Guard**:
   - For DLC-only games, **never** add the base game AppID to `AdditionalApps`. Only the DLC AppIDs should be listed.
4. **YAML Indentation**:
   - SLSsteam uses standard 2-space indentation. Never use tabs.

---

## 7. Troubleshooting & FAQ

### "Failed to connect to lobby" or "No games found"
- **AppID / Online Fix Compatibility**: Most of the time, ASSella's online spoofing allows you to play directly with players who are using community multiplayer fixes (such as those from Online-Fix.me or similar). To ensure multiplayer lobbies appear, check with your friends which AppID their fix is directing to (most commonly `480` for Spacewar) and ensure your **FID** matches theirs. If they are on `480` and you have a different ID configured (or vice versa), Steam will separate your lobbies.

### Game crashes immediately upon launch with Netsock enabled
- The game likely features anti-cheat (EAC, BattlEye, or proprietary integrity checks) that blocks `LD_AUDIT`.
- Open **Game Details**, click **Netsock** to disable it, and restart Steam.

### "EOS Login Failed" after a game update
- The game update overwrote the patched `EOSSDK-Win64-Shipping.dll` with an unpatched binary.
- Open ASSella, open **Game Details**, and click the amber **Reapply Proxy** button.

### Flatpak Steam Compatibility
- ASSella automatically detects Flatpak Steam (`~/.var/app/com.valvesoftware.Steam`) and syncs `netsock.so` into the Flatpak sandbox environment so `LaunchOptions` resolve correctly across both host and container paths.
