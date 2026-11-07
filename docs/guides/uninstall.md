# Uninstall a Game

Open the game ([Game Details](/game-details)) → **Info** tab → **Uninstall**. You get two choices: **Standard** and **Advanced**.

## Standard uninstall

The quick, safe option. It removes:

- the **game folder**,
- Steam's **app manifest** (`appmanifest_<appid>.acf`), so Steam forgets the install,
- the game's entries in SLSsteam's **config** (apps, depots, keys, Fake AppID, launch options, tokens). This is skipped for depots other games share.
- for AT0-M games, its entry in the AT0-M library.

It **keeps** your Proton prefix (Windows saves) and Steam Cloud saves, so a reinstall later picks up where you left off.

For a **DLC-only** game, Standard removes just the DLC it added and leaves the base game alone.

## Advanced uninstall

![Advanced uninstall](/images/guide/uninstall-advanced.png)

Choose exactly what goes:

| Option | What it does |
| :--- | :--- |
| **I bought the game** | Keeps the game files, saves and prefix. It only removes ASSella's SLSsteam integration (config entries, tokens, EOS proxy, Goldberg), so the game runs as a normal Steam purchase. Use this after buying a game you'd been playing. |
| **Remove Proton prefix (has Windows saves)** | Also deletes `compatdata/<appid>`: the game's Windows environment, including save games stored in *Documents* / *AppData*. |
| **Remove local cloud saves** | Also deletes your local copy of the game's Steam Cloud saves in `userdata`. |

Then **Confirm Uninstall**. ASSella lists exactly what will be deleted before anything happens.

## ASSella way vs AT0-M way

- **ASSella games:** ASSella deletes the files itself.
- **AT0-M games:** ASSella removes the files and the config entries. Steam then sees the game as uninstalled and doesn't download it again.

::: warning Saves
Deleted saves can't be restored. If you might come back to a game, leave the prefix and cloud saves options unticked.
:::
