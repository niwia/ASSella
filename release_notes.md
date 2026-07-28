# ASSella v2.5.3 Prerelease Changelog

## New Feature: Import Mode (User-Provided LUA/Manifest Files)
- Added support for bringing your own LUA and/or manifest zip files for games not already in your library.
- A new "Scan for LUA" button has been added in the Game Library to automatically detect unregistered LUA files in the cached_luas folder.
- **Under-the-Hood Workflow**:
  - **Scanning**: Scans cached_luas for files not registered in the depot keys database. Parses the AppID, game name, app tokens, depot keys, and GIDs.
  - **Replication**: Injects the decrypted depot keys and app tokens directly into the local depot_keys.db SQLite database.
  - **Staleness Check**: If the manifest zip counterpart is missing from the hubcap_manifests folder, ASSella runs an offline verification check. It compares the manifest GIDs inside your LUA file against live Steam PICS metadata (queried anonymously and free of charge, saving your Hubcap API usage).
  - **Fallback Routes**:
    - **LUA is Fresh**: Automatically calls the /generate/appmanifest endpoint to fetch only the manifest files (cheaper API usage) and bundles them into the required zip format.
    - **LUA is Stale / Untracked**: Falls back to the /manifest/{appid} endpoint to fetch the full zip containing the latest LUA and manifest files.
    - **LUA + Zip Both Exist**: Processes the local files directly with zero API cost.
- **Experimental Notice**: The user-provided LUA import flow is highly experimental. Please report any parsing, database injection, or manifest generation errors.

## ASShead Configuration Fixer Improvements
- Optimized the SLSsteam config.yaml formatting, cleanup, and merging.
- Dynamically retrieves default configurations from the upstream repository and migrates user configurations cleanly without manual keys updates.
- Hardened outbound network calls to fetch template defaults gracefully with a 15-second timeout, avoiding deadlocks or startup hangs on slow networks.

## Packaging & Update Changes
- Deprecated ZSync delta updates. From this build forward, self-updates will perform full AppImage download updates directly, eliminating zsync check overhead and packaging complexity.
- Removed appimageupdatetool delta checks on self-update and packaging scripts.
- Emojis have been removed from the Select and Scan button labels in the Game Library UI.
