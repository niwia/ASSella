# ASSella v2.5.4 Changelog

## 1. Unified Ratings and Visor Redesign
- Unified Denuvo status checking and ProtonDB rating checking inside a single ratings module.
- Added ProtonDB compatibility caching inside ~/.local/share/ACCELA/protondb_cache.json.
- Integrated thread-safe memory caches and 10 parallel background workers to handle ratings queries asynchronously with zero main-thread block.
- Implemented a 500ms debouncer UI refresh signal to coalesce all completed background fetches into a single main-thread repaint pass.
- Added a settings option under Theme settings to simplify both Denuvo Hypervisor and Uncracked statuses to a single status representation.
- Automatically greyscale subsystems such as SLS Config, SLSsteam, and CloudR if their host files/utilities are not detected.
- Removed the obsolete Denuvo Sync visor label from the main window.
- Repositioned Denuvo and ProtonDB ratings in the details dialog hero title banner to a dedicated row below the title to prevent clipping of long game names.

## 2. Interface Focus and Button Safety
- Implemented DialogRaiser event filter to automatically monitor focus events and raise modal child dialogs (Game Details, Settings, Downloader), preventing lockouts and window freezes.
- Overrode mousePressEvent in ProgressButton to discard user clicks while a task is loading or progress is active, preventing duplicate job submissions.
- Added a 20-second safety fallback timeout to ProgressButton's loading state to reset the clickable state if a background task fails.
- Disabled manual_download_btn when clicked, re-enabling it only after generation and packaging finishes or fails.
- Added update cycle active flag guard to prevent double-click re-entrancy on dashboard Update All.

## 3. Library and Search Improvements
- Replaced the bottom selection bar in the library with a Floating Action Button (FAB) showing "Actions" with a drop-down menu for update and uninstall actions.
- Aligned update notification badges with the top line of multi-line game title labels to improve aesthetics.
- Added a "Pin Build" switch in Game Details preferences to lock the installed build version and disable updates.
- Dynamically parse and cache backup manifest zips for pinned games.
- Redesigned search results in the downloader list to match the library card layout, featuring a 200x94 image, proper typography, badge pills, and a 98px card height.
- Fixed a NameError crash in search list rendering by importing QSizePolicy.

## 4. Workshop and Downloader Integration
- Merged the standalone workshop downloader dialog into FetchManifestDialog as a second tab, removing the obsolete dialog and menu buttons.
- Relocated workshop downloader configurations to Settings -> Downloads -> Workshop Downloader Settings.
- Defaulted workshop Steam integration to True on fresh installs.

## 5. First-Run Presets
- Automatically default application theme to Ocean Breeze (monet blue accent #a1c9fd, deep slate background #111318) on first launch.

## 6. Drag-and-Drop and Recovery Fallbacks
- Expanded drag-and-drop support to process loose .lua, .manifest, or folders, packaging them on the fly.
- Automatically prompt the user to pin dropped builds upon successful packaging.
- Recover missing manifest files by checking local depotcache directories before falling back to Hubcap API download pipelines.
- Implemented an automatic API manifest download fallback in DownloadDepotsTask when required manifests are missing or empty.
- Fixed Refetch (download_only) completing download without parsing the zip. It now executes ProcessZipTask to extract and import depot decryption keys and token.
