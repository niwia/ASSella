# Plugin Modification & Publishing Rules

Whenever changes are made to SLSsteam Lua plugins (e.g., `download.lua`, `spliced-tickets.lua`):

1. **Storage Location**:
   - All modified plugins must be saved to `/home/aiwin/.local/share/ACCELA/plugins/` (and synced to active SLSsteam plugin directory `~/.config/SLSsteam/plugins/`).
   - The plugin files and `plugins_manifest.json` are excluded from the git repository.

2. **Decimal Version Increment**:
   - Run `python3 scripts/generate_plugins_manifest.py --output /home/aiwin/.local/share/ACCELA/plugins/plugins_manifest.json`.
   - The manifest generator uses decimal rollover versioning: `x.y.z`
     - Per change/file alteration, `z` increments from 0 to 9.
     - When `z` reaches 10, it rolls over to 0 and increments `y` by 1.
     - When `y` reaches 10, it rolls over to 0 and increments `x` by 1.

3. **Cloudflare R2 Publishing**:
   - After updating the plugins and generating the manifest, execute `/home/aiwin/r2-publish/publish.sh`.
   - This publishes the fresh plugins and manifest to the Cloudflare R2 bucket (`github-files`).
