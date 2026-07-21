# ASSella v2.3.3 Release Notes

## Critical Fixes and Improvements

### Steam API and Library Update Detection
- Fixed UnboundLocalError in Steam API batch product info fetcher where empty API responses caused unassigned variable errors, leading to update detection failures.
- Added comprehensive diagnostic logging for update checks returning cannot_determine status (logging missing depot files, empty payloads, and missing Steam public manifests).
- Added library scanning state tracking to GameManager and updated UI statistics panel to show active scanning status instead of premature zero counts.

### Download Progress, Speed, and ETA Smoothing
- Enhanced DownloadDepotsTask progress parsing to distinguish between local disk file validation and network downloading. The UI now displays "Validating local files..." during disk hashing instead of inaccurate network speeds.
- Implemented Exponential Moving Average (EMA) download speed calculation with negative speed clamping and guaranteed persistent ETA formatting.
- Upgraded downloader process output parsing to chunked reads for improved performance and responsiveness.

### UI and Thread Safety Hardening
- Resolved QMetaObject.invokeMethod RuntimeError on Python 3.14 by converting background boot check callbacks to a thread-safe PyQt signal.
- Removed obsolete cursor stylesheet property from bottom titlebar that caused QSS warnings.
- Upgraded settings management to use thread-local QSettings instances for thread safety across background workers.
- Refactored Morrenus API health check endpoints to pass through ISP bypass transport logic.
- Cleaned up legacy proxy fallback code paths from Morrenus API and updated dialog callers.
