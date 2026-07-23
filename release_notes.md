## Smart Update Mode & Key-Based Manifest Generation
- Implemented Hubcap Smart Update Mode utilizing key-based manifest generation via `/api/v1/generate/appmanifest/{appid}`.
- Smart Update Mode generates live manifest packages for 0 API tokens by reusing cached AES depot keys and AppTokens.
- Integrated `depot_keys.db` SQLite database with thread-safe atomic caching for depot keys and AppTokens.
- Implemented timestamp-aware key persistence ("Latest = Always Better") that compares `.lua` modification timestamps to prevent overwriting newer cached keys with older data.
- Full Backward Compatibility: Maintained fallback to classic `.lua` zip manifest downloads whenever keys are not yet cached, automatically populating `depot_keys.db` on first fetch to upgrade all future updates to free Smart Updates.
- Reconstructed depot data for `.lua`-less generate bundles from `depot_keys.db` and `depots.ini`, ensuring seamless installation and zero-cost updates.

## Local Cache Verification & Optimization
- Updated Verify to utilize local cached manifest zips (`accela_fetch_{appid}.zip` or selected rollback build zips) directly without making unnecessary network requests to Hubcap, consuming API tokens, or generating duplicate backup files.
- Deprecated and removed legacy Stage 2 Hubcap manifest freshness verification routines in favor of direct Steam PICS build matching.

## Game Details & UI Redesign
- Redesigned the Game Details actions row with an expanded "Open Install Folder" button and streamlined layout.
- Enhanced the Build Selection dropdown to display file modification timestamps alongside Build IDs (e.g. `Backup Build: 23968060 (2026-07-23 14:05)`), allowing clear identification of distinct backups.
- Applied dropdown color coding (vibrant green for latest build, warm orange for historical backups).

## ASShead & SLSsteam Configuration Fixes
- Added missing upstream scalar keys (e.g., `DumpClientInterfaces`) to ASShead validation rules.
- Implemented dynamic runtime registration of new upstream template keys to prevent infinite repair loops when SLSsteam introduces new configuration settings.
- Filtered out game AppIDs from being incorrectly added as content depots during key migration and cache reconstruction.
- Ensured SLSsteam Config Management is automatically enabled when an active `SLSsteam.so` installation is detected on Linux.
