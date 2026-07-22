# ASSella v2.3.4 Release Notes

## Architectural & Verification Enhancements
- Stage 1 and Stage 2 Manifest Verification: Integrated background auto-fetching with automated Hubcap status checks and extracted zip manifest ID comparisons against live Steam build data.
- Refined Timestamp Verification: Added optional UTC timestamp verification to prevent downloading stale manifests when new Steam builds release.
- Build ID Tracking: Added Build ID resolution across local ACF files, Steam PICS data, and downloaded manifest zip packages.

## Game Details & UI Redesign
- Installed Build ID Display: Added direct display of the installed Build ID in the Game Details stats grid.
- Build-Based Backup Zip Naming: Renamed and formatted manifest backups as `Build: <buildid>`.
- Dropdown Build Color Coding: Highlighted latest build entries in vibrant green and historical backup builds in muted orange.
- Dynamic Action Buttons: Action button automatically updates to "Verify & Repair", "Downgrade", or "Download Update" based on the selected dropdown build.
- Integrated Open Folder Button: Embedded a styled Open Install Folder button directly into the stats grid.

## System Reliability & Bug Fixes
- Fixed per-game update checking error handling and improved offline manifest validation.
- Enhanced SLSsteam token configuration synchronization for multi-library setups.
