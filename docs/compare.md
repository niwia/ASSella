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
    ['Delta updates', N, Y, 'Updates download only the changed parts, not the whole game'],
    ['Auto speed tuning', N, Y, 'Finds the fastest number of parallel downloads for your connection'],
    ['Smart depot presets & editions', P, Y, 'Platform, language and edition picked for you'],
    ['DLC-only mode', P, Y, 'Own the base game? Add just the DLC'],
    ['Bring your own .lua / .manifest', P, Y, 'Drag and drop, or Library → Scan for LUA'],
    ['Branches and builds', P, Y],
    ['Tested builds list (Voices)', N, Y, 'Recommends known-good builds for tricky games'],
    ['Roll back to any SteamDB build', N, Y],
    ['Pin a build', N, Y],
    ['Works when your ISP blocks Hubcap', N, Y, 'Cloudflare worker fallback'],
    ['Animated download progress', P, Y],
  ]},
  { title: 'Updates', rows: [
    ['Update checks', P, Y, 'SteamCMD and Steam PICS checked in parallel'],
    ['Update All in one click', N, Y],
    ['Updates without Hubcap (MRC providers)', N, Y, 'AT0-M only; providers can be down, so not always reliable'],
  ]},
  { title: 'Steam integration', rows: [
    ['No Steam restart after installs (SLSsteam API)', P, Y, 'Games appear and disappear instantly'],
    ['Instant uninstall', N, Y],
    ['SLSsteam ownership tickets', N, Y, 'Import, verify and remove per game'],
    ['Health check & one-click config fix', N, Y],
    ['SLSsteam config fixer (ASShead)', N, Y, 'Rebuilds config.yaml on the latest template, keeping all your entries'],
    ['Missing keys refetched automatically', N, Y, 'Keys that were missing are fetched again when Hubcap gets them'],
  ]},
  { title: 'Library & game page', rows: [
    ['Library with uninstall', Y, Y],
    ['Multi-select: update, uninstall or move many games', N, Y],
    ['Game details page with tabs', N, Y, 'Info, Depots, Builds, Tools, Tickets'],
    ['ProtonDB & SteamDB badges', N, Y],
    ['SteamDB build history', N, Y, 'Direct; Byparr only as an optional fallback'],
    ['Advanced uninstall (prefix, cloud saves)', Y, Y],
    ['"I bought the game" clean-up', N, Y],
    ['Workshop mods', N, Y, 'Through AT0-M (the old downloader is retired)'],
  ]},
  { title: 'Fixes & online', rows: [
    ['SLSonline (Fake AppID 480)', Y, Y],
    ['Netsock & EOS proxy', N, Y],
    ['Launcher bypass', N, Y],
    ['Steamless & Goldberg', Y, Y, 'Downloaded only when you need them in ASSella'],
    ['Achievements (SLScheevo)', Y, Y],
  ]},
  { title: 'Ecosystem', rows: [
    ['Decky plugin for Game Mode (Uran)', N, Y],
    ['Browser button on Steam & SteamDB (Epsilon)', N, Y],
    ['Signed, auto-updating SLSsteam plugins', N, Y],
    ['Web UI / headless mode', N, Y],
  ]},
  { title: 'Desktop & system', rows: [
    ['Modern, themeable interface', P, Y],
    ['Smaller app; extras downloaded on demand', N, Y],
    ['Command line mode', Y, Y],
    ['Desktop shortcuts with SteamGridDB icons', Y, Y],
    ['accela:// links & .zip right-click (Windows)', Y, N],
    ['Sounds & custom GIFs', Y, P],
  ]},
]
const highlights = [
  { t: 'No Steam restarts, ever', d: 'ASSella talks to Steam live through the SLSsteam API. Games you install show up in Steam at once, uninstalls vanish at once, and config changes apply while Steam keeps running.', wide: true },
  { t: 'Steam does the downloading', d: 'With AT0-M the real Steam client downloads and updates your games, like games you own.', link: '/at0m' },
  { t: 'Only what changed', d: 'Updates download just the changed parts (delta updates), and the download speed tunes itself.' },
  { t: 'Smarter updates', d: 'SteamCMD and Steam PICS are checked side by side, and all your games update in one click.' },
  { t: 'Any version you want', d: 'Roll back to any SteamDB build, pin it, or start from your own .lua and manifest files.', link: '/guides/downgrade' },
  { t: 'Game Mode and browser', d: 'Uran adds games from Game Mode, Epsilon from the Steam Store and SteamDB in your browser.', link: '/uran' },
  { t: 'Checks itself', d: 'The Health tab finds broken SLSsteam setups and fixes them in one click. Rarely used tools download only when you need them.' },
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

<p class="vs-intro">ASSella is built on <b>ACCELA</b> and keeps everything that made it good. Here's what each one can do, side by side.</p>

<div class="vs-hl">
  <component :is="h.link ? 'a' : 'div'" v-for="h in highlights" :key="h.t" :class="['vs-card', { 'vs-wide': h.wide }]" :href="h.link">
    <div class="vs-ct">{{ h.t }}</div>
    <div class="vs-cd">{{ h.d }}</div>
  </component>
</div>

<h2 class="vs-h2">Feature by feature</h2>

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
.vs-hl { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:22px 0 8px; }
.vs-card { display:block; padding:16px; border-radius:14px; background:var(--vp-c-bg-soft); border:1px solid var(--vp-c-divider);
  text-decoration:none !important; color:inherit !important; transition:border-color .2s, transform .2s; }
a.vs-card:hover { border-color:var(--vp-c-brand-1); transform:translateY(-2px); }
.vs-wide { grid-column:1 / -1; border-color:var(--vp-c-brand-1); background:linear-gradient(135deg, var(--vp-c-brand-soft), var(--vp-c-bg-soft) 60%); }
.vs-wide .vs-ct { font-size:1.15rem; }
.vs-ic { font-size:1.5rem; line-height:1; margin-bottom:8px; }
.vs-ct { font-weight:700; margin-bottom:4px; }
.vs-cd { font-size:.85rem; line-height:1.45; color:var(--vp-c-text-2); }
.vs-h2 { text-align:center; border-top:none !important; margin-top:28px !important; }
@media (max-width: 760px) { .vs-hl { grid-template-columns:1fr 1fr; } }
@media (max-width: 480px) { .vs-hl { grid-template-columns:1fr; } }
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
ACCELA's `accela://` links and `.zip` right-click menu on Windows, and its sound effects, aren't in ASSella yet. If you rely on one of these, keep ACCELA around for it. Both apps can be installed side by side.
:::
