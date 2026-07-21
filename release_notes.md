# ASSella v2.3.2 Beta Release Notes

> CAUTION: This beta release includes new two-stage Hubcap manifest freshness verification features. While basic logic and dry-run tests have passed, these features are newly implemented and require further real-world testing. Please use with caution.

## Stage 1 & Stage 2 Hubcap Manifest Verification
- Implemented a two-stage verification safeguard system to prevent downloading outdated or stale manifests from Hubcap when Steam updates occur.
- Stage 1 Pre-Download Check: Queries Hubcap status API before downloading update manifests to check if Hubcap reports needs_update or update_in_progress. Displays warning prompts if Hubcap is not yet ready.
- Stage 2 Post-Download Check: Compares downloaded manifest IDs inside the zip archive against Steam's latest manifest ID. Alerts users if the downloaded manifest is older than Steam's live build or identical to currently installed game files.
- Smart Pass for File Validation: When running "Validate Files" on up-to-date games, Stage 1 pre-checks are bypassed, and Stage 2 skips identical-version alerts to allow fast, seamless re-verification while still protecting against outdated validation files.

## Experimental Refined Update Check
- Added a new "Refined Update Check" setting under the ASSella -> Experimental section in Settings (disabled by default).
- Modular Verifier: Built src/utils/manifest_verifier.py to parse and compare Steam's live build release timestamp (timeupdated) against Hubcap's manifest modification date (file_modified) strictly in UTC.
- Zero Local Clock Sensitivity: Bypasses system clocks, local timezones, and Deck settings by comparing pure UTC timestamps.
- Non-Blocking Graceful Fallback: If Steam or Hubcap timestamps are missing or unreachable, the check automatically yields a cannot_determine status and bypasses without hanging or blocking user downloads.
- UI Integration: When enabled, Hubcap manifest staleness is reflected directly in Game Details status pills and download confirmation dialogs.
