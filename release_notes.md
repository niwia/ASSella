# ASSella v2.3.1-rc2 Release Notes

## DLC Only Mode Enhancements
- Restructured DLC only mode to be modular, robust, and safe.
- Uninstallation in DLC only mode now deletes only the files belonging to the selected DLC depots, strictly leaving the base game files untouched.
- Cleaned up empty installation folders automatically after a DLC-only uninstall if no other files remain.
- Synchronized DLC configuration rules to config.yaml seamlessly.
- Consolidated all DLC mode state queries throughout the codebase to use a centralized helper.

## Game Details Redesign
- Fully redesigned the Game Details page with a compact, layout-matching design.
- Replaced the vertical sidebar with a clean horizontal tab bar (Info and Tools).
- Aligned text labels, inputs, and buttons to use unmuted high-contrast typography that matches the main application's theme.
- Structured the Info tab with a compact grid layout for key statistics (Install size, Manifest cache status).
- Replaced path details with a single full-width button to open the installation folder directly.
- Consolidated status pill and check-for-updates actions, displaying the last-checked timestamp inline.
- Integrated Goldberg Emulator and DRM actions into a neat, grid-based layout in the Tools tab.
- Added quick links (Steam Store, SteamDB) and copy clipboard helpers (App ID, Install path).
- Redesigned the Uninstall workflow to expand as an inline danger zone pill at the bottom of the Info tab.
