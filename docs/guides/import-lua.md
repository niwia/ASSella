# Bring Your Own .lua

Already have the files for a game? You can install it from them, without searching. ASSella understands three kinds of files:

| File | What's inside |
| :--- | :--- |
| **`.lua`** | The game's **AppID**, its **depots** and their **decryption keys** (sometimes the manifest IDs too). This is the "recipe". |
| **`.manifest`** | The **file list** for one depot at one exact version. |
| **`.zip`** | A bundle with a `.lua` and/or `.manifest` files inside, like the ones Hubcap gives out. |

## What you need

- **.lua and .manifest files together (or a .zip with both):** everything is local. ASSella can install without asking Hubcap for anything.
- **Only a .lua:** ASSella has the keys, and fetches the matching manifests from **Hubcap**. You need a Hubcap key.
- **Only .manifest files:** ASSella has the file list but still needs the keys. It looks for them in its key cache and on Hubcap.

ASSella also checks whether your `.lua` is **outdated**, by comparing its manifest IDs with Steam's current ones, so you know if you'd be installing an old version.

---

## Way 1: Drag and drop

1. Drag the `.lua`, `.manifest` or `.zip` file onto ASSella's main window. You can also drag a folder with several of them.
2. ASSella reads it and shows the game, its depots and the build.
3. Continue like a normal download: pick the method (ASSella or [AT0-M](/at0m)), then the depots.

## Way 2: Scan for LUA (several games at once)

1. Copy your `.lua` files into ASSella's **cached_luas** folder: `~/.local/share/ACCELA/cached_luas/`.
2. Open **Library**, click the round **⋯ button** in the bottom-right corner, and choose **Scan for LUA**.
3. ASSella lists every `.lua` it found that isn't in your library yet:
   - **Ready (zip found):** the manifests are already there.
   - **Needs API fetch:** the manifests will be fetched from Hubcap.
4. Choose **Import Selected** or **Import All**.

Imported games are added to your library, and you download them from there like any other game. They're included in update checks too.

::: tip Where the files go
ASSella keeps `.lua` files in `cached_luas/`, manifests in `manifests/`, and Hubcap bundles in `hubcap_manifests/`, all under `~/.local/share/ACCELA/`.
:::

::: details "No new .lua files found"
The `.lua` files must be directly inside `cached_luas/`, and named after the AppID (for example `1245620.lua`). Games already in your library are skipped.
:::
