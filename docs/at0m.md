# AT0-M (Steam native downloads)

**AT0-M** lets the real Steam client download your games. ASSella doesn't download anything itself here. Instead, it tells Steam (through **SLSsteam**) which games, depots and keys to use, and a few small **plugins** inside Steam do the rest.

To Steam, an AT0-M game looks like a game you own: it shows in your library, downloads from Steam's servers, and updates itself.

::: tip New here?
Start with [Downloading Games](/downloading). This page explains the settings behind the **AT0-M way** and what happens under the hood.
:::

## What you need

1. **SLSsteam (Headcrab)** installed and Steam started through it. See the [Installation Guide](/install).
2. A **Hubcap API key** saved in **Settings → Integrations**.
3. The **plugins** turned on and installed (below).

If something is missing, **Settings → Health** shows it in red, and the Download Method dialog greys out **Steam**.

---

## The AT0-M settings tab

Open **Settings → at0-m**. It has two sub-tabs.

### General

![AT0-M General tab](/images/guide/atom-general.png)

| Option | What it does |
| :--- | :--- |
| **Enable plugins** | The master switch. Installs the plugins and sets `Plugins: yes` in SLSsteam's `config.yaml`. Turn this on first. |
| **Start download with Steam** | What Steam does after a game is registered. **Start auto**: starts downloading straight away. **Add only**: only adds it to your library, and you press *Install* yourself. **Always ask**: asks every time. |
| **DLC-Only Download behaviour** | For games where you own the base game and only add DLC: download the DLC through Steam (**Start Native**), through ASSella (**Start ASSella**), or **Always ask**. See the [DLC-Only Mode guide](/guides/dlc-only). |
| **Manage plugins** | One button per plugin. **Green** = installed and up to date. Click to check for updates, or to turn an optional plugin off. **Grey** = not installed (click to install) or not available yet. See [Plugins](/plugins). |
| **Extras** | Optional plugins such as Lettuce. |
| **I'm using custom plugins (Advanced)** | Only for people who run their own plugins. ASSella stops checking for its plugins. You're on your own with this one. |

If ASSella finds plugins in your SLSsteam folder that it doesn't publish, a **Review** button appears. Unknown plugins can clash with SpaceBunny and Styx.

### Advanced

![AT0-M Advanced tab](/images/guide/atom-advanced.png)

- **SpaceBunny MRC provider:** where SpaceBunny asks for the *manifest request code* (see below). **Primary Authority** is asked first; **Secondary Rescue** is asked if the first one fails. *None* turns the rescue off.
- **Styx:** turns ownership-ticket splicing on, sets the mode (**Insert** is recommended) and the **Fallback AppID** (480).
- **Lobster:** turns config recovery on, and whether to show a desktop notification when it repairs something.

![AT0-M Advanced tab, continued](/images/guide/atom-advanced-2.png)

- **Cloudflare Worker:** the backup route used when your internet provider blocks a site. **Proxy Mode** chooses **Fallback** (try direct first; recommended), **Always** (always use the worker) or **Never** (direct only).
- **Save Changes** writes everything to `~/.config/SLSsteam/katamari.conf.lua`. **Restore Defaults** puts the defaults back.

---

## How an AT0-M download works

What happens when you press **Register to AT0-M**:

1. **ASSella writes the game into SLSsteam's `config.yaml`:**
   - the game and its DLC (`AdditionalApps`),
   - the depots Steam may download (`AdditionalDepots`),
   - the keys that unlock them (`DecryptionKeys`).

   SLSsteam picks the change up at once, without restarting Steam.
2. **Steam starts the download** (or waits for you, if you chose *Add only*). Before Steam can download any depot it needs two things:
   - **the decryption key:** SpaceBunny hands Steam the key from your config.
   - **a manifest request code (MRC):** a short-lived code that lets Steam fetch the depot's file list. SpaceBunny fetches it for Steam.
3. **Steam downloads, installs and later updates the game** on its own, just like a game you own.

### Fallbacks: what happens when something fails

SpaceBunny tries its options in a fixed order, so a single dead website doesn't stop your download:

1. **Direct:** ask the **Primary Authority**, then the **Secondary Rescue** provider over a normal connection.
2. **Lettuce, once:** if all of those failed (for example, your internet provider blocks them), Lettuce retries each provider **one time** through your proxy, or through the Cloudflare worker if you have no proxy.
3. **Back off:** if everything failed, SpaceBunny waits a little before trying that depot again, instead of hammering the servers.

Answers are checked before use: anything that isn't a valid code (8–20 digits) is thrown away rather than handed to Steam.

**Lobster** watches your `config.yaml` in the background. If an SLSsteam update strips the depots or keys out of it, Lobster puts them back.

---

## Plugins and updates

- **Install or update:** **Settings → at0-m → Manage plugins**, then click a plugin. ASSella downloads it from its signed plugin list, checks the signature, and copies it into `~/.config/SLSsteam/plugins/`.
- **Required plugins** (SpaceBunny, Styx, Lobster) can't be turned off while AT0-M is on.
- **Optional plugins** (Lettuce) can be turned off. Clicking a green optional plugin asks whether to turn it off or check for an update.
- **After an update**, restart Steam so it loads the new plugin version.

What each plugin does: [Plugins](/plugins).

## Moving games between modes

On a game's [Info tab](/game-details#info), **Move to AT0-M** hands an ASSella-downloaded game to Steam, and **Move to ASSella** does the reverse. If the game has a pinned build, [DLC-only mode](/guides/dlc-only), an EOS proxy or Goldberg applied, ASSella shows what it will do about each one before moving.

## Uran and Epsilon use the same setup

[Uran](/uran) (Game Mode) and [Epsilon](/epsilon) (your browser) register games exactly like ASSella does. They write to the same `config.yaml`, rely on the same [plugins](/plugins), and share the same Hubcap key.
