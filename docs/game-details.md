# Game Details

Click any game in [Library](/library) to open its details. The header shows the game's art, size on disk, installed build and a **SteamDB** shortcut. The tabs below change with the game: AT0-M games get a **Depots** tab, games with downloaded mods get **Workshop**.

---

## Info

![Info tab (ASSella-managed game)](/images/guide/details-info-assella.png)

- **Branch & build** (dropdown) — which branch the game follows and its current build. Switching branch downloads that branch's build.
- **Main action button** — changes with the game's state:
  - **Update** when a newer build exists,
  - **Verify Files** to check the installed files,
  - **Refetch** when the game's manifest or key data is missing,
  - **Verify Pinned Build** while the build is pinned.
- **Open folder** — opens the install folder (and shows which library it is in).
- **Pin Build** — freezes the game on its current build: Update All and update checks leave it alone. [How pinning works](/guides/pin-build).
- **Update-All** — include or exclude this game from the main screen's **Update All**.
- **Uninstall** — *Standard* removes the game; *Advanced* lets you choose what else goes. [Uninstall guide](/guides/uninstall).
- **Online**
  - **SLSonline** — gives the game a Fake AppID (480, *Spacewar*) so Steam multiplayer and lobbies work with friends.
  - **Netsock** — Steam Networking Sockets fix for games that disconnect under Proton. Needs SLSonline.
  - **Bypass** — skips a third-party launcher and starts the game's real executable. Cannot be on together with Netsock. [Launcher Bypass guide](/guides/launcher-bypass).
- **Managed by** — whether ASSella or Steam (AT0-M) updates the game, with **Move to AT0-M** / **Move to ASSella** to switch.

![Info tab (AT0-M game)](/images/guide/details-info-atom.png)

For AT0-M games Steam owns updates, so the branch picker, verification and Update-All are disabled.

---

## Depots (AT0-M games)

![Depots tab](/images/guide/details-depots.png)

What this game has in SLSsteam's config, in three lists:

- **AppIDs & licenses** (`AdditionalApps`) — the game and each DLC.
- **Depots registered** (`AdditionalDepots`) — the depots Steam may download.
- **AES decryption keys** (`DecryptionKeys`) — the keys that unlock them.

**View** is read-only. **Edit** turns the switches on — turning an app off also turns off its depots — then **Save** writes the changes. **Refetch keys** asks Hubcap again for keys that were missing, and offers DLC that became available. Depots shared with other games stay protected.

---

## Builds

The game's patch history from SteamDB. Pick an older build to see its depots and **Download Selected** to roll back, or **Manual** to enter a manifest by hand.

ASSella reads SteamDB directly. **Byparr** (a helper that gets past SteamDB's bot check) is **optional**: it's only used as a fallback, for example to load the full history. If the direct route is blocked on your network, the tab offers to install it. Step by step: [Downgrade a Game](/guides/downgrade).

---

## Tools

![Tools tab](/images/guide/details-tools.png)

- **DRM & emulation**
  - **Steamless** (Python or .NET) — removes the SteamStub DRM wrapper from the game's executable.
  - **Apply / Remove Goldberg** — swaps in the Goldberg Steam emulator for offline play.
- **Depots & installation**
  - **Depots: Select** — choose which depots this game keeps (**Reset** returns to the default).
  - **Fix Installation** — repairs the install and makes Steam verify it.
  - **Move Storage…** — moves the game and its manifest to another Steam library.
- **Utility & store links** — **Open Store**, **Open SteamDB**, **Copy App ID**, **Copy Path**.
- **Move DLC to DlcData (Advanced)** — experimental: keeps DLC content in a separate folder. Use only when a guide tells you to.

---

## Tickets

![Tickets tab](/images/guide/details-tickets.png)

SLSsteam ownership tickets for this game. Drag a ticket `.yaml` onto the box, browse to one, or paste its base64 text. **Verify Ticket Status** checks it; **Delete Ticket** removes it.

---

## Workshop

::: warning Retired
The old Workshop downloader no longer works reliably and is switched off by default. Use [AT0-M](/at0m) instead: subscribe to mods in Steam and Steam downloads and updates them for you.
:::

If you turn the old downloader back on (**Settings → Advanced**), this tab lists the game's installed mods and updates outdated ones.
