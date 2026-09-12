
```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

## 📋 Requirements
* **Headcrab (SLSsteam)**: Required to intercept and download depots:
  `curl -fsSL headcrab.pages.dev | bash`
* **.NET 9 Runtime**: Automatically installed if missing, required for Steamless and DepotDownloader tools.
* **Hubcap API Key**: Required to query manifest histories.

---

## ❄️ NixOS Compatibility

NixOS does not use standard `/lib64/ld-linux-x86-64.so.2` dynamic linkers, which causes generic Linux AppImages to display `stub-ld` or missing `libzstd.so.1` warnings. NixOS users can run ASSella using any of the following methods:

### 1. Using `steam-run` (Recommended — Includes libzstd.so.1 & Graphics Drivers)
```bash
nix-shell -p steam-run --run "steam-run ~/.local/share/ACCELA/ASSella.AppImage"
```

### 2. Using `appimage-run` with `zstd`
```bash
nix-shell -p appimage-run zstd --run "appimage-run ~/.local/share/ACCELA/ASSella.AppImage"
```

### 3. Global Fix (`nix-ld`)
Add `programs.nix-ld.enable = true;` and `zstd` to `/etc/nixos/configuration.nix` and rebuild.

---

*god is in the ass*
