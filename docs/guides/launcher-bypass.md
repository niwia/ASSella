# Launcher Bypass

Some games start a separate **launcher** first (a publisher's own launcher, a "Play" window, an account login). Those launchers often break under SLSsteam, or simply get in the way. **Launcher Bypass** makes Steam start the game's real program directly and skip the launcher.

::: danger This needs a crack
Skipping the launcher only works if the game itself runs without it. Many launchers handle DRM or online logins, so the game's own `.exe` refuses to start without them. In that case, the bypass only works if the game's executable is **cracked** (patched to run without its launcher/DRM). ASSella doesn't provide cracks. Bypassing DRM is illegal in many countries, so make sure you know the rules where you live.
:::

## How to set it up

1. Open the game ([Game Details](/game-details)) → **Info** tab.
2. Under **Online**, click **Bypass**.
3. Pick the real game executable. **Root Executables** (in the game's main folder) are usually right; launchers often live in a subfolder.

   ![Launcher bypass picker](/images/guide/bypass-picker.png)

4. Press **Select**, then start the game from Steam as usual.

To change or remove it, click **Bypass** again: pick another file, or **Clear Bypass**.

## What happens underneath

ASSella adds a **launch option** for the game to SLSsteam's `config.yaml`. When Steam starts the game, that launch option swaps the launcher for the program you picked. It works the same for Proton (Windows) games and native ones. Nothing in the game folder changes, so clearing the bypass fully undoes it.

## Things to know

- **Bypass and Netsock can't be on together.** Both use the game's launch options; turn one off before using the other.
- Works for both ASSella and AT0-M games.
- If the game shows a black screen or closes right away, you probably picked the wrong file, or the game needs its launcher (see the warning above). Try another executable or clear the bypass.
