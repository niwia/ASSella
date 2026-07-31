# Changelog Next — ASSella Local Build Updates

This file indexes all updates and cleanups implemented for the next version release, providing a reference index for the automated deployment processes.

---

## 1. Visor & Settings Updates
- **New Dashboard Metrics**: Integrated `CloudR` (On/Off/Missing) status labels to the main visor panel.
- **Unified Ratings Module**: Replaced the Denuvo-only module with a unified `ratings` module that handles both Denuvo crack statuses (scraped from `isitcracked.com` with a 6-hour cache) and ProtonDB compatibility tiers (fetched asynchronously from `protondb.com` with a 7-day cache).
- **ProtonDB Badge Integration**: Displays solid-colored compatibility rating pills (`PLATINUM`, `GOLD`, etc.) at the bottom-right corner of game cards in the library list, as well as on game details page headers.
- **Rich Search Results Layout**: Replaced plain search result strings in the downloader list with a polished `SearchItemWidget` containing game cover headers, Denuvo pills, and ProtonDB compatibility badges.
- **Simplify Denuvo Status Option**: Added checkbox under Theme settings to simplify both Hypervisor and Uncracked statuses to a single status representation.
- **Dynamic Grayscaling**: Subsystems such as `SLS Config`, `SLSsteam`, and `CloudR` automatically grayscale if their host files/utilities are not detected.


- **Ignore SLSsteam Updater Option**: Added setting option checkbox under SLS tab to ignore remote GitHub SLS version checks (highly requested for custom Headcrab updater setups).
- **Theme Settings Cleanups**: Removed the obsolete "Ignore safety color limits" checkbox from the settings panel.

## 2. Dialog Focus Z-Order Safety
- **DialogRaiser Event Filter**: Implemented `DialogRaiser` event filter which monitors parent focus/restoration events and automatically raises active modal child dialogs (Game Details, Settings, Downloader) to prevent interface freezing and focus lockouts.

## 3. Library QoL Improvements
- **ProgressButton Integration**: Replaced standard verification action button in Game Details with a custom `ProgressButton` that paints live progress percentages or indeterminate loading pulses without obscuring button text.
- **Auto Theme Colors**: Programmatically generates success/cached action colors (Verify Files / Pinned) using theme-harmonized HSV hue shifts.
- **Pin Build Workflow**: Added a "Pin Build" switch in Game Details preferences. Activating this locks the installed build version, disables periodic and manual update checks, and replaces the verify action with "Verify Pinned Build".
  - **Smart Dependencies**: Activating Pin Build automatically unchecks and disables/greys out "Exclude from update-all".
  - **Dynamic Manifest Reconstruction**: Dynamically scans the game's `depotcache` directory for `.manifest` files and parses them to populate the game's manifest mapping for pinned games.
  - **Smart Baseline Duplication**: Toggling Pin Build ON automatically duplicates the game's default manifest zip as a specific build backup zip if it is not already present.
- **Selection FAB**: Replaced the bottom selection bar in the library with a Floating Action Button (FAB) showing "Actions" with a drop shadow. Click opens a dropdown menu with **Update Selected** and **Uninstall Selected** (which performs batch directory + `.acf` cleanups with confirmation dialogs and single-dialog progress indicators).
- **Badge Top-Alignment**: Aligned the "New version available" badge with the top line of multi-line game title labels in the library list widget to ensure perfect layout aesthetics.

## 4. Unified Downloader & Workshop Tab
- **Integrated Workshop Downloader**: Merged the standalone workshop dialog into `FetchManifestDialog` as a second tab, cleaning up obsolete code files. Rerouted titlebar/tray actions to launch `FetchManifestDialog` with the Workshop tab pre-selected.
- **Relocated Workshop Options**: Moved workshop configurations (Max downloads, Cell ID, Steam Integration) into `SettingsDialog -> Downloads -> Workshop Downloader Settings`.
- **Steam Integration Default**: Set workshop Steam integration to default to `True` for new installations.

## 5. First-Run Presets
- **ASSella First-Run Theme Override**: Automatically defaults the application theme to **"Ocean Breeze"** (monet blue accent `#a1c9fd`, deep slate background `#111318`) on first launch of ASSella, ensuring modern premium aesthetics for new users without clashing with existing ACCELA configs.

## 6. Drag-and-Drop & Missing Manifest Recovery
- **Expanded Drag-and-Drop**: Users can now drag and drop loose `.lua` files, `.manifest` files, or folders containing them. The system automatically packages them recursively into a temporary `.zip` archive on the fly and queue-processes it. Fixed a NameError bug where `os` was not defined during drop events.
- **Pin Dropped Builds Prompt**: Automatically prompts the user if they want to pin the build when dragging and dropping files or zips. Toggles Pin Build ON and caches the manifest zip upon queue job completion.
- **Manual Download Auto-Pin**: Toggling manual build ID downloads and installs automatically checks Pin Build and caches the generated zip under the specific build ID for offline-friendly file verification.
- **Local Depotcache Recovery Fallback**: In `DownloadDepotsTask`, check the library's local `depotcache` folder first to restore missing `.manifest` files before falling back to the Hubcap API download pipeline.
- **API Manifest Recovery Fallback**: Injected a fallback download mechanism into `DownloadDepotsTask`. If a required manifest file is missing or corrupted (0 bytes), it is automatically fetched from the Hubcap API and extracted in the workspace before download/verification execution.

## 7. Ratings Optimization & Search Redesign
- **Zero-Overhead Redesign**: Fully decoupled Denuvo and ProtonDB ratings checks from library card widget construction. Badges are initialized as hidden on the main thread, instantly eliminating lock acquisition overhead and layout blocking.
- **Batched Deferred Paint**: Automatically populates Denuvo and ProtonDB cached badges 300ms after the list is drawn, keeping library load snappy.
- **Thread-Safe Concurrent Worker Queue**: Replaced the per-card thread-spawning pipeline with a thread-safe Queue and 10 parallel background workers.
- **Atomically Serialized Caching**: Locked cache updates under a global memory lock to prevent parallel workers from clobbering each other's disk writes, ensuring 100% of fetched ProtonDB ratings are successfully cached.
- **Search Results Redesign**: Revamped `SearchItemWidget` to look like a premium library card, featuring a 200x94 image, proper typography, badge pills, and a 98px item height.
- **Update All Double-Click Guard**: Added an update cycle active flag to prevent double-click re-entrancy issues on the dashboard.
- **Refetch Button Key Extraction**: Fixed Refetch (download_only) completing download without parsing the zip. It now executes `ProcessZipTask` to extract/import depot decryption keys and token, shifting the button text from "Refetch" to "Verify Files".
- **ProgressButton Safety Timeout**: Added a 20-second fallback timer to ProgressButton's loading state. If a task fails to clear the state due to background thread errors or unhandled exceptions, the button automatically resets to clickable.


