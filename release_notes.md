## Version 2.4.2 Hotfix Changelog

- Introduced Branch Selection: Users can now switch between public and beta/alternative branches directly from the Game Details dialog.
- Branch-Aware Build Verification: Installs and updates are now tracked and validated on a per-branch basis. The local installation state and action buttons adapt dynamically based on the selected branch's active build ID.
- Optimize Game Details dialog opening: Cached Steam PICS branches query locally (in SQLite database and memory) to load Game Details instantly in under 1 millisecond on launch.
- Fix background boot check filtering that previously skipped checking for updates for any game cached as up-to-date. The library scanner now checks all active games against Steam PICS on startup.
- Fix in-memory GameManager game update status syncing to ensure the Game Library list view correctly updates and displays newly detected updates without requiring a tool restart.
- Prevent rate limiting by introducing a 2.0-second delay between downloads in the background auto-fetch loop, ensuring all available updates successfully pull their manifests without triggering HTTP 429 errors.
- Optimize Smart Update to always attempt 0-token manifest generation first when new depot IDs are detected, falling back to 1-token classic zip downloads only on generation failure.
