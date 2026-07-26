# ASSella v2.5.1 Prerelease Changelog

## Material You UI Overhaul (Beta)
- Restyled the Game Library and Fetch Manifest lists into modern elevated Material Design 3 card components.
- Introduced capsule/pill styling for search bars, sort comboboxes, and filter buttons.
- Implemented an immersive Spotify-style fading thumbnail visual effect that smoothly blends cover art headers into dark card containers.
- Replaced standard status text with rounded Suggestion Chips/Pills for update availability.
- Note: The Game Library interface still needs a bit more tuning for UI alignments in future updates.

## Manual Depot Downloading Option (Beta)
- Added a manual depot-buildid-manifest selection and verification feature before triggering downloads.
- This allows custom, targeted downloads of specific historical manifests and build configurations.
- Note: Currently, this manual selection option is only supported for games that are already owned/installed. Support for unowned or pre-download games is planned for future releases.

## Game Details Performance & Caching Fixes
- Bypassed cache expiration restrictions on details dialog initialization to guarantee instant rendering of branch configurations and dropdown menus.
- Implemented a local fallback path that loads installed configuration build IDs synchronously if there is no pre-existing SQLite database cache.

## Bug Fixes & Stability
- Set the default application font to Roboto, falling back safely to bundled fonts if Roboto is not available on the user's system to ensure stability.
- Set the default theme to Ocean Breeze (Monet Blue).
- Fixed a bug where standard DLC uninstallation and SLS config wipes would throw error crashes due to name resolving mismatches.
- Resolved a download task issue where validation scanning logs were misidentified as network download rates, keeping the download speed and ETA displays permanently frozen.
