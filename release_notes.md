# ASSella v2.5.2 Prerelease Changelog

## Goldberg Experimental Emulator Update
- Upgraded the bundled Goldberg Steam Emulator libraries to the latest July 2026 experimental builds.
- Brings full compatibility for Steam SDK v1.60 interfaces (including ISteamUGC020, ISteamVideo007, and ISteamTimeline001).
- Introduces native support for the new steam_settings/branches.json configuration schema.
- Added new statistical toggles (allow_unknown_stats, save_only_higher_stat_achievement_progress) to significantly optimize achievement progression saving.
- Replaced the interface scanner binaries (generate_interfaces) with the latest compiled tools for both Windows and Linux environments.

## UI Alignments & Layout Optimization
- Fixed Game Library alignment by adjusting top-row controls and game cards to share the identical horizontal layout boundaries.
- Repositioned the update status badges (Up to date, Checking, Update Available) side-by-side next to the game title inside library cards.
- Italicized the manifest cached and status helper labels.
- Fixed main window status card alignment by applying identical horizontal margin paddings to the central stats dashboard widget.

## UX Improvements
- Implemented sort order memory. Game Library now persists and automatically loads your last chosen sorting preference.
