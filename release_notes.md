## Version 2.4.4 Release Changelog

### Update Check Engine
- Authoritative Depot Manifest Matching: Branch build ID changes no longer trigger false positive updates. Depot manifest IDs are now the sole authoritative comparison signal. Branch build ID changes are logged for diagnostic visibility only.
- Per-Branch Build ID Tracking: Implemented branch-specific build ID tracking using an 'installed_buildid/{appid}/{branch}' structure with backward-compatible fallback to flat keys.
- Diagnostic Cache Metadata: The 'update_status_cache.json' cache now stores the branch name, branch/build IDs, depot diff details, and reason strings for every checked game.
- Smart Skip & De-duplication: Single-game checks defer to running batch checks to prevent overlaps. Cold boot scanner skips up-to-date games, delegating checking of up-to-date games to the periodic check timer.

### Steam API Resilience
- Exponential Backoff: Implemented backpressure backoff for batched product info calls. Consecutive network or client failures delay requests by 1s, 2s, 4s, up to a 30-second cap. Successful batches decay backpressure.

### Game Details V2 Design
- Hero Layout (100px): Enabled taller hero layout by default. Essential stats (SIZE, MANIFEST, BUILD, LUA) are presented horizontally directly under the game's title in the header banner. Added a fallback option 'USE_V2_HERO = False' to revert to the legacy 65px layout.
- Branch-Aware Defaults: Opening details defaults directly to the installed branch instead of resetting to public.
- Branch Suffixes: Display titles in the details header and games library list show branch suffixes, e.g. Game Name (beta), next to the title.
- DLC Mode Label: Displayed in the header next to the title instead of inside the stats grid.
- Simplified Action Row: Removed the rollback/backup dropdown from the details action row. Verify, download, and install buttons are simplified to a cache-presence check.
- Instant Caching Path: Game details pull branch information from local SQLite/database cache instantly (<1ms) on dialog creation, with a silent background PICS refresh updating the UI when done.

### Smart Update & Auto-Fetch
- Branch-aware Smart Updates: smart update tasks pull build IDs for the selected branch instead of resetting to public.
- Overwriting Manifest Bundles: Save operations overwrite fetched zip manifest bundles directly instead of archiving multiple old builds.
- Depot Filtering: Smart update tasks only query and include depots specified in the saved depot file, skipping common redistributables.

### Uninstall
- SLS Wipe Option: Added a "Wipe SLS (you own the game)" checkbox during uninstall. This removes the app from the SLS configuration and deletes the '.DepotDownloader' folder while leaving ACF, Proton prefixes, and saves intact.
- Auto-Scroll Panel: Uninstall sub-panel scrolls to view options automatically when expanded.
