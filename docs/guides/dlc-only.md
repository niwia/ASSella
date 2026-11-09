# DLC-Only Mode

**DLC-Only Mode** is designed for games where you already own the base game legitimately on Steam (or have it installed through Steam), and you only want to add, download, or unlock DLCs—without re-downloading the base game or disturbing your existing Steam installation.

ASSella lets you manage DLCs in two ways: through **AT0-M** (Steam native) or through **ASSella** (the built-in downloader).

---

## At a Glance: Two Ways to Handle DLCs

| Feature | **AT0-M Mode** (Steam Native) | **ASSella Mode** (Built-in Downloader) |
| :--- | :--- | :--- |
| **Who downloads files** | Steam client directly | ASSella's internal downloader |
| **Depot-less (License-only) DLCs** | Instant activation (0 KB download) | Activated in config (0 KB download) |
| **Depot-backed DLCs** | Steam downloads and updates the DLC content | ASSella downloads DLC files to the game directory |
| **Base game manifest (`.acf`)** | Untouched; managed entirely by Steam | Untouched; managed entirely by Steam |
| **Best for** | Most games; automatic Steam integration | Modded games, offline setups, or specific builds |

::: tip Configurable Default
You can choose how ASSella handles DLC downloads by default under **Settings → at0-m → General → DLC-Only Download behaviour**:
- **Start Native (at0-m)**: Automatically registers DLCs to Steam.
- **Start ASSella**: Automatically downloads through ASSella.
- **Always ask**: Prompts you to choose each time.
:::

---

## Understanding DLC Types: Depot-Backed vs. Depot-Less

DLCs on Steam fall into two distinct technical categories:

### 1. Depot-Backed DLCs (Real Files to Download)
These are expansions, new maps, high-resolution textures, or separate campaigns (e.g. *Elden Ring: Shadow of the Erdtree* or *Cyberpunk 2077: Phantom Liberty*). They contain dedicated Steam depots with downloadable files and require AES decryption keys.
- **In AT0-M mode**: Steam downloads these files from Steam's CDN once registered.
- **In ASSella mode**: ASSella downloads the files directly into the game folder.

### 2. Depot-Less / License-Only DLCs (Instant Unlock)
Many modern games—especially fighting games, RPGs, sports games, and live-service titles—include all DLC assets (characters, costumes, weapons, soundtrack files) in the regular base game updates. The DLC itself is merely a license token (0 KB or a small stub depot).
- **No download is needed!** Once the entitlement is written to SLSsteam, the game engine checks Steamworks (`ISteamApps::BIsSubscribedApp`), verifies the license, and immediately unlocks the content upon game launch.

---

## The 64+ DLC Limit and `DlcData`

Steam and Steamworks have an internal architecture where passing too many DLC licenses (typically **64 or more**) through standard app license tables (`AdditionalApps`) can hit a hard cutoff or cause game launch crashes. Games with vast catalogs of cosmetics or songs (such as *The Sims 4*, *Dead or Alive*, *Payday 2*, *Rocksmith*, or *Train Simulator*) easily exceed this limit.

To solve this, SLSsteam provides a dedicated **`DlcData`** section in `config.yaml`.

### What `DlcData` Does
Instead of exposing dozens or hundreds of DLC AppIDs globally in Steam's general app list, `DlcData` scopes the DLCs directly under the parent game's AppID:

```yaml
DlcData:
  1234560:
    1234561: "Costume Pack 1"
    1234562: "Costume Pack 2"
    # ... hundreds of DLCs bypass the limit safely
```

SLSsteam hooks the Steamworks API calls (e.g. `BIsSubscribedApp` and `GetDLCDataByIndex`) to directly report every entry in `DlcData` as owned and installed for that game.

### How ASSella Handles the 64 DLC Threshold
ASSella automates this routing seamlessly:
- **Fewer than 64 DLCs (< 64)**: Written to `AdditionalApps`.
- **64 or More DLCs (≥ 64)**: Automatically routed to `DlcData` to bypass Steam's limit.

#### The High DLC Prompt Dialog
When ASSella detects an owned game with 64 or more depot-less DLCs, it displays the **High DLC Count Detected** prompt with two options:

1. **Add All to DlcData (Recommended)**: Automatically writes all DLCs into `DlcData` in a single click, instantly unlocking them without hitting Steam's 64 DLC ceiling.
2. **Manual Selection**: Opens a searchable checklist where you can cherry-pick specific DLCs. A dynamic status badge shows whether your current selection will be saved under `AdditionalApps` (< 64) or `DlcData` (≥ 64).

#### Manual Utility in Game Details
If an individual game fails to recognize unlocked DLCs or you want to manually adjust the storage mode, go to **[Game Details](/game-details) → Tools tab → Experimental DLC Tools**:
- Click **Move DLC to DlcData (Advanced)** to scan all discovered DLCs and store them under `DlcData`.
- Click **Revert DLC from DlcData (Advanced)** to clear them if no longer needed.

---

## How to Use DLC-Only Mode in AT0-M (Steam Native)

1. **Search for the game** in ASSella's search bar, or select your owned game from the **Library** tab.
2. If prompted, select **Steam (AT0-M)** as the download method.
3. Depending on the DLC types found:
   - **If depot-backed DLCs exist**: The **AT0-M DLC Setup** dialog opens. Base game depots and already-owned DLCs are greyed out. Unowned DLC depots are pre-selected. Verify your selection and click **Save**.
   - **If only depot-less DLCs exist**: ASSella informs you that no file download is required. If the game has ≥ 64 DLCs, the High DLC prompt lets you choose **Add All to DlcData** or **Manual Selection**.
4. ASSella writes the DLC AppIDs and keys into `config.yaml` and updates the internal database.
5. If Steam was running, restart Steam or launch the game directly. For depot-backed DLCs, Steam downloads the content files; for depot-less DLCs, the content is unlocked immediately in-game.

---

## How to Use DLC-Only Mode in ASSella (Built-in Downloader)

1. Find the game in ASSella and choose **ASSella** in the Download Method dialog.
2. The **Depot Selection** dialog opens with DLC-only mode active:
   - Main game files and engine depots are hidden/locked.
   - Only downloadable DLC content depots are listed.
3. Tick the DLCs and language depots you want to install.
4. Choose the Steam library folder where your base game is installed.
5. Click **Download**. ASSella downloads the files directly into your game's directory without touching Steam's base game `.acf` manifest.

---

## Invariants & Safety Under the Hood

To prevent corrupting your legitimate Steam library or breaking Steam's native functionality, ASSella enforces strict safety invariants in DLC-only mode:

1. **Base AppID is NEVER in `AdditionalApps` or `AdditionalDepots`**:
   Because you already own the base game on Steam, adding the base AppID to SLSsteam's emulation tables would interfere with your legitimate cloud saves, playtime tracking, and family sharing. ASSella strictly strips the parent AppID from these sections in DLC-only mode.
2. **No Base `.acf` Overwriting**:
   ASSella never replaces or deletes Steam's `.acf` manifest for the base game.
3. **Safe Uninstall**:
   Uninstalling a DLC-only game from ASSella only removes the injected DLC files and config entries—it will **never** delete your base game installation or save files.

---

## Troubleshooting

::: details Unlocked DLCs do not appear in-game
1. **Restart Steam**: Steam only re-reads plugin tickets and game entitlements when the game launches.
2. **Check for the 64 DLC limit**: If the game has dozens of DLCs, open **[Game Details](/game-details) → Tools** and click **Move DLC to DlcData (Advanced)**.
3. **Check AT0-M Plugins**: Ensure **SpaceBunny** and **Styx** are installed and up to date in **Settings → at0-m → Manage plugins**.
:::

::: details Steam shows "Update Queued" or "Depot Without Key"
This happens if a depot was registered without a valid AES decryption key. Open **[Game Details](/game-details) → Depots tab** and click **Refetch keys** to pull the latest keys from Hubcap, or uncheck the unkeyed depot under the **Edit** mode.
:::

::: details Switching between ASSella and AT0-M for DLCs
On the game's **[Game Details](/game-details) → Info tab**, use **Move to AT0-M** or **Move to ASSella**. ASSella will perform a preflight review and safely adjust the configuration without harming your base game files.
:::
