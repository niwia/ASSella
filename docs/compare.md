---
title: ASSella vs ACCELA
outline: false
---

# ASSella vs ACCELA

<script setup>
const Y = 'yes', N = 'no', P = 'part'
const groups = [
  { title: 'Downloading', rows: [
    ['Download games with DepotDownloader', Y, Y],
    ['Native Steam downloads (AT0-M)', N, Y, 'Steam itself downloads and updates the game'],
    ['Smart depot presets & editions', P, Y, 'Platform, language and edition picked for you'],
    ['Branches and builds', P, Y],
    ['Roll back to any SteamDB build', N, Y],
    ['Pin a build', N, Y],
    ['Works when your ISP blocks Hubcap', N, Y, 'Cloudflare worker fallback'],
  ]},
  { title: 'Library & game page', rows: [
    ['Library with uninstall', Y, Y],
    ['Game details page with tabs', N, Y, 'Info, Depots, Builds, Tools, Tickets, Workshop'],
    ['Advanced uninstall (prefix, cloud saves)', Y, Y],
    ['"I bought the game" clean-up', N, Y],
    ['Move a game to another library', N, Y],
    ['Workshop downloader', N, Y],
  ]},
  { title: 'Fixes & online', rows: [
    ['SLSonline (Fake AppID 480)', Y, Y],
    ['Netsock & EOS proxy', N, Y],
    ['Launcher bypass', N, Y],
    ['Steamless & Goldberg', Y, Y],
    ['Achievements (SLScheevo)', Y, Y],
  ]},
  { title: 'Ecosystem', rows: [
    ['Decky plugin for Game Mode (Uran)', N, Y],
    ['Browser button on Steam & SteamDB (Epsilon)', N, Y],
    ['Signed, auto-updating SLSsteam plugins', N, Y],
    ['Web UI / headless mode', N, Y],
  ]},
  { title: 'Desktop & system', rows: [
    ['Health check & one-click config fix', N, Y],
    ['Command line mode', Y, Y],
    ['Desktop shortcuts with SteamGridDB icons', Y, N],
    ['accela:// links & .zip right-click (Windows)', Y, N],
    ['Sounds & custom GIFs', Y, P],
  ]},
]
const icon = v => v === 'yes' ? '✓' : v === 'no' ? '—' : 'Partly'
</script>

<div class="vs-hero">
  <div class="vs-side">
    <img src="/images/compare/accela.png" alt="ACCELA" />
    <div class="vs-name">ACCELA</div>
    <div class="vs-sub">The original</div>
  </div>
  <div class="vs-mid">VS</div>
  <div class="vs-side vs-new">
    <img src="/logo.png" alt="ASSella" />
    <div class="vs-name">ASSella</div>
    <div class="vs-sub">Built on top of it</div>
  </div>
</div>

<p class="vs-intro">ASSella is built on <b>ACCELA</b> by Tachibana Labs, and keeps everything that made it good. Here's what each one can do, side by side.</p>

<div class="vs-legend"><span><b class="yes">✓</b> Yes</span><span><b class="part pill">Partly</b> In part</span><span><b class="no">—</b> No</span></div>

<div v-for="g in groups" :key="g.title" class="vs-group">
  <div class="vs-head"><span>{{ g.title }}</span><span>ACCELA</span><span>ASSella</span></div>
  <div v-for="r in g.rows" :key="r[0]" class="vs-row">
    <div class="vs-feat">{{ r[0] }}<small v-if="r[3]">{{ r[3] }}</small></div>
    <div :class="['vs-cell', r[1]]">{{ icon(r[1]) }}</div>
    <div :class="['vs-cell', r[2]]">{{ icon(r[2]) }}</div>
  </div>
</div>

<style scoped>
.vs-hero { display:flex; align-items:center; justify-content:center; gap:28px; margin:12px 0 8px; }
.vs-side { flex:1; max-width:220px; text-align:center; padding:20px 12px; border-radius:16px;
  background:var(--vp-c-bg-soft); border:1px solid var(--vp-c-divider); }
.vs-new { border-color:var(--vp-c-brand-1); box-shadow:0 0 0 1px var(--vp-c-brand-1) inset; }
.vs-side img { height:84px; width:auto; margin:0 auto 10px; }
.vs-name { font-size:1.35rem; font-weight:700; }
.vs-sub { font-size:.85rem; color:var(--vp-c-text-2); }
.vs-mid { font-weight:800; font-size:1.1rem; color:var(--vp-c-text-3); letter-spacing:.1em; }
.vs-intro { text-align:center; color:var(--vp-c-text-2); }
.vs-legend { display:flex; gap:18px; justify-content:center; font-size:.85rem; color:var(--vp-c-text-2); margin:6px 0 18px; }
.vs-group { border:1px solid var(--vp-c-divider); border-radius:12px; overflow:hidden; margin:16px 0; }
.vs-head, .vs-row { display:grid; grid-template-columns: 1fr 92px 92px; align-items:center; }
.vs-head { background:var(--vp-c-bg-soft); font-weight:700; font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; padding:10px 14px; }
.vs-head span:not(:first-child) { text-align:center; }
.vs-row { padding:9px 14px; border-top:1px solid var(--vp-c-divider); }
.vs-feat { font-size:.95rem; }
.vs-feat small { display:block; color:var(--vp-c-text-3); font-size:.78rem; }
.vs-cell { text-align:center; font-weight:700; font-size:1.05rem; }
.yes { color:#3dd68c; } .no { color:var(--vp-c-text-3); }
.part, .vs-cell.part { color:#f0b429; font-size:.72rem; font-weight:700; }
.vs-cell.part, .pill { justify-self:center; padding:2px 8px; border-radius:999px; background:rgba(240,180,41,.12); }
@media (max-width: 560px) {
  .vs-hero { gap:10px; } .vs-side img { height:56px; }
  .vs-head, .vs-row { grid-template-columns: 1fr 64px 64px; }
}
</style>

## Moving from ACCELA

Moving over is painless, because ASSella reads the same places ACCELA used:

- **Settings** (API keys, colours, download folder): ASSella uses ACCELA's settings file, so everything carries over.
- **Downloaded games:** the same Steam libraries and the same `~/.local/share/ACCELA` data folder, so your library shows up straight away.
- **Manifests and keys:** ACCELA's cached manifest bundles are moved from `morrenus_manifests/` to `hubcap_manifests/` on first start. Your SLSsteam `config.yaml` (depots and decryption keys) is kept as it is.

**To switch:**

1. Close ACCELA.
2. Install ASSella ([Installation Guide](/install)).
3. Open it. Your games and settings are already there.
4. Optional: turn on [AT0-M](/at0m) so Steam downloads and updates games for you.

::: tip What ASSella doesn't have (yet)
ACCELA's desktop shortcuts with SteamGridDB icons, its `accela://` links and `.zip` right-click menu on Windows, and its sound effects aren't in ASSella. If you rely on one of these, keep ACCELA around for it. Both apps can be installed side by side.
:::
