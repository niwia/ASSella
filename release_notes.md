# Release Notes - ASSella v2.3.1-rc1

### SLS Config Enhancements & Dynamic Rules

* **Dynamic Validation Rules:** The SLS config validator (ASShead) now fetches its validation rules (`asshead_rules.json`) dynamically from the ASSfixer GitHub repository on boot. New SLSsteam configuration options can now be supported immediately without requiring an ASSella update. An offline fallback is in place.
* **Open Config Button:** Added a button in Settings -> SLS to open the config.yaml file directly in the system's default text editor.
* **Restore Backup Button:** Added a button in Settings -> SLS to restore the last backup copy of config.yaml. The button enables/disables dynamically based on backup file availability.

### Credits Screen Redesign

* **Adaptive Layout:** Redesigned the Credits & Updates page into a dynamic, responsive grid layout that adapts to container resizing.
* **Pill Badges:** Added orange BETA and ALPHA branch badges next to the version label to clearly distinguish pre-release channels.
* **Simplified Credits:** Cleaned up descriptions to lists, credited contributors and third-party projects directly.

### Robust Self-Updater

* **Delta updates fixed:** Corrected the ZSync asset filename mapping in the build pipeline and updater logic, resolving the exit code -6 (SIGABRT) crash.
* **Robust Fail-safe:** If delta updates fail, the updater automatically falls back to streaming the full AppImage directly from GitHub, featuring a live progress bar with downloaded and total sizes.
* **Fixed Background Checker Crash:** Fixed a missing import error (re module) that previously caused background update checks to fail silently.
