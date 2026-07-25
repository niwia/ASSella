## Version 2.4.5-r2 Release Changelog

### Implemented Changes & Bug Fixes
- Fixed Settings Dialog save crash: Resolved PyQt6 RuntimeError caused by garbage collection of the commented-out Manifest Rollback Settings tab widgets by adding safety try-except blocks.
- Fixed PyQt6 import error in Settings Dialog exception handler to prevent hard crashes.
- Implemented real-time incremental update checking progress: Added update_check_progress signal to GameManager and hooked it to set_updates_checking_progress in SimplifiedTerminalWidget to show live progress of batched network queries (e.g., CHECKING 20/166).
- Decoupled update checking logic from cached statuses to query Steam PICS API live (comparing live manifest GIDs against local depot files) regardless of cache state.
- Added Material You style Floating Action Button (FAB) for manual updates refresh (↻) to run a forced check bypassing all cache checks.
- Added Check Updates on Boot configuration option to Settings.
