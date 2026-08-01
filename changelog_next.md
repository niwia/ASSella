# Changelog Next — ASSella Local Build Updates

This file indexes all updates and cleanups implemented for the next version release, providing a reference index for the automated deployment processes.

---

## Release Notes (v2.5.4-hotfix1)

### Important Notice for ASSella v2.5.4 Users:
In build v2.5.4, an issue caused Denuvo status data to unintentionally write to SLSsteam's config.yaml under DenuvoGames:. This could cause SLSsteam to block games listed under that section.

Version 2.5.4-hotfix1 fixes this issue completely:
- One-Time Automatic Cleanup: Launching ASSella v2.5.4-hotfix1 automatically purges any accidental Denuvo entries from /home/deck/.config/SLSsteam/config.yaml, restoring your configuration. A QSettings switch (denuvo_config_cleaned_v255) ensures this cleanup runs only once on initial launch.
- Visual Badges Only: Denuvo statuses are now used strictly for visual badges in the Library and Details panels, with zero interaction or writes to config.yaml. SLS Denuvo sync options have been removed.

### What's New in v2.5.4-hotfix1
- Multi-Layer Branch Support: Full support for branch tracking (testingbranch, public, beta) across Fetch Manifest, Game Details, DepotDownloader, and Smart Update Mode. Includes direct Steam PICS Build ID matching.
- Search & Selection Speedup: Throttled image cache limit checks to run at most once per 10 minutes (bumping limit to 300MB), eliminating disk thrashing and thread-pool freezes when viewing search results.
- Dashboard Alignment: Re-aligned the Dashboard header, Status Ticker, and Pending Updates / Recent Activity cards cleanly to uniform margins.
- Refetch Feedback: Disabled the Refetch button during active fetches, provided immediate visual loading feedback, and added a completion dialog.
