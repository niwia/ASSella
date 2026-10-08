# Canary Development Rules

- **Branch**: All plugin, Vapor, and experimental Steam native downloader/workshop R&D must strictly take place on the `canary` branch.
- **Build Target**: Builds made on or for the canary branch must strictly output to `/home/aiwin/.local/share/ACCELA/assella3.0canary.appimage`.
- **Preservation**: Do NOT modify or overwrite `/home/aiwin/.local/share/ACCELA/ASSella.AppImage` or `ASSella.AppImage.dev` during canary development.
- **Config Invariants (AT0-M & Plugin Mode)**:
  - `DecryptionKeys`: If an AppID has a decryption key (root AppKey) or depot key, it MUST be added to `DecryptionKeys` 100% of the time when adding the game. Never refuse or sanitize valid 64-hex keys in `DecryptionKeys`. Users have full freedom to toggle keys on/off in the Depots tab.
  - `AdditionalDepots`: Parent/Base AppIDs must NEVER be added to `AdditionalDepots` under any circumstances (only content depot IDs and build download IDs belong here).
  - `AdditionalApps`: Normal games must have their base AppID added; DLC-only games must have only their DLC AppIDs added (never base AppID).

