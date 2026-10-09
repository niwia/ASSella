# cloud/ — optional components & R2 publishing

**This folder is local-only.** It is excluded from git by `.gitignore` in this
directory, so the 35 MB archives never get committed.

## What lives here

| File | What it is |
|---|---|
| `goldberg.tar.gz` | Goldberg emulator payload (~35 MB) |
| `steamless.tar.gz` | Steamless launcher (~140 KB) |
| `slscheevo.tar.gz` | SLScheevo achievements helper (~15 KB) |
| `components_manifest.json` | SHA-256 + size per component. The app compares against this to decide "Download" vs "Update". |
| `generate_components_manifest.py` | Rebuilds the archives + manifest from `src/deps/` |
| `update_goldberg.sh` | Fetch a new Goldberg release → package → publish |
| `publish_components.sh` | Upload components + manifest to R2 |

Goldberg was **81 MB of the 98 MB** in `src/deps/` and most users never need it
(it only applies to games whose Steam build emulates multiplayer). Compressed it
is 34.8 MB, which is what gets shipped on demand.

---

## Quick reference

```bash
# Update Goldberg from its GitHub releases and publish it
./cloud/update_goldberg.sh

# Update Goldberg but don't publish yet (inspect first)
./cloud/update_goldberg.sh --no-publish

# Update a specific tag instead of the latest release
./cloud/update_goldberg.sh release-2026_09_27

# Republish the current archives (e.g. after editing the manifest by hand)
./cloud/publish_components.sh
```

That's the whole loop. No version numbers to bump, no AppImage rebuild.

---

## How users get it

`components_manifest.json` carries a SHA-256 per component. On launch the app
compares it against the hash recorded at install time:

| Situation | Status shown in Settings → Tools | Behaviour |
|---|---|---|
| Never downloaded | `Not installed (34.8 MB)` | **Download** button |
| Installed, hash matches | `Installed and up to date` | Button disabled |
| Installed, hash differs | `Update available (34.8 MB)` | **Update** button |
| Present but untracked | `Bundled with an older ASSella build` | **Download** to re-verify |

Downloads are atomic: files land in a temp directory, the SHA-256 must match,
and only then are they swapped into place. A failed or interrupted download
leaves the previous install untouched, and archive entries that try to escape
the destination folder are skipped.

### Where things get installed

Components go into `Paths.deps(...)`, the same directory the rest of the code
reads from, so no call site needed changing. That resolves to:

- **Unpackaged / dev** → `src/deps/` inside the repo
- **Packaged AppImage** → `~/.local/share/ACCELA/deps/` — *not* the AppImage
  mount, because that is a throwaway temp dir that would force a 35 MB
  re-download on every launch

`Paths.DEPS` is repointed rather than given a second path, so
`Paths.deps("Goldberg")` and the downloader can never disagree. (They did once:
downloads landed in the data dir while the code looked in `src/deps`, making the
component permanently "not installed" during development.)

---

## How `update_goldberg.sh` works

Source: <https://github.com/Detanup01/gbe_fork/releases>

Each release publishes several assets:

```
emu-linux-debug.tar.bz2      <- debug build, not for end users
emu-linux-release.tar.bz2    <- the one we use (82 MB)
emu-win-debug-vs22.7z       <- Windows only
emu-win-release-vs22.7z     <- Windows only
migrate_gse-linux.tar.bz2   <- migration helpers, not the payload
migrate_gse-win-vs22.7z
migrate_gse-win.7z
```

The script picks `emu-linux-release.tar.bz2` explicitly rather than guessing,
and picks the decompression command from the filename. It then:

1. Locates the payload inside the extracted tree (release archives vary in how
   deeply they nest it)
2. Refuses to package unless `steam_appid.txt`, `version` and a `linux/` or
   `windows/` folder are present — catches a truncated or wrong-asset download
3. Regenerates the archive and manifest
4. Publishes, unless `--no-publish`

## Reproducibility

Archives are deterministic: fixed mtime, sorted entries, no owner metadata. The
same input always produces the same SHA-256, so the manifest only changes when
the component content actually changes — which is what makes "update
available" meaningful rather than noisy.

---

## Manual publishing

`publish_components.sh` symlinks the artefacts into the publish directory and
uploads:

1. Payload archives first
2. `components_manifest.json` and `files.json` **last**

That ordering matters — a client must never fetch a manifest advertising a file
the bucket does not have yet.

Override the locations if needed:

```bash
PUBLISH_DIR=/some/other/dir ./cloud/publish_components.sh
BUCKET=my-bucket ./cloud/publish_components.sh
```

To publish the Lua plugins as well, use `/home/aiwin/r2-publish/publish.sh`,
which covers both plugins and components.

---

## Verifying before you publish

```bash
# Nothing in src/ is broken (use the locked 3.13 env - see docs/DEPENDENCIES.md)
python3 scripts/smoke_import_check.py

# The card, guards and download path all work, against a local HTTP server
QT_QPA_PLATFORM=offscreen python3 scripts/test_components_ui.py
```

---

## Trimming the AppImage

Once users are downloading these, remove them from the shipped bundle:

```bash
git rm -r --cached src/deps/Goldberg src/deps/Steamless src/deps/SLScheevo
```

Keep the local copies while developing — `Paths.deps("Goldberg")` resolves the
same either way, and `is_component_available()` reports whether files are
present. The GUI never hard-fails on a missing component; it offers the download
button instead.

## Verified paths

| Component | Bundled | Compressed | Uploaded |
|---|---|---|---|
| Goldberg | 81 MB | 34.8 MB | 13 files |
| Steamless | 484 KB | 137 KB | 14 files |
| SLScheevo | 236 KB | 14 KB | 4 files |