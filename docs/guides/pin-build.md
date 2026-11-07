# Pin a Build

**Pinning** freezes a game on the version you have now. Use it when an update would break your mods, or after you [downgraded](/guides/downgrade) a game.

Turn it on with **Pin Build** on the game's [Info tab](/game-details#info). The tile shows **Active** while the game is pinned. A rollback pins the game for you.

## ASSella-managed games

- ASSella treats the game as **up to date**, whatever new builds exist.
- It's taken out of **Update All** (the Update-All tile switches to *Exclude* and locks).
- ASSella keeps a copy of the current version's manifest bundle (`accela_fetch_<appid>_build_<build>.zip`), so it can **Verify Pinned Build** later, even after newer versions come out.

Turn **Pin Build** off to let updates through again. The game rejoins Update All.

## AT0-M games

Steam updates AT0-M games itself, so pinning has to stop *Steam*:

- ASSella writes the game's current depot versions into the **`ManifestIds`** section of SLSsteam's `config.yaml`.
- SLSsteam then makes Steam ask for exactly those versions, so Steam has nothing new to download.

Turn **Pin Build** off to remove those `ManifestIds` lines. Steam then updates the game normally on its next check.

::: tip DLC-only games
Pinning a game where you only add DLC is rarely needed and can stop DLC tracking. ASSella asks before it does it.
:::

::: tip Moving a pinned game
When you move a pinned game to AT0-M, ASSella carries the pin over as `ManifestIds`, so the game stays on its version. **Update All**'s "move to AT0-M" batch skips pinned games on purpose.
:::
