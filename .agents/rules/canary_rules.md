# Canary Development Rules

- **Branch**: All plugin, Vapor, and experimental Steam native downloader/workshop R&D must strictly take place on the `canary` branch.
- **Build Target**: Builds made on or for the canary branch must strictly output to `/home/aiwin/.local/share/ACCELA/assella3.0canary.appimage`.
- **Preservation**: Do NOT modify or overwrite `/home/aiwin/.local/share/ACCELA/ASSella.AppImage` or `ASSella.AppImage.dev` during canary development.
