## Version 2.4.5 Release Changelog

### Implemented Changes & Bug Fixes
- Consolidated high-density status dashboard containing two rows of system indicators. Row 1 lists daily Hubcap API stats and resets, expiry dates formatted in bracket syntax ([ 336d ]), SLS configuration, SLSsteam, and Steam Updates. Row 2 lists network connection mode indicators (supporting direct and DoH/Tor mode fallbacks), ASSella version updates, and dynamic game library size statistics.
- Seamless game updates layout with game cover art thumbnails cropped to fill the right section of the card background and blended into the solid background color on the left via linear gradient transition.
- Compact pending updates pill size reduced to 38px height.
- Dedicated game quotes rotator widget relocated as a full-width footer at the extreme bottom of the main layout, with center-aligned text.
- Cleaned up quotes logic from the terminal sidebar widget.
- Modernized the simplified terminal idle view into a clean two-column layout showing pending updates and recent activity.
- Floating action button (FAB) Update All overlayed on the bottom-right of the scroll area, styled with theme colors and responsive resize tracking.
- Restored classic manifest behavior by removing the update manifest toggle.
- Fixed an issue where the download update warning button in Game Details showed up with the wrong download state on launch.
- Fixed a Linux crash occurring during file select dialog calls.
- Fixed the Update All flow to correctly respect branch selections.
- Automatically hide Linux depots when no Linux files are present in the manifest.
- Added verification status updates to download progress tasks.
- General update checker search optimizations.
- Initiated stable build pipeline configurations.

### Known Issues & Bugs
- Verification speed and ETA calculations during active verification tasks are currently broken.
- Game names may display duplicate beta branch suffixes under certain conditions.
- Some text colors and font contrast issues reduce readability on some UI pages.

### Planned Features
- Redesign and UI glow-up of the Tools section inside the Game Details panel.
- Extend the "Select All" function in details selection lists to verify and uninstall operations.
- Append status results reporting of update searches directly to the pending updates log view.
- General cleanup and updates of the credits list.
- Complete removal of the legacy zsync updater module.
- Enable user capability to request and download specific game build IDs.
