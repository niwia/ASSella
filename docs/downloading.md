# Downloading Games

ASSella can put a game on your system in two ways. Both start the same way: find the game, pick what to download, confirm.

| | **ASSella way** | **AT0-M way** (Steam native) |
| :--- | :--- | :--- |
| Who downloads | ASSella's built-in downloader | The Steam client itself |
| Where progress shows | ASSella's progress bar | Steam's Downloads page |
| Updates | ASSella checks and updates (Update All) | Steam updates the game like any other |
| Needs | A Hubcap API key | A Hubcap API key, SLSsteam (Headcrab) and the AT0-M plugins |
| Best for | Pinning builds, rollbacks, full control | "Set and forget" games that behave like normal Steam games |

You can move a game between the two later from its [Game Details](/game-details) page (**Move to AT0-M** / **Move to ASSella**).

::: tip Already own the base game on Steam?
If you only want to download or unlock DLCs for a game you already own, see our [DLC-Only Mode Guide](/guides/dlc-only).
:::

::: tip Before you start
Finish the [Installation Guide](/install) first: your Hubcap key must be saved in **Settings → Integrations**. For the AT0-M way, also turn on **Settings → at0-m → Enable plugins** ([AT0-M setup](/at0m)).
:::

---

## 1. Find the game

Type a game name or a Steam **App ID** into the **Search games…** box in ASSella's title bar and pick the game from the list.

You can also start from files you already have, such as a `.lua`, `.manifest` or `.zip`. See [Bring Your Own .lua](/guides/import-lua).

## 2. Choose how to download

If AT0-M is enabled, ASSella asks how this game should be downloaded:

<img src="/images/guide/download-method.png" alt="Download method dialog" width="420" />

- **Steam** — the AT0-M way. Greyed out with *Plugins Missing* or *AT0-M not ready* when SLSsteam or the plugins are not set up yet.
- **ASSella** — the built-in downloader.
- **Remember my choice** skips this question next time. You can change it any time in **Settings → Downloads → Default Download Behavior** (*Always ask*, *Start Native (at0-m)*, *Start ASSella*).

Without AT0-M enabled, ASSella simply uses its own downloader and this step is skipped.

## 3. Pick the depots

![Depot selection](/images/guide/depot-selection.png)

A game is split into **depots** (the game files per platform, languages, soundtrack, extras). ASSella pre-selects what you need:

- **Linux: Game ▾** — presets for your platform. Native Linux depots are chosen when the game has them, otherwise Windows (played through Proton). Soundtracks, artbooks and dedicated servers are left out unless you tick them.
- **Branch** — `public` is the normal release; other branches (betas) appear when the game has them.
- **Build** — the build that will be downloaded.
- **Browse Files…** — look inside a depot before downloading.
- **Storage** (ASSella way) — the Steam library to install into, with its free space. Set a preferred library or *Ask which library every time* in **Settings → Downloads**.

Then press **Download** (ASSella way) or **Register to AT0-M** (AT0-M way).

## 4a. The ASSella way: download

ASSella downloads the selected depots, writes the Steam app manifest and shows progress in its main window. When it finishes, the game appears in your Steam library as installed — restart Steam if it was running.

Updates: ASSella checks your games and offers **Update All** on the main screen. Games can opt out per game (**Update-All: Exclude** on the Info tab) or be frozen with **Pin Build**.

## 4b. The AT0-M way: register with Steam

ASSella writes the game's apps, depots and decryption keys into SLSsteam's `config.yaml`, then hands the game to Steam. What happens next depends on **Settings → AT0-M → Start download with Steam**:

- **Start auto** — Steam starts downloading right away.
- **Add only** — the game is added to your library; press *Install* in Steam when you want.
- **Always ask** — ASSella asks each time.

Steam then downloads, updates and verifies the game itself, exactly like a game you own. If a game only ships Windows files, ASSella offers to force Proton for it.

Want to know what happens underneath, or what each AT0-M setting does? See **[AT0-M](/at0m)** and **[Plugins](/plugins)**.

::: tip Also from Game Mode and your browser
The same AT0-M library is shared with [Uran](/uran) (Decky plugin, Game Mode) and [Epsilon](/epsilon) (an *Add to at0-m* button on Steam Store and SteamDB pages).
:::

---

## Troubleshooting

::: details "Steam" is greyed out in the Download method dialog
AT0-M needs SLSsteam and the plugins. Open **Settings → AT0-M**, turn on **Enable plugins** and deploy the plugins, then check **Settings → Health** for anything marked red.
:::

::: details Some depots show as missing or "not on Hubcap"
Hubcap has no decryption key for that depot yet. Download the rest; later use **Refetch keys** (Depots tab, AT0-M) or **Refetch** (Info tab, ASSella) to pick up new keys.
:::

::: details The game does not appear in Steam
Restart Steam. For AT0-M games, Steam must have been started with SLSsteam active (see [Installation Guide](/install)).
:::
