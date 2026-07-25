## Version 2.4.5 Release Changelog

### User Interface Redesign
- Consolidated high-density status dashboard containing two rows of system indicators. Row 1 lists daily Hubcap API stats and resets, expiry dates formatted in bracket syntax ([ 336d ]), SLS configuration, SLSsteam, and Steam Updates. Row 2 lists network connection mode indicators (supporting direct and DoH/Tor mode fallbacks), ASSella version updates, and dynamic game library size statistics.
- Seamless game updates layout with game cover art thumbnails cropped to fill the right section of the card background and blended into the solid background color on the left via linear gradient transition.
- Compact pending updates pill size reduced to 38px height.
- Dedicated game quotes rotator widget relocated as a full-width footer at the extreme bottom of the main layout, with center-aligned text.
- Cleaned up quotes logic from the terminal sidebar widget.
- Modernized the simplified terminal idle view into a clean two-column layout showing pending updates and recent activity.
- Floating action button (FAB) Update All overlayed on the bottom-right of the scroll area, styled with theme colors and responsive resize tracking.

### Core Bugfixes & Feature Polish
- Restored classic manifest behavior by removing the update manifest toggle.
- Fixed an issue where the download update warning button in Game Details showed up with the wrong download state on launch.
- Fixed a Linux crash occurring during file select dialog calls.
- Resolved duplicate beta branch suffixes appearing on game names.
- Optimized update check routines and simplified the Update All flow to respect branch selections.
- Automatically hide Linux depots when no Linux files are present in the manifest.
- Added verification status updates to download progress tasks.
- Restored download speed and ETA tracking parameters during download verification phases.
- Polished details dialog tools section layouts and overall text/font color contrast.
- Extended the Select All toggle to verify and uninstall operations in details lists.
- Integrated status results reports into pending updates logs.
- Cleaned up application credits and removed legacy zsync updater components.
- Added capability to select and download specific build IDs of games.
