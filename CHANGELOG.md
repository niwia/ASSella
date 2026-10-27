# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.5] - 2026-10-27

### Added
- Dynamic titlebar theming with clean header for normal installs
- Async header fallback and disk color cache
- Dynamic theming with PS5 ambient drift effect
- Game thumbnail vibrant color extraction for ambient radial gradient background
- Redesigned header with click-to-copy AppID, symmetric pills, and smart single-depot routing
- Modernized edition catalog using IStoreBrowseService GetItems API
- DLC mode memory persistence and AT0-M DLC selection dialog
- Custom Byparr solver configuration and Extras plugins support
- AT0-M vs ACCELA mode selection with 64+ DlcData quality-of-life improvements for Steam games
- Missing/pending AT0-M depots displayed as tagged unclickable entries in game details
- Split config buttons for Lobster and Hubcap, AT0-M key sync, single-depot DLC check
- Plugin version display in UI
- Greyscaling of owned DLCs and resized AT0-M setup modal
- Refactored platform mismatch modal UX with eliminated duplicate DLC badges
- Manifest ground truth comparison and self-healing depot recovery
- Human-readable build dates and strict no-ACF invariant enforcement
- Selective DLC entitlement persistence with AdditionalDepots guarded against parent AppIDs
- Direct TLS fast-path for SteamDB racing with dual patchnotes parser and Byparr fallback
- Official SteamDB RSS feed parsing for rich patchnotes and build history
- RenderAppSection fast-path supporting up to 50 builds with Byparr background follow-up
- Bundled zsync fallback support
- Config templates for AT0-M migration
- Online multiplayer and proxy guide with screenshots

### Changed
- Standardized on prepacked spacebunny.lua (v1.8.2) instead of spacebunny1.5 across AT0-M settings and plugin manager, automatically archiving redundant spacebunny1.5.lua to .bak
- Default plugin switched to spacebunny; Lobster autodeployed with AT0-M; smallcrab disabled
- Added steamrun/opensteamtool MRC endpoints

### Fixed
- Safely exclude unkeyed content depots from AdditionalDepots during AT0-M migration with user warnings to prevent DepotWithoutKey failures
- Manifest fix choice modal for atom scope; scoped DLC AppTokens
- Seasonal accent mismatch in tests; aligned DLC mode badge test suites
- Byparr close exception logging and AppToken injection in AT0-M mode
- Pinned vdf git URL to @v4.0 to resolve pip dependency conflict with steam
- Audit findings across AT0-M migration, config permissions, key sync timestamps, and installer
- Lua config string escaping, Hubcap key validation, and plugin .bak preservation on undeploy
- Used get_plugin_status in morrenus tab; added alias in plugin manager
- Test button layout in downloads tab and sanitized wirecutter URL scheme
- Preserved Update All button styling during prep; omitted unconditional -validate
- Refined DLC-only edition selection, polished bottom bar, added ambient radial gradient
- Compact depot selection dialog dimensions; handled blacklisted bundle depots
- Unlinked destination symlink before mv to prevent ELOOP on older coreutils
- Guarded BASH_SOURCE check when executing via piped stdin
- Hardened desktop shortcuts, temp file tracking, download retries, and uninstaller parity
- Repaired broken ACFs in-place instead of deleting
- Removed redundant pre-flight gate from single-game migration engine
- StateFlags set to 6 in ACF during transfer; fixed install instead of deleting

### Build
- Bumped curl-cffi to 0.17.0 for modern browser TLS impersonation

### CI
- Added CI regression gate, guarded cleanup paths, and set StartupWMClass for installer

### Docs
- Added online multiplayer and proxy guide with screenshots

### Reverted
- Reverted UI animation and restored user accent color for buttons and selection

### Other
- AT0-M migration hardening with bundled zsync fallback and config templates (v3.0.4)
