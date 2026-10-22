# Steam Stuck on Loading Screen in Game Mode? (TTY Recovery Guide)

If your Steam keeps restarting or never makes it into Game Mode, and you've been using SLSsteam, the odds are good that SLSsteam is what broke it. It happens, it's fixable, and you won't lose your games.

You just can't fix it from Game Mode, because Game Mode is the thing that's stuck. We'll use a plain text terminal (called a **TTY**) instead, and it's easier than it sounds.

---

## Step 1: Get a Keyboard

You'll need to type a few commands, so plug in a USB keyboard.

::: tip SSH Alternative
You can also **SSH** into the Steam Deck or PC from another computer or mobile device if you configured SSH beforehand. If connected via SSH, you can skip Step 2 and jump straight to [Step 3](#step-3-try-the-simple-fix-first).
:::

---

## Step 2: Open the TTY

1. With the keyboard connected, press <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>F3</kbd> simultaneously.
2. The screen should switch to a black screen with a login prompt.
3. If it stays blank or nothing happens, wait a few seconds and try <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>F4</kbd> instead.
   - You can try keys up to <kbd>F10</kbd> (<kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>F3</kbd> through <kbd>F10</kbd>) as the designated TTY shortcut can vary between Linux distributions and kernels.
   - You can always exit TTY mode and return to the graphical session / Game Mode by pressing <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>F1</kbd> or <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>F2</kbd>.

4. **Log in to your account**:
   - Username: on a Steam Deck, that is usually `deck`.
   - Password: enter your user password.

::: warning Invisible Password Entry
Don't be alarmed when nothing shows up as you type your password. Linux hides it completely (not even asterisks or dots appear), but it is actively registering your keys. Just type your password and press <kbd>Enter</kbd>.
:::

::: details Never set a sudo/deck password?
If you never set a password, you will need one before you can log in to a TTY. If you can access desktop mode or a terminal terminal window, run `passwd` to create one.
:::

---

## Step 3: Try the Simple Fix First

This completely removes the SLSsteam runtime folder so Steam can boot vanilla without hooks:

```bash
rm -rf ~/.local/share/SLSsteam
```

::: tip No News is Good News
Copy and run the command exactly as written. Nothing will print when it succeeds. What this does is remove the SLSsteam runtime directory from your system so Steam will boot normally.
:::

Then restart the device:

```bash
sudo reboot
```

It will ask for your password again. Give it a minute or two after rebooting, and Steam should load into Game Mode normally.

---

## Step 4: Still Stuck? Run the Reset Script

If Steam is still stuck on the loading screen after Step 3, or if you also installed Headcrab, go back to the TTY ([Step 2](#step-2-open-the-tty)) and run the vanilla reset script:

```bash
curl -fsSL "https://runn.i.ng/Deadboy666/h3adcr-b-modul3s/blob/main/reset2vanilla.sh" | bash
```

### What this script does:
- This script does not touch the `SLSsteam` folder. Instead, it undoes the changes Headcrab made to how Steam launches, which is a different cause of the same symptom.
- You may see a prompt asking about removing **Millennium**. It will usually skip past it on its own when piped into `bash`. If you also have Millennium installed, let us know on Discord and we will walk you through removing it separately.
- On **Bazzite**, the script also cleans up legacy files and asks for your password.

Then reboot again:

```bash
sudo reboot
```

---

## What to Expect After Fixing It

- **First launch can be slow**: Steam may need to re-download or repair a few of its own genuine files. Ensure you are connected to Wi-Fi and give it a few minutes before assuming it is stuck again.
- **Your games and saves are 100% safe**: These recovery steps only touch launcher injection wrappers and hooks; your installed games, Steam library, and save files are completely unaffected.
- **Reinstalling SLSsteam**: If you want SLSsteam back afterwards, wait until it has been updated or ask in the community channel before reinstalling, so you don't land right back in the same boot loop.
