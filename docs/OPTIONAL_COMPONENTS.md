# Optional components

Goldberg, Steamless and SLScheevo are **not** bundled with ASSella. They are
downloaded on demand from Cloudflare R2 the first time they are needed.

Goldberg alone was 81 MB of the 98 MB in `src/deps/` — 34.8 MB compressed — and
only a minority of games need it (those whose Steam build emulates
multiplayer). Keeping it out means a new Goldberg release ships without
rebuilding the AppImage.

## For users

Open **Settings → Tools → Optional Components**. Each component has one button:

- **Download** — not installed
- **Update** — installed, but a newer version is available
- disabled — installed and up to date

Nothing is downloaded until you ask for it, and no action fails because a
component is missing: pressing *Apply Goldberg* on a game, or enabling
*Apply Goldberg automatically* during a download, will simply offer you the
download button instead.

Installs are verified against a SHA-256 from `components_manifest.json`. A
corrupt or interrupted download never replaces a working install.

## For maintainers

The procedure for updating, packaging and publishing these components lives in
[`cloud/README.md`](../cloud/README.md).