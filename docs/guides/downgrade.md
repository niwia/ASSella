# Downgrade a Game

Sometimes a game update breaks mods, saves or performance, and you want an older version back. This is called a **rollback** or **downgrade**. ASSella gives you three ways to do it.

::: tip Words you'll see
- **Build ID:** the version number of the whole game (e.g. `23935869`).
- **Depot:** one part of the game (game files, a language, a soundtrack…).
- **Manifest ID:** the exact version of one depot (e.g. `4622465672933170056`). A build is a set of manifests, one per depot.
:::

---

## Way 1: Builds tab (easiest)

1. Open the game ([Game Details](/game-details)), then the **Builds** tab.
2. ASSella loads the game's patch history from SteamDB. It reads SteamDB directly; **Byparr** (a helper that gets past SteamDB's bot check) is only an optional fallback, and the tab offers to install it if the direct route is blocked.
3. Click the build you want. You'll see the depots in that build, with the installed ones ticked.
4. Press **Download Selected**.

ASSella fetches those exact manifests from Hubcap and downloads that version. The build is **pinned** automatically, so the next update check doesn't undo your rollback (see [Pin a Build](/guides/pin-build)).

---

## Way 2: Manual rollback (from SteamDB yourself)

Use this when the Builds tab can't load, or you already know the numbers.

![Manual Rollback](/images/guide/manual-rollback.png)

**Finding the numbers on SteamDB:**

1. Go to `steamdb.info/app/<AppID>/patchnotes/` and find the update you want to go back to. Note its **Build ID**.
2. Open the game's **Depots** page (`steamdb.info/app/<AppID>/depots/`) and click the depot you want (usually the main game depot).
3. Open its **Manifests** tab. It lists every version of that depot with a date. Pick the one from your chosen build's date and copy its **Manifest ID**.

**In ASSella:**

1. **Builds** tab → **Manual** (or **Manual Rollback / Manifest**).
2. Pick the **Target Depot** and enter the **Target Build ID** and **Target Manifest ID**.
3. Leave **Pin build** ticked so ASSella doesn't update the game again.
4. Press **Start Rollback**.

Repeat for other depots if the game has more than one that changed.

---

## Way 3: Import a file (.zip / .lua / .manifest)

If someone gave you the files for a specific version, or you saved them earlier:

1. Drag the **`.zip`**, **`.lua`** or **`.manifest`** file (or a folder of them) onto ASSella's main window.
2. ASSella reads it and shows the game, depots and build it contains.
3. Confirm, then download as usual.

Hubcap bundles ASSella already downloaded are kept in `~/.local/share/ACCELA/hubcap_manifests/`.

---

## Downgrading AT0-M games

AT0-M games are downloaded by Steam, so a rollback works a bit differently. You use the same Builds tab or Manual Rollback dialog, but underneath:

1. ASSella puts the old **manifest files** into Steam's `depotcache` folder.
2. It writes those manifest IDs into the **`ManifestIds`** section of SLSsteam's `config.yaml`. SLSsteam then makes Steam ask for *that* version instead of the newest one.
3. Steam downloads the old version.

Those `ManifestIds` entries also keep Steam on that version: Steam won't update those depots until you remove the pin. Turning **Pin Build** off on the Info tab removes them, and Steam updates the game to the newest version again.

::: warning
Your save files are not rolled back. Saves made by a newer version sometimes don't load in an older one, so back them up first.
:::
