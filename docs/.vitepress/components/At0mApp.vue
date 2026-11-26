<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

/* ------------------------------------------------------------------ *
 * at0m — web console
 *
 * Metro/Windows-8 design language: flat planes, 0px radius, no shadows,
 * tight tile gutters, panorama typography, accent-driven highlights.
 *
 * Data sources:
 *   - paired with Epsilon: library + search from 127.0.0.1:1952 /v1
 *   - preview metadata: public api.steamcmd.net (no Hubcap until Register)
 *   - otherwise: demo catalog (?demo=1 keeps it on purpose)
 * ------------------------------------------------------------------ */

const BRIDGE_BASE = 'http://127.0.0.1:1952'
const CACHE_KEY = 'at0m.library'
const CACHE_VERSION = 2
const CACHE_MAX = 500
const MAX_FLIPPED = 2

const ACCENTS = [
  { name: 'cobalt', hex: '#0050EF' },
  { name: 'teal', hex: '#008299' },
  { name: 'emerald', hex: '#008A00' },
  { name: 'magenta', hex: '#D80073' },
  { name: 'amber', hex: '#F0A30A' },
  { name: 'crimson', hex: '#A20025' },
  { name: 'violet', hex: '#AA00FF' },
  { name: 'steel', hex: '#647687' }
]

const ACCENT_CYCLE = ['#1BA1E2', '#60A917', '#E3C800', '#FA6800', '#0050EF', '#A20025', '#00ABA9', '#AA00FF']

const GLYPHS = {
  ring: '<circle cx="32" cy="32" r="17"/><circle cx="32" cy="32" r="6"/>',
  cube: '<path d="M32 8 54 20v24L32 56 10 44V20z"/><path d="M10 20l22 12 22-12M32 32v24"/>',
  prism: '<path d="M32 7 55 49H9z"/><path d="M32 7v42M20 28h24"/>',
  helix: '<path d="M20 10c0 14 24 14 24 28S20 52 20 52"/><path d="M44 10c0 14-24 14-24 28s24 14 24 14"/>',
  grid: '<rect x="10" y="10" width="18" height="18"/><rect x="36" y="10" width="18" height="18"/><rect x="10" y="36" width="18" height="18"/><rect x="36" y="36" width="18" height="18"/>',
  wave: '<path d="M8 36c8-16 16-16 24 0s16 16 24 0"/><path d="M8 20c8-10 16-10 24 0s16 10 24 0"/>',
  hex: '<path d="M32 6 54 19v26L32 58 10 45V19z"/><circle cx="32" cy="32" r="7"/>',
  disc: '<circle cx="32" cy="32" r="21"/><circle cx="32" cy="32" r="4"/><path d="M32 11v9M32 44v9"/>',
  shard: '<path d="M32 6 48 32 32 58 16 32z"/><path d="M16 32h32"/>',
  key: '<circle cx="23" cy="32" r="10"/><path d="M33 32h23M48 32v9M56 32v7"/>'
}

const DEMO_GAMES = [
  {
    id: 'elden', title: 'Elden Ring', developer: 'FromSoftware', year: 2022,
    appId: '1245620', buildId: '18456314', status: 'installed', installed: true, owned: false, featured: true, tile: 't2x2',
    accent: '#C23B22', glyph: 'ring', updated: '2 h ago',
    tags: ['souls-like', 'open world', 'dark fantasy'],
    depots: [
      { id: '1245621', name: 'Elden Ring — base', size: 63.9, key: '0baf5626b33478965691a969199f18aa40f58e1743735ca62ca3056943cd6792', gid: '7680684986696390415' },
      { id: '1245622', name: 'Elden Ring — DLC data', size: 4.6, key: 'acacd5cf0fb8e235d94382797f2dfb797c07e6a5df792836a7bf034ecc8a1d80', gid: '2054183504709005948' }
    ],
    dlc: [{ appId: '1259760', name: 'Shadow of the Erdtree' }]
  },
  {
    id: 'bg3', title: "Baldur's Gate 3", developer: 'Larian Studios', year: 2023,
    appId: '1086940', buildId: '17932104', status: 'ready', installed: false, owned: false, featured: true, tile: 't2x1',
    accent: '#8C0095', glyph: 'prism', updated: '4 h ago',
    tags: ['rpg', 'turn-based', 'co-op'],
    depots: [
      { id: '1086941', name: "Baldur's Gate 3 — base", size: 142.1, key: '0e5dd8b43415e6b5fad0be29c67a420d39dab654b270d01cc2ed86aeaf009897', gid: '2488721615174967870' },
      { id: '1086942', name: 'Language pack', size: 2.7, key: '6b3469834fc6d42ace327f602ca7bb3308689e39f5ae475cf21ef7b4969d8935', gid: '3797821284027531825' }
    ],
    dlc: []
  },
  {
    id: 'hades2', title: 'Hades II', developer: 'Supergiant Games', year: 2024,
    appId: '1145350', buildId: '17304882', status: 'needs-key', installed: false, owned: false, featured: true, tile: 't1x2',
    accent: '#008A00', glyph: 'shard', updated: '6 h ago',
    tags: ['action roguelike', 'mythology', 'indie'],
    depots: [
      { id: '1145351', name: 'Hades II — base', size: 9.8, key: 'bf766a60b308341fc401ca47449e39e1eb64dfb140081d404d6c87791751d9e8', gid: '8110277814307777462' },
      { id: '1145352', name: 'Bonus soundtrack', size: 0.4, key: null, gid: '8993667808327121564' }
    ],
    dlc: []
  },
  {
    id: 'cyberpunk', title: 'Cyberpunk 2077', developer: 'CD PROJEKT RED', year: 2020,
    appId: '1091500', buildId: '17640033', status: 'ready', installed: false, owned: false, featured: true, tile: 't2x1',
    accent: '#D80073', glyph: 'grid', updated: '9 h ago',
    tags: ['cyberpunk', 'open world', 'fps'],
    depots: [
      { id: '1091501', name: 'Cyberpunk 2077 — base', size: 78.9, key: '7fef4143cc4444f376c230e5723d34d11627b017687115afd709e3334aa49624', gid: '3774370779412272289' },
      { id: '1091502', name: 'REDmod tools', size: 7.5, key: '964890e8f9b966ea2a7cb49b739fba992c4a246defbdc15029497ee80b9a36b5', gid: '8959477508082534538' }
    ],
    dlc: [{ appId: '2138330', name: 'Phantom Liberty' }]
  },
  {
    id: 'ror2', title: 'Risk of Rain 2', developer: 'Hopoo Games', year: 2020,
    appId: '632360', buildId: '16123980', status: 'installed', installed: true, owned: true, featured: false, tile: 't1x1',
    accent: '#008299', glyph: 'helix', updated: 'yesterday',
    tags: ['roguelike', 'co-op', 'shooter'],
    depots: [
      { id: '632361', name: 'Risk of Rain 2 — base', size: 4.2, key: '4356534c90e96bddbe204082378f5bfbd72edeaec88266920527ede2df9dd71c', gid: '5859347729115854006' }
    ],
    dlc: []
  },
  {
    id: 'drg', title: 'Deep Rock Galactic', developer: 'Ghost Ship Games', year: 2020,
    appId: '548430', buildId: '15983112', status: 'ready', installed: false, owned: false, featured: false, tile: 't2x1',
    accent: '#FA6800', glyph: 'cube', updated: 'yesterday',
    tags: ['co-op', 'fps', 'procedural'],
    depots: [
      { id: '548431', name: 'Deep Rock Galactic — base', size: 3.4, key: 'e63dc7c0e0a2d63e9d0df820f30bae68adc9be183fa0bdc7c43a7bf80dc4e224', gid: '5863080139693169371' }
    ],
    dlc: []
  },
  {
    id: 'stardew', title: 'Stardew Valley', developer: 'ConcernedApe', year: 2016,
    appId: '413150', buildId: '17234018', status: 'registered', installed: false, owned: false, featured: false, tile: 't1x1',
    accent: '#60A917', glyph: 'grid', updated: '2 days ago',
    tags: ['farming sim', 'pixel', 'relaxing'],
    depots: [
      { id: '413151', name: 'Stardew Valley — base', size: 1.2, key: '36c34b51ad68019e905f5fe9b6ee2460461839bb4ea814deeb27c0f5870da09a', gid: '6563760767147878054' }
    ],
    dlc: []
  },
  {
    id: 'factorio', title: 'Factorio', developer: 'Wube Software', year: 2020,
    appId: '427520', buildId: '18005216', status: 'no-build', installed: false, owned: false, featured: false, tile: 't1x1',
    accent: '#E3C800', glyph: 'hex', updated: '3 days ago',
    tags: ['automation', 'base building', 'strategy'],
    depots: [
      { id: '427521', name: 'Factorio — base', size: 4.8, key: '82b9963ed4a4607cd19bb59c2dcda3dc2e3048c5f0096d29413e34fb65c7e214', gid: null }
    ],
    dlc: [{ appId: '645730', name: 'Space Age' }]
  },
  {
    id: 'hollow', title: 'Hollow Knight', developer: 'Team Cherry', year: 2017,
    appId: '367520', buildId: '15278912', status: 'installed', installed: true, owned: true, featured: false, tile: 't1x2',
    accent: '#1BA1E2', glyph: 'shard', updated: '3 days ago',
    tags: ['metroidvania', 'platformer', '2d'],
    depots: [
      { id: '367521', name: 'Hollow Knight — base', size: 7.5, key: 'af1f0e3dc6ca1fff420613930cb0d67a5855150ef20a805d3630e99a3ea1d571', gid: '1378972138293554776' }
    ],
    dlc: []
  },
  {
    id: 'portal2', title: 'Portal 2', developer: 'Valve', year: 2011,
    appId: '620', buildId: '14981123', status: 'ready', installed: false, owned: true, featured: false, tile: 't1x1',
    accent: '#00ABA9', glyph: 'ring', updated: '4 days ago',
    tags: ['puzzle', 'co-op', 'first-person'],
    depots: [
      { id: '621', name: 'Portal 2 — base', size: 11.4, key: '2699e885090db9b07d604e0ad00bf2a0e9c27f91038800e253c74677403164a0', gid: '2974777751465872247' }
    ],
    dlc: []
  },
  {
    id: 'disco', title: 'Disco Elysium', developer: 'ZA/UM', year: 2019,
    appId: '632470', buildId: '15702338', status: 'needs-key', installed: false, owned: false, featured: false, tile: 't1x1',
    accent: '#A20025', glyph: 'disc', updated: '5 days ago',
    tags: ['detective', 'story rich', 'crpg'],
    depots: [
      { id: '632471', name: 'Disco Elysium — base', size: 9.1, key: 'a7dd6cba6bee74f689a968740861db50f1c0f041ba570582338545201b2a8a20', gid: '6897590123573679404' },
      { id: '632472', name: 'Language pack', size: 0.6, key: null, gid: '4849317888440063947' }
    ],
    dlc: []
  },
  {
    id: 'celeste', title: 'Celeste', developer: 'Maddy Makes Games', year: 2018,
    appId: '504230', buildId: '14450192', status: 'ready', installed: false, owned: false, featured: false, tile: 't1x1',
    accent: '#AA00FF', glyph: 'wave', updated: '1 week ago',
    tags: ['platformer', 'indie', 'soundtrack'],
    depots: [
      { id: '504231', name: 'Celeste — base', size: 1.2, key: '852f6801394115277df7e7298fbc6573c257f29ea41d26d8c31cd506091c370e', gid: '2690539352691467569' }
    ],
    dlc: []
  },
  {
    id: 'tabletop', title: 'Tabletop Simulator', developer: 'Berserk Games', year: 2015,
    appId: '286160', buildId: '15118840', status: 'installed', installed: true, owned: true, featured: false, tile: 't2x1',
    accent: '#0050EF', glyph: 'grid', updated: '1 week ago',
    tags: ['board game', 'sandbox', 'physics'],
    depots: [
      { id: '286161', name: 'Tabletop Simulator — base', size: 3.9, key: '0e72752950792fb8c80b0268fb623b66503c88d2e2ccede57c8422dfb159336e', gid: '2362897436156866247' }
    ],
    dlc: []
  },
  {
    id: 'ftk', title: 'For The King', developer: 'IronOak Games', year: 2018,
    appId: '527230', buildId: '14330622', status: 'no-build', installed: false, owned: false, featured: false, tile: 't1x1',
    accent: '#647687', glyph: 'cube', updated: '2 weeks ago',
    tags: ['turn-based', 'roguelike', 'co-op'],
    depots: [
      { id: '527231', name: 'For The King — base', size: 3.1, key: '827bd6b2eeecff5c88bffabb05c3d87d2726928b95ca5c524fe7c174535989b4', gid: null }
    ],
    dlc: []
  }
]

const PIVOTS = [
  { id: 'catalog', label: 'all' },
  { id: 'installed', label: 'installed' },
  { id: 'registered', label: 'registered' },
  { id: 'ready', label: 'ready' },
  { id: 'needs-key', label: 'needs key' }
]

const STATUS_LABEL = {
  installed: 'installed',
  registered: 'registered',
  owned: 'owned',
  ready: 'ready',
  'needs-key': 'needs key',
  'no-build': 'no build',
  available: 'not added',
  installing: 'downloading'
}

/* ------------------------------- state ------------------------------- */

const query = ref('')
const pivot = ref('catalog')
const activeId = ref(null)
const selected = ref(new Set())
const excluded = ref(new Set())
const revealed = ref(new Set())
const flippedQueue = ref([])
const accent = ref(ACCENTS[0].hex)
const hubcapKey = ref('')
const keyDraft = ref('')
const keyVisible = ref(false)
const bridge = ref(false)
const bridgeVersion = ref('')
const bridgeToken = ref('')
const paired = ref(false)
const probed = ref(false)
const probing = ref(false)

// Per-game artwork fallback step (kept outside objects so demo & live behave identically)
const thumbStep = reactive({})
const bannerStep = reactive({})

// Live data (Epsilon)
const demo = ref(false)
const live = ref(false)
const loading = ref(false)
const library = ref([])
const hits = ref([])
const searching = ref(false)
const searchError = ref('')
const syncedAt = ref(0)
const cachedAt = ref(0)
const hubcap = ref(null)
const store = ref(null)
const registering = ref(false)
const settingsOpen = ref(false)
const bridgeOpen = ref(false)
const form = ref({
  gateway: 'auto',
  fetchDlcs: true,
  addDelisted: true,
  notifications: 'auto',
  storePatch: true
})
const toasts = ref([])
const searchEl = ref(null)

let toastSeq = 0
let liveTimer = null
let searchTimer = null
let searchSeq = 0
let searchController = null
const toastTimers = new Set()

/* ----------------------------- artwork ------------------------------- */

function cdnUrl(appId, file) {
  return `https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${appId}/${file}`
}

function legacyCdnUrl(appId, file) {
  return `https://cdn.cloudflare.steamstatic.com/steam/apps/${appId}/${file}`
}

// Pick Steam CDN artwork that matches each tile's aspect ratio so portraits
// don't get landscape-cropped and wides don't get blurred. Also honours any
// custom art URL (e.g. from a local SteamGridDB proxy in Epsilon) first.
function tileArtChain(game) {
  const out = []
  if (game.customArt) out.push(game.customArt)
  const id = game.appId
  if (!id) return out
  if (game.tile === 't1x2') {
    out.push(
      cdnUrl(id, 'library_600x900_2x.jpg'),
      legacyCdnUrl(id, 'library_600x900.jpg'),
      cdnUrl(id, 'capsule_616x353.jpg'),
      legacyCdnUrl(id, 'header.jpg')
    )
  } else if (game.tile === 't2x2') {
    out.push(
      cdnUrl(id, 'library_hero.jpg'),
      cdnUrl(id, 'capsule_616x353.jpg'),
      legacyCdnUrl(id, 'header.jpg')
    )
  } else if (game.tile === 't1x1') {
    out.push(
      cdnUrl(id, 'capsule_616x353.jpg'),
      legacyCdnUrl(id, 'header.jpg'),
      legacyCdnUrl(id, 'library_600x900.jpg')
    )
  } else {
    out.push(
      legacyCdnUrl(id, 'header.jpg'),
      cdnUrl(id, 'capsule_616x353.jpg'),
      cdnUrl(id, 'library_hero.jpg')
    )
  }
  return out
}

function bannerArtChain(game) {
  const out = []
  if (game.customHero) out.push(game.customHero)
  if (game.customArt) out.push(game.customArt)
  const id = game.appId
  if (!id) return out
  out.push(
    cdnUrl(id, 'library_hero.jpg'),
    legacyCdnUrl(id, 'header.jpg'),
    cdnUrl(id, 'capsule_616x353.jpg')
  )
  return out
}

function tileThumb(game) {
  if (!game.live && !demo.value) return ''
  const chain = tileArtChain(game)
  const idx = thumbStep[game.id] || 0
  return chain[idx] || ''
}

function nextTileThumb(game) {
  thumbStep[game.id] = (thumbStep[game.id] || 0) + 1
}

function bannerThumb(game) {
  const chain = bannerArtChain(game)
  const idx = bannerStep[game.id] || 0
  return chain[idx] || ''
}

function nextBannerThumb(game) {
  bannerStep[game.id] = (bannerStep[game.id] || 0) + 1
}

/* ----------------------------- computed ------------------------------ */

const flipped = computed(() => new Set(flippedQueue.value))
const searchMode = computed(() => !demo.value && paired.value && query.value.trim().length >= 2)

const libraryByAppId = computed(() => {
  const map = new Map()
  for (const g of library.value) map.set(g.appId, g)
  return map
})

const allGames = computed(() => {
  if (demo.value) return DEMO_GAMES
  const map = new Map()
  for (const g of library.value) map.set(g.id, g)
  for (const g of hits.value) {
    if (!map.has(g.id)) map.set(g.id, g)
  }
  return Array.from(map.values())
})

function matchesQuery(game, q) {
  if (!q) return true
  const hay = [game.title, game.appId, game.developer, game.buildId, ...(game.tags || [])]
    .join(' ')
    .toLowerCase()
  return hay.includes(q)
}

function matchesPivot(game, p) {
  if (p === 'catalog') return true
  if (p === 'installed') return Boolean(game.installed) || game.status === 'installed'
  if (p === 'registered') {
    return game.status === 'registered' || game.status === 'installed' || game.status === 'ready'
  }
  return game.status === p
}

// Library matches during live search: direct text match OR any library game
// whose AppID Steam returned for the query (catches aliases like "gtav").
const matchedLibrary = computed(() => {
  if (!searchMode.value) return []
  const q = query.value.trim().toLowerCase()
  const hitAppIds = new Set(hits.value.map((h) => h.appId))
  return library.value.filter((g) => matchesQuery(g, q) || hitAppIds.has(g.appId))
})

// Store results excluding anything already in your library (so no duplicates).
const storeHits = computed(() => {
  if (!searchMode.value) return []
  return hits.value.filter((h) => !libraryByAppId.value.has(h.appId))
})

const baseFiltered = computed(() => {
  const list = demo.value ? DEMO_GAMES : library.value
  const q = query.value.trim().toLowerCase()
  return list.filter((g) => matchesPivot(g, pivot.value) && matchesQuery(g, q))
})

const isSearching = computed(() => query.value.trim().length > 0 || pivot.value !== 'catalog')

const sections = computed(() => {
  if (demo.value) {
    if (!baseFiltered.value.length) return []
    if (isSearching.value) {
      const owned = baseFiltered.value.filter((g) => g.installed || g.status === 'registered')
      const other = baseFiltered.value.filter((g) => !(g.installed || g.status === 'registered'))
      const out = []
      if (query.value.trim() && owned.length) {
        out.push({ id: 'owned', label: 'in your library', games: owned })
        if (other.length) out.push({ id: 'results', label: 'catalog matches', games: other })
        return out
      }
      return [{ id: 'results', label: 'results', games: baseFiltered.value }]
    }
    const featured = baseFiltered.value.filter((g) => g.featured)
    const rest = baseFiltered.value.filter((g) => !g.featured)
    const out = []
    if (featured.length) out.push({ id: 'featured', label: 'featured & installed', games: featured })
    if (rest.length) out.push({ id: 'catalog', label: 'catalog', games: rest })
    return out
  }

  if (searchMode.value) {
    const out = []
    if (matchedLibrary.value.length) {
      out.push({ id: 'owned', label: 'registered in your library', games: matchedLibrary.value })
    }
    if (storeHits.value.length) {
      out.push({ id: 'results', label: 'steam store', games: storeHits.value })
    }
    return out
  }

  if (!baseFiltered.value.length) return []
  return [
    {
      id: 'library',
      label: paired.value ? 'your library' : 'cached library',
      games: baseFiltered.value
    }
  ]
})

const visibleGames = computed(() => sections.value.flatMap((s) => s.games))
const activeGame = computed(() => allGames.value.find((g) => g.id === activeId.value) || null)
const selectedGames = computed(() => allGames.value.filter((g) => selected.value.has(g.id)))
const selectedSize = computed(() => selectedGames.value.reduce((n, g) => n + totalSize(g), 0))
const installedCount = computed(() => {
  const source = demo.value ? DEMO_GAMES : library.value
  return source.filter((g) => g.installed || g.status === 'installed').length
})
const activeDepots = computed(() => (activeGame.value ? includedDepots(activeGame.value) : []))
const hasKey = computed(() => hubcapKey.value.trim().length >= 8 || Boolean(hubcap.value?.ok))

function countFor(p) {
  const source = demo.value ? DEMO_GAMES : library.value
  return source.filter((g) => matchesPivot(g, p)).length
}

/* ------------------------------ helpers ------------------------------ */

function hashOf(text) {
  let hash = 0
  for (const ch of String(text)) hash = (hash * 31 + ch.charCodeAt(0)) % 100003
  return hash
}

function accentFor(appId) {
  return ACCENT_CYCLE[hashOf(appId) % ACCENT_CYCLE.length]
}

// Metro mosaic sizing based on status/priority + deterministic visual rhythm
// rather than title length (so short names still get hero/wide tiles cleanly).
function pickTileSize(entry, index = 0) {
  if (entry.downloading) return 't2x2'
  const h = hashOf(entry.app_id ?? entry.appId ?? index)
  if (entry.installed) {
    // Installed titles carry the grid, but they still need a mix of shapes or
    // the whole page becomes one weight of wide tiles.
    if (h % 11 === 0) return 't2x2'
    if (h % 4 === 0) return 't1x2'
    if (h % 3 === 0) return 't1x1'
    return 't2x1'
  }
  const slot = h % 10
  if (slot === 0) return 't2x2'
  if (slot <= 3) return 't2x1'
  if (slot <= 5) return 't1x2'
  return 't1x1'
}

function totalSize(game) {
  if (typeof game.sizeGB === 'number' && game.sizeGB > 0) return game.sizeGB
  return (game.depots || []).reduce((n, d) => n + (d.size || 0), 0)
}

function sizeLabel(game) {
  const gb = totalSize(game)
  return gb > 0 ? fmtGB(gb) : '—'
}

function keyCountOf(game) {
  if (typeof game.keyCount === 'number') return game.keyCount
  return (game.depots || []).filter((d) => d.key || d.hasKey).length
}

function depotOf(game, depot) {
  return `${game.id}:${depot.id}`
}

function depotOn(game, depot) {
  return !excluded.value.has(depotOf(game, depot))
}

function includedDepots(game) {
  return (game.depots || []).filter((d) => depotOn(game, d))
}

function isLight(game) {
  const hex = game.accent.replace('#', '')
  const channels = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
  const lin = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  const luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
  return 1.05 / (luminance + 0.05) < 4.5
}

function maskKey(key) {
  return `${key.slice(0, 6)}…${key.slice(-6)}`
}

function fmtGB(n) {
  return `${n.toFixed(1)} GB`
}

function toggleSet(setRef, value) {
  const next = new Set(setRef.value)
  next.has(value) ? next.delete(value) : next.add(value)
  setRef.value = next
}

function toggleDepot(game, depot) {
  toggleSet(excluded, depotOf(game, depot))
}

function toggleAllDepots(game) {
  const allOn = game.depots.every((d) => depotOn(game, d))
  const next = new Set(excluded.value)
  for (const d of game.depots) {
    const key = depotOf(game, d)
    if (allOn) next.add(key)
    else next.delete(key)
  }
  excluded.value = next
}

function toggleReveal(id) {
  toggleSet(revealed, id)
}

function toggleSelect(id) {
  toggleSet(selected, id)
}

function toggleAllVisible() {
  const ids = visibleGames.value.map((g) => g.id)
  if (!ids.length) return
  const allOn = ids.every((id) => selected.value.has(id))
  const next = new Set(selected.value)
  ids.forEach((id) => (allOn ? next.delete(id) : next.add(id)))
  selected.value = next
}

function clearSelection() {
  selected.value = new Set()
}

function onTile(game) {
  if (selected.value.size > 0) {
    toggleSelect(game.id)
    return
  }
  activeId.value = activeId.value === game.id ? null : game.id
}

function closeFlyout() {
  activeId.value = null
}

function toast(title, sub = '') {
  const id = ++toastSeq
  toasts.value = [...toasts.value, { id, title, sub }]
  const t = setTimeout(() => {
    toasts.value = toasts.value.filter((x) => x.id !== id)
    toastTimers.delete(t)
  }, 4400)
  toastTimers.add(t)
}

function closePopovers() {
  bridgeOpen.value = false
  settingsOpen.value = false
}

function toggleBridgePopover() {
  const next = !bridgeOpen.value
  closePopovers()
  bridgeOpen.value = next
}

function toggleSettingsPopover() {
  const next = !settingsOpen.value
  closePopovers()
  settingsOpen.value = next
}

function pickAccent(hex) {
  accent.value = hex
  toast('accent updated', hex)
}

function selectPivot(id) {
  pivot.value = id
  if (searchMode.value) query.value = ''
}

/* ------------------------------ actions ------------------------------ */

function applyKey() {
  const value = keyDraft.value.trim()
  if (value.length < 8) {
    toast('key too short', 'Hubcap keys are longer than that.')
    return
  }
  hubcapKey.value = value
  keyDraft.value = ''
  try {
    localStorage.setItem('at0m.hubcap', value)
  } catch {
    /* private mode */
  }
  toast('hubcap key saved', 'Stored in this browser only.')
}

function forgetKey() {
  hubcapKey.value = ''
  keyDraft.value = ''
  try {
    localStorage.removeItem('at0m.hubcap')
  } catch {
    /* ignore */
  }
  toast('key removed from this browser')
}

async function probeBridge({ silent = false } = {}) {
  if (probing.value) return
  probing.value = true
  if (!silent) {
    closePopovers()
    bridgeOpen.value = true
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 2500)
  try {
    const res = await fetch(`${BRIDGE_BASE}/v1/session`, {
      signal: controller.signal,
      mode: 'cors',
      cache: 'no-store',
      headers: bridgeToken.value ? { Authorization: `Bearer ${bridgeToken.value}` } : {}
    })
    const body = await res.json().catch(() => ({}))
    bridge.value = true
    probed.value = true
    bridgeVersion.value = body.version || ''
    paired.value = res.ok

    if (paired.value) {
      const count = body.library_count ?? 0
      toast('console paired', `epsilon ${bridgeVersion.value} · ${count} ${count === 1 ? 'game' : 'games'}`)
      await Promise.all([loadLibrary(), loadHubcap(), loadSettings(), loadStore()])
    } else if (res.status === 401) {
      toast('bridge found', 'Paste the console token from ASSella → Companions → Epsilon.')
    } else if (res.status === 404) {
      toast('epsilon needs an update', 'This build has no console API yet — update or restart it.')
    } else {
      toast('bridge refused', `Epsilon replied ${res.status}. Restart it so it accepts this page.`)
    }
  } catch {
    bridge.value = false
    paired.value = false
    probed.value = true
    bridgeVersion.value = ''
    if (!silent) {
      toast('bridge not reachable', 'Epsilon is not running on 127.0.0.1:1952 — export instead.')
    }
  } finally {
    clearTimeout(timer)
    probing.value = false
  }
}

function submitSearch() {
  const term = query.value.trim()
  if (term.length >= 2 && paired.value && !demo.value) runSearch(term)
}

function focusSearch() {
  searchEl.value?.focus()
  searchEl.value?.select?.()
}

function saveBridgeToken() {
  const token = bridgeToken.value.trim()
  try {
    if (token) localStorage.setItem('at0m.token', token)
    else localStorage.removeItem('at0m.token')
  } catch {
    /* storage unavailable */
  }
  if (token) probeBridge()
}

/* --------------------------- epsilon data ---------------------------- */

function relTime(seconds) {
  if (!seconds) return ''
  const diff = Date.now() / 1000 - seconds
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  return `${Math.round(diff / 86400)} d ago`
}

function toGame(entry, index = 0) {
  const keys = Number(entry.key_count || 0)
  const installed = Boolean(entry.installed)
  const owned = Boolean(entry.owned || entry.source === 'steam')
  const registered = Boolean(entry.registered ?? !owned)

  let status = 'registered'
  if (entry.downloading) status = 'installing'
  else if (installed) status = 'installed'
  else if (owned && !registered) status = 'owned'
  else if (!keys) status = 'needs-key'
  else if (entry.dlc_only) status = 'no-build'
  else status = 'ready'

  const tags = []
  if (installed) tags.push('installed')
  if (owned) tags.push('steam owned')
  else if (registered) tags.push('at0-m')

  return {
    id: `app${entry.app_id}`,
    appId: String(entry.app_id),
    title: entry.name || `App ${entry.app_id}`,
    developer: entry.developer || '',
    year: entry.year || null,
    buildId: entry.buildid || '',
    status,
    featured: false,
    tile: pickTileSize(entry, index),
    accent: accentFor(entry.app_id),
    glyph: 'cube',
    updated: relTime(entry.updated_at),
    tags,
    customArt: entry.art || entry.thumb || '',
    customHero: entry.hero || '',
    live: true,
    inLibrary: true,
    installed,
    owned,
    registered,
    downloading: Boolean(entry.downloading),
    sizeGB: entry.size_on_disk ? entry.size_on_disk / 1024 ** 3 : 0,
    keyCount: keys,
    detailState: 'idle',
    depots: (entry.depots || []).map((id) => ({
      id: String(id),
      name: `depot ${id}`,
      size: 0,
      key: null,
      gid: null,
      hasKey: false,
      enabled: true
    })),
    dlc: (entry.dlc_appids || []).map((id) => ({ appId: String(id), name: `DLC ${id}`, size: 0 }))
  }
}

function toHit(row, index = 0) {
  const owned = Boolean(row.owned)
  const installed = Boolean(row.installed)
  return {
    id: `app${row.app_id}`,
    appId: String(row.app_id),
    title: row.title || row.name || `App ${row.app_id}`,
    developer: row.developer || '',
    year: row.year || null,
    buildId: '',
    status: installed ? 'installed' : owned ? 'owned' : 'available',
    featured: false,
    tile: index === 0 ? 't2x1' : hashOf(row.app_id) % 4 === 0 ? 't1x2' : 't2x1',
    accent: accentFor(row.app_id),
    glyph: 'cube',
    updated: '',
    tags: owned ? ['steam owned'] : ['store'],
    customArt: row.art || row.thumb || '',
    customHero: row.hero || '',
    live: true,
    inLibrary: false,
    installed,
    owned,
    registered: false,
    sizeGB: 0,
    keyCount: 0,
    detailState: 'idle',
    depots: [],
    dlc: []
  }
}

async function bridgeFetch(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const headers = { ...(options.headers || {}) }
  if (bridgeToken.value) headers.Authorization = `Bearer ${bridgeToken.value}`
  if (method !== 'GET') headers['X-At0m-Client'] = '1'
  if (options.body) headers['Content-Type'] = 'application/json'

  const res = await fetch(`${BRIDGE_BASE}${path}`, {
    ...options,
    method,
    headers,
    mode: 'cors',
    cache: 'no-store'
  })
  if (!res.ok) {
    let message = `Epsilon replied ${res.status}`
    try {
      const body = await res.json()
      if (body.error) message = body.error
    } catch {
      /* not json */
    }
    const error = new Error(message)
    error.status = res.status
    throw error
  }
  return res.json()
}

// Public SteamCMD metadata lookup — zero keys, never touches Hubcap.
async function fetchSteamCmdInfo(appId) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 5000)
  try {
    const res = await fetch(`https://api.steamcmd.net/v1/info/${appId}`, {
      signal: controller.signal,
      mode: 'cors'
    })
    if (!res.ok) return null
    const json = await res.json()
    const info = json?.data?.[String(appId)]
    if (!info) return null

    const common = info.common || {}
    const extended = info.extended || {}
    const rawDepots = info.depots || {}

    const associations = Object.values(common.associations || {})
    const developer =
      associations.find((a) => a?.type === 'developer')?.name || extended.developer || ''
    const relTs = Number(common.steam_release_date || 0)
    const year = relTs ? new Date(relTs * 1000).getUTCFullYear() : null
    const buildId = rawDepots.branches?.public?.buildid
      ? String(rawDepots.branches.public.buildid)
      : ''

    const depots = []
    for (const [id, d] of Object.entries(rawDepots)) {
      if (!/^\d+$/.test(id) || !d || typeof d !== 'object') continue
      const maxSize = Number(d.maxsize || d.manifests?.public?.size || 0)
      const oslist = d.config?.oslist ? ` (${d.config.oslist})` : ''
      const lang = d.config?.language ? ` · ${d.config.language}` : ''
      depots.push({
        id,
        name: `${d.name || `depot ${id}`}${oslist}${lang}`,
        size: maxSize > 0 ? maxSize / 1024 ** 3 : 0,
        key: null,
        gid: d.manifests?.public?.gid ? String(d.manifests.public.gid) : null,
        hasKey: false,
        enabled: false
      })
    }

    const dlc = String(extended.listofdlc || '')
      .split(',')
      .map((s) => s.trim())
      .filter((s) => /^\d+$/.test(s))
      .map((id) => ({ appId: id, name: `DLC ${id}`, size: 0 }))

    return { title: common.name || '', developer, year, buildId, depots, dlc }
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

function saveCache(games) {
  try {
    const rows = games.slice(0, CACHE_MAX).map((g) => [
      g.appId,
      g.title,
      g.installed ? 1 : 0,
      g.keyCount || 0,
      g.buildId || '',
      g.owned ? 1 : 0
    ])
    localStorage.setItem(CACHE_KEY, JSON.stringify({ v: CACHE_VERSION, at: Date.now(), rows }))
  } catch {
    /* quota or private mode */
  }
}

function restoreCache() {
  try {
    const data = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null')
    if (!data || !Array.isArray(data.rows) || !data.rows.length) return false
    if (data.v !== CACHE_VERSION && data.v !== 1) return false
    library.value = data.rows.map(([appId, title, installed, keyCount, buildId, owned], i) =>
      toGame(
        {
          app_id: appId,
          name: title,
          installed,
          key_count: keyCount,
          buildid: buildId,
          owned: Boolean(owned)
        },
        i
      )
    )
    cachedAt.value = Number(data.at) || 0
    live.value = true
    return true
  } catch {
    return false
  }
}

async function loadLibrary() {
  if (!paired.value) return
  loading.value = true
  try {
    const body = await bridgeFetch('/v1/library')
    library.value = (body.games || []).map((entry, i) => toGame(entry, i))
    live.value = true
    syncedAt.value = Date.now()
    searchError.value = ''
    saveCache(library.value)
  } catch (error) {
    searchError.value = error.message
  } finally {
    loading.value = false
  }
}

// The Store button patch is Uran's job; Epsilon owns the switch and can tell
// us whether Steam is even exposing the debugging port it needs.
async function loadStore() {
  if (!paired.value) return
  try {
    const body = await bridgeFetch('/v1/store')
    store.value = body
    form.value.storePatch = body.enabled !== false
  } catch {
    store.value = null
  }
}

function storeCefLabel() {
  const cef = store.value?.cef
  if (!cef) return 'unknown'
  if (!cef.available) return 'no debug port'
  if (!cef.store_tab) return `port ${cef.port} · open the store page`
  return `port ${cef.port} · store page detected`
}

async function loadSettings() {
  if (!paired.value) return
  try {
    const body = await bridgeFetch('/v1/settings')
    const stored = body.settings || {}
    form.value = {
      gateway: stored.hubcap_gateway || 'auto',
      fetchDlcs: stored.fetch_dlcs_on_inject !== false,
      addDelisted: stored.add_delisted_dlcs !== false,
      notifications: stored.notification_mode || 'auto',
      storePatch: stored.steam_store_enabled !== false
    }
  } catch {
    /* non-fatal */
  }
}

async function saveSetting(key, value) {
  if (!paired.value) return
  try {
    await bridgeFetch('/v1/settings', { method: 'POST', body: JSON.stringify({ [key]: value }) })
    toast('setting saved', `${key.replace(/_/g, ' ')} → ${value}`)
  } catch (error) {
    toast('setting rejected', error.message)
    await loadSettings()
  }
}

async function loadHubcap() {
  if (!paired.value) return
  try {
    hubcap.value = await bridgeFetch('/v1/hubcap')
  } catch {
    hubcap.value = null
  }
}

async function runSearch(term) {
  if (searchController) searchController.abort()
  searchController = new AbortController()
  const seq = ++searchSeq
  searching.value = true
  try {
    const body = await bridgeFetch(`/v1/search?q=${encodeURIComponent(term)}`, {
      signal: searchController.signal
    })
    if (seq !== searchSeq) return
    hits.value = (body.results || []).map((row, i) => toHit(row, i))
    searchError.value = ''
  } catch (error) {
    if (error.name === 'AbortError' || seq !== searchSeq) return
    hits.value = []
    searchError.value = error.message
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}

// Preview details WITHOUT touching Hubcap:
// - Library games query Epsilon's local state (/v1/app/:id, no ?network=1)
// - Public metadata (dev, year, build, depot sizes, DLCs) comes from SteamCMD API
async function loadDetail(game, force = false) {
  if (!game || !game.live) return
  if (!force && (game.detailState === 'loading' || game.detailState === 'loaded')) return
  game.detailState = 'loading'

  const localPromise =
    paired.value && game.inLibrary
      ? bridgeFetch(`/v1/app/${game.appId}`).catch(() => null)
      : Promise.resolve(null)
  const publicPromise = fetchSteamCmdInfo(game.appId)

  const [detail, pub] = await Promise.all([localPromise, publicPromise])

  if (!detail && !pub) {
    game.detailState = 'error'
    return
  }

  const pubDepotMap = new Map((pub?.depots || []).map((d) => [d.id, d]))

  if (pub) {
    if (pub.developer && !game.developer) game.developer = pub.developer
    if (pub.year && !game.year) game.year = pub.year
    if (pub.buildId && !game.buildId) game.buildId = pub.buildId
    if (!detail && pub.depots.length) {
      game.depots = pub.depots
      if (!game.sizeGB) {
        game.sizeGB = pub.depots.reduce((n, d) => n + (d.size || 0), 0)
      }
    }
    if (!detail && pub.dlc.length && !game.dlc.length) {
      game.dlc = pub.dlc
    }
  }

  if (detail) {
    game.buildId = detail.buildid || game.buildId
    game.keyCount = Number(detail.key_count ?? game.keyCount)
    if (detail.installed) game.installed = true
    if (detail.owned) game.owned = true
    // Union of three sources, because none of them is complete on its own:
    // SteamCMD knows every depot (and which have no key yet), the local record
    // knows what is projected into config.yaml, and the file itself is the only
    // thing that knows which keys actually resolve. Replacing instead of merging
    // hid the shared depots and the unkeyed game depot.
    const byId = new Map()
    const put = (id, patch) => {
      const key = String(id)
      const base = byId.get(key) || {
        id: key, name: `depot ${key}`, size: 0, key: null, gid: null, hasKey: false, enabled: false
      }
      const clean = {}
      for (const [k, v] of Object.entries(patch)) if (v !== undefined && v !== null) clean[k] = v
      byId.set(key, { ...base, ...clean })
    }
    for (const d of pub?.depots || []) put(d.id, { name: d.name, size: d.size, gid: d.gid })
    for (const id of detail.depots || []) put(id, { enabled: true })
    for (const d of detail.depots_detail || []) {
      put(d.id, {
        hasKey: Boolean(d.has_key),
        enabled: Boolean(d.in_config),
        name: d.name && d.name !== `depot ${d.id}` ? d.name : undefined
      })
    }
    for (const depot of byId.values()) if (depot.enabled) depot.hasKey = true
    if (byId.size) game.depots = [...byId.values()]
    if (Array.isArray(detail.dlcs_detail) && detail.dlcs_detail.length) {
      game.dlc = detail.dlcs_detail.map((d) => ({
        appId: String(d.id),
        name: d.name || `DLC ${d.id}`,
        size: 0,
        enabled: Boolean(d.selected)
      }))
    }
    if (!game.keyCount && game.inLibrary && !game.installed && !game.owned) {
      game.status = 'needs-key'
    }
  }

  game.detailState = 'loaded'
}

/* ----------------------------- exports ------------------------------- */

function luaFor(games) {
  const lines = []
  for (const g of games) {
    const depots = includedDepots(g)
    lines.push(`-- ${g.title} (AppID ${g.appId}) — build ${g.buildId}`)
    lines.push('-- exported from the at0m web console')
    lines.push(`addappid(${g.appId})`)
    for (const d of depots) {
      if (d.key) lines.push(`addappid(${d.id}, 1, "${d.key}")`)
      else lines.push(`addappid(${d.id})`)
      if (d.gid) lines.push(`setManifestid(${d.id}, "${d.gid}")`)
    }
    for (const dlc of g.dlc) lines.push(`addappid(${dlc.appId}) -- ${dlc.name}`)
    lines.push('')
  }
  return lines.join('\n')
}

function yamlFor(games) {
  const apps = []
  const depots = []
  const keys = []
  for (const g of games) {
    apps.push(`  - ${g.appId} # ${g.title}`)
    for (const dlc of g.dlc) apps.push(`  - ${dlc.appId} # ${dlc.name}`)
    for (const d of includedDepots(g)) {
      depots.push(`  - ${d.id} # ${g.title} — ${d.name}`)
      if (d.key) keys.push(`  ${d.id}: "${d.key}"`)
    }
  }
  return [
    '# AT0-M — SLSsteam config.yaml',
    '# Paste into ~/.config/SLSsteam/config.yaml, then let Steam pick it up.',
    '',
    'AdditionalApps:',
    ...apps,
    '',
    'AdditionalDepots:',
    ...depots,
    '',
    'DecryptionKeys:',
    ...keys,
    ''
  ].join('\n')
}

function download(name, text, mime = 'text/plain') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

function exportLua(games) {
  const list = games && games.length ? games : activeGame.value ? [activeGame.value] : []
  if (!list.length) return
  if (!demo.value) {
    toast('keys stay on this machine', 'Registering is the supported path; nothing is exported.')
    return
  }
  const name = list.length === 1 ? `${list[0].appId}.lua` : `at0m-bundle-${list.length}.lua`
  download(name, luaFor(list), 'text/x-lua')
  toast(
    list.length === 1 ? `exported ${name}` : `exported ${list.length} titles`,
    'Import with ASSella → Library → Scan for LUA, or drag it in.'
  )
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text)
    toast('copied', label)
    return
  } catch {
    /* clipboard blocked — fall through */
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  try {
    document.execCommand('copy')
    toast('copied', label)
  } catch {
    toast('copy failed', 'Select the text manually.')
  }
  ta.remove()
}

function copyYaml(games) {
  if (!demo.value) {
    toast('keys stay on this machine', 'Use Register so Epsilon writes config.yaml directly.')
    return
  }
  const list = games && games.length ? games : activeGame.value ? [activeGame.value] : []
  if (!list.length) return
  copyText(yamlFor(list), 'config.yaml snippet')
}

function copyLua(games) {
  if (!demo.value) return
  const list = games && games.length ? games : activeGame.value ? [activeGame.value] : []
  if (!list.length) return
  copyText(luaFor(list), '.lua contents')
}

async function register(games) {
  const list = games && games.length ? games : activeGame.value ? [activeGame.value] : []
  if (!list.length || registering.value) return

  if (demo.value) {
    toast('demo catalog', 'These tiles are sample data — exporting the recipe instead.')
    exportLua(list)
    return
  }
  if (!paired.value) {
    toast('not paired', 'Paste the console token from ASSella → Companions → Epsilon.')
    return
  }

  registering.value = true
  let added = 0
  let failure = ''
  for (const game of list) {
    try {
      // Hubcap is only touched here, inside Epsilon, when registering.
      await bridgeFetch('/v1/register', {
        method: 'POST',
        body: JSON.stringify({ appid: game.appId })
      })
      added += 1
    } catch (error) {
      failure = failure || `${game.title}: ${error.message}`
    }
  }
  registering.value = false

  if (added) {
    toast(
      `registered ${added} ${added === 1 ? 'title' : 'titles'}`,
      'Epsilon resolved keys and updated config.yaml — Steam picks it up on the next scan.'
    )
    query.value = ''
    clearSelection()
    await loadLibrary()

    // Registration can only project depots it has keys for. Say so instead of
    // letting a half-populated game look like a successful one.
    const fresh = library.value.find((g) => g.appId === list[0].appId)
    if (fresh) {
      await loadDetail(fresh, true)
      const missing = fresh.depots.filter((d) => !d.hasKey).length
      if (missing) {
        toast(
          `${missing} depot${missing === 1 ? '' : 's'} without a key`,
          'Hubcap has no bundle for them yet — press refresh keys later.'
        )
      }
    }
  }
  if (failure) toast('registration failed', failure)
}

// Registered games do not need /v1/register again — it would answer
// "already added". This is the only way to pull a newer Hubcap bundle (and any
// depot keys that arrived since the first registration).
async function refetch() {
  const game = activeGame.value
  if (!game || !paired.value || registering.value) return
  registering.value = true
  try {
    await bridgeFetch('/v1/refetch', { method: 'POST', body: JSON.stringify({ appid: game.appId }) })
    toast('keys refreshed', `${game.title} was re-projected from a fresh Hubcap bundle.`)
  } catch (error) {
    toast('refetch failed', error.message)
  } finally {
    registering.value = false
    game.detailLoaded = false
    await loadLibrary()
    await loadDetail(game, true)
  }
}

/* ----------------------------- lifecycle ----------------------------- */

function onDocumentClick(e) {
  if (!e.target.closest?.('.popover-anchor')) closePopovers()
}

function isTyping() {
  const el = document.activeElement
  return (
    !!el &&
    (el.tagName === 'INPUT' ||
      el.tagName === 'TEXTAREA' ||
      el.tagName === 'SELECT' ||
      el.isContentEditable)
  )
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    if (activeId.value) closeFlyout()
    else if (selected.value.size) clearSelection()
    else if (query.value) query.value = ''
    closePopovers()
    return
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    focusSearch()
    return
  }
  if (isTyping() || e.metaKey || e.ctrlKey || e.altKey) return
  if (e.key === '/') {
    e.preventDefault()
    focusSearch()
    return
  }
  if (e.key.length === 1 && /[\w-]/.test(e.key)) {
    searchEl.value?.focus()
  }
}

// Keep at most MAX_FLIPPED live tiles showing their back face at once so the
// mosaic always stays artwork-forward instead of drifting half-flipped.
function cycleLiveTiles() {
  if (document.hidden || activeId.value) return
  const tiles = visibleGames.value
  if (tiles.length < 3) return
  const visibleIds = new Set(tiles.map((g) => g.id))
  const queue = flippedQueue.value.filter((id) => visibleIds.has(id))

  if (queue.length >= MAX_FLIPPED) queue.shift()

  const candidates = tiles.filter((g) => !queue.includes(g.id))
  if (candidates.length) {
    const pick = candidates[Math.floor(Math.random() * candidates.length)]
    queue.push(pick.id)
  }
  flippedQueue.value = queue
}

onMounted(() => {
  if (new URLSearchParams(window.location.search).get('demo') === '1') demo.value = true
  else restoreCache()

  try {
    const savedAccent = localStorage.getItem('at0m.accent')
    if (savedAccent) accent.value = savedAccent
    const savedKey = localStorage.getItem('at0m.hubcap')
    if (savedKey) hubcapKey.value = savedKey
    const savedToken = localStorage.getItem('at0m.token')
    if (savedToken) bridgeToken.value = savedToken
  } catch {
    /* storage unavailable */
  }

  const match = window.location.hash.match(/(?:^#|&)k=([^&]+)/)
  if (match) {
    const token = decodeURIComponent(match[1])
    bridgeToken.value = token
    try {
      localStorage.setItem('at0m.token', token)
    } catch {
      /* storage unavailable */
    }
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    toast('pairing token stored', 'Checking the local bridge…')
    probeBridge()
  } else if (bridgeToken.value) {
    probeBridge({ silent: true })
  }

  document.addEventListener('click', onDocumentClick)
  window.addEventListener('keydown', onKeydown)
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (!reduce) liveTimer = setInterval(cycleLiveTiles, 5400)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  window.removeEventListener('keydown', onKeydown)
  if (liveTimer) clearInterval(liveTimer)
  if (searchTimer) clearTimeout(searchTimer)
  if (searchController) searchController.abort()
  toastTimers.forEach((t) => clearTimeout(t))
  toastTimers.clear()
})

watch(accent, (value) => {
  try {
    localStorage.setItem('at0m.accent', value)
  } catch {
    /* ignore */
  }
})

watch(activeId, (id) => {
  closePopovers()
  if (id) loadDetail(allGames.value.find((g) => g.id === id))
})

watch(query, (value) => {
  if (searchTimer) clearTimeout(searchTimer)
  if (demo.value || !paired.value) return
  const term = value.trim()
  if (term.length < 2) {
    if (searchController) searchController.abort()
    hits.value = []
    searchError.value = ''
    searching.value = false
    return
  }
  searchTimer = setTimeout(() => runSearch(term), 300)
})
</script>

<template>
  <div class="at0m" :style="{ '--accent': accent }">
    <!-- ==================== panorama header ==================== -->
    <header class="hd">
      <div class="hd-left">
        <div class="super">
          <span class="sq" />
          <span>at0-m // web console</span>
          <span class="chip">{{ demo ? 'demo' : paired ? 'live' : live ? 'cached' : 'offline' }}</span>
        </div>
        <h1>at0m</h1>
        <nav class="pivots" aria-label="catalog pivots">
          <button
            v-for="p in PIVOTS"
            :key="p.id"
            type="button"
            class="pivot"
            :class="{ active: pivot === p.id && !searchMode }"
            :aria-pressed="pivot === p.id && !searchMode"
            @click="selectPivot(p.id)"
          >
            {{ p.label }}
            <span v-if="p.id !== 'catalog'" class="pivot-count">
              {{ countFor(p.id) }}
            </span>
          </button>
        </nav>
      </div>

      <div class="hd-right">
        <div class="stats" role="status">
          <div class="stat">
            <span class="stat-k">showing</span>
            <span class="stat-v">{{ visibleGames.length }}</span>
          </div>
          <div class="stat">
            <span class="stat-k">installed</span>
            <span class="stat-v">{{ installedCount }}</span>
          </div>
          <div class="stat">
            <span class="stat-k">selected</span>
            <span class="stat-v accent">{{ selected.size }}</span>
          </div>
        </div>

        <form class="search" role="search" @submit.prevent="submitSearch">
          <input
            ref="searchEl"
            v-model="query"
            type="search"
            class="search-input"
            placeholder="search library & steam…"
            autocomplete="off"
            spellcheck="false"
            aria-label="Search library and Steam"
          />
          <button
            v-if="query"
            type="button"
            class="search-clear"
            title="Clear (Esc)"
            aria-label="Clear search"
            @click="query = ''"
          >
            ✕
          </button>
          <button type="submit" class="search-go" title="Search Steam" aria-label="Search Steam">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" />
              <path d="M16 16l4.5 4.5" />
            </svg>
          </button>
        </form>

        <div class="tools">
          <div class="popover-anchor">
            <button
              type="button"
              class="tool bridge"
              :class="{ live: paired, online: bridge && !paired }"
              :aria-expanded="bridgeOpen"
              @click.stop="toggleBridgePopover"
            >
              <span class="dot" />
              <span>bridge · {{ paired ? 'paired' : !probed ? 'check' : bridge ? 'online' : 'offline' }}</span>
            </button>

            <div v-if="bridgeOpen" class="popover bridge-pop" @click.stop>
              <div class="pop-k">epsilon bridge</div>
              <p class="pop-p">
                The page previews metadata publicly and only hands AppIDs to Epsilon when you
                register into <code>config.yaml</code>.
              </p>
              <dl class="bridge-dl">
                <dt>endpoint</dt>
                <dd>127.0.0.1:1952</dd>
                <dt>state</dt>
                <dd>
                  {{
                    probing
                      ? 'checking…'
                      : !probed
                        ? 'not checked'
                        : paired
                          ? `paired · ${bridgeVersion}`
                          : bridge
                            ? `running, not paired${bridgeVersion ? ` · ${bridgeVersion}` : ''}`
                            : 'not running'
                  }}
                </dd>
              </dl>
              <div v-if="bridge" class="bridge-token">
                <label for="bridge-token">console token</label>
                <input
                  id="bridge-token"
                  v-model="bridgeToken"
                  type="password"
                  placeholder="paste from ASSella → Companions → Epsilon"
                  @keydown.enter.prevent="saveBridgeToken"
                />
                <button type="button" class="btn" @click="saveBridgeToken">pair</button>
              </div>
              <div class="pop-actions">
                <button type="button" class="btn" :disabled="probing" @click="probeBridge()">
                  {{ probing ? 'checking…' : probed ? 'check again' : 'check for epsilon' }}
                </button>
                <button type="button" class="btn ghost" @click="bridgeOpen = false">close</button>
              </div>
            </div>
          </div>

          <button
            type="button"
            class="tool"
            :aria-pressed="selected.size > 0"
            @click="selected.size ? clearSelection() : toggleAllVisible()"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" />
            </svg>
            <span>{{ selected.size ? 'clear' : 'select' }}</span>
          </button>

          <div class="popover-anchor">
            <button
              type="button"
              class="tool"
              :class="{ live: paired }"
              :aria-expanded="settingsOpen"
              @click.stop="toggleSettingsPopover"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="12" cy="12" r="3.2" />
                <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
              </svg>
              <span>settings</span>
            </button>

            <div v-if="settingsOpen" class="popover set-pop" @click.stop>
              <div class="pop-k">settings</div>
              <div class="set-row">
                <span class="set-k">accent</span>
                <div class="swatches">
                  <button
                    v-for="a in ACCENTS"
                    :key="a.hex"
                    type="button"
                    class="swatch-btn"
                    :class="{ on: accent === a.hex }"
                    :style="{ background: a.hex }"
                    :title="a.name"
                    :aria-label="a.name"
                    @click="pickAccent(a.hex)"
                  />
                </div>
              </div>
              <template v-if="paired">
                <div class="set-row">
                  <span class="set-k">hubcap key</span>
                  <span class="set-v">{{ hubcap?.ok ? hubcap.masked : 'none on this machine' }}</span>
                </div>
                <div class="set-row">
                  <label class="set-k" for="set-gateway">gateway</label>
                  <select
                    id="set-gateway"
                    v-model="form.gateway"
                    @change="saveSetting('hubcap_gateway', form.gateway)"
                  >
                    <option value="auto">auto</option>
                    <option value="direct">direct</option>
                    <option value="proxy">proxy</option>
                  </select>
                </div>
                <div class="set-row">
                  <label class="set-k" for="set-dlcs">fetch dlcs on inject</label>
                  <input
                    id="set-dlcs"
                    v-model="form.fetchDlcs"
                    type="checkbox"
                    @change="saveSetting('fetch_dlcs_on_inject', form.fetchDlcs)"
                  />
                </div>
                <div class="set-row">
                  <label class="set-k" for="set-delisted">add delisted dlcs</label>
                  <input
                    id="set-delisted"
                    v-model="form.addDelisted"
                    type="checkbox"
                    @change="saveSetting('add_delisted_dlcs', form.addDelisted)"
                  />
                </div>
                <div class="set-row">
                  <label class="set-k" for="set-notify">notifications</label>
                  <select
                    id="set-notify"
                    v-model="form.notifications"
                    @change="saveSetting('notification_mode', form.notifications)"
                  >
                    <option value="auto">auto</option>
                    <option value="steam">steam</option>
                    <option value="desktop">desktop</option>
                    <option value="none">none</option>
                  </select>
                </div>
                <div class="set-row">
                  <label class="set-k" for="set-store">steam store button</label>
                  <input
                    id="set-store"
                    v-model="form.storePatch"
                    type="checkbox"
                    @change="saveSetting('steam_store_enabled', form.storePatch).then(loadStore)"
                  />
                </div>
                <div class="set-row">
                  <span class="set-k">cef debugging</span>
                  <span class="set-v" :class="{ off: !store?.cef?.available }">{{ storeCefLabel() }}</span>
                </div>
                <p class="pop-note">
                  The switch is shared with Uran and ASSella; Uran draws the button inside Steam.
                  Launch Steam with <code>--cef-enable-debugging</code> for it to appear.
                </p>
              </template>
              <p v-else class="pop-note">
                Pair the bridge first — these live in Epsilon, not in this page.
              </p>
              <div class="pop-actions">
                <button type="button" class="btn tiny" :disabled="probing" @click="probeBridge()">
                  refresh
                </button>
                <button type="button" class="btn tiny ghost" @click="settingsOpen = false">close</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- ==================== catalog ==================== -->
    <main class="main">
      <div v-if="isSearching || searchMode" class="filter-bar">
        <div>
          showing <strong>{{ visibleGames.length }}</strong> matching title{{ visibleGames.length === 1 ? '' : 's' }}
          <span v-if="query" class="filter-q">“{{ query.trim() }}”</span>
          <span v-if="searchMode && searching" class="filter-q">· searching steam…</span>
        </div>
        <button type="button" class="btn tiny" @click="query = ''; pivot = 'catalog'">reset view</button>
      </div>

      <div v-if="!visibleGames.length" class="empty">
        <template v-if="searchMode && searching">
          <div class="empty-title">searching the store…</div>
          <p>Valve's catalogue can take a second.</p>
        </template>
        <template v-else-if="searchError">
          <div class="empty-title">the bridge said no</div>
          <p>{{ searchError }}</p>
          <button type="button" class="btn" @click="probeBridge()">check the bridge</button>
        </template>
        <template v-else-if="searchMode">
          <div class="empty-title">no matches</div>
          <p>Nothing in your library or on Steam matched “{{ query.trim() }}”. Try a shorter term or the AppID.</p>
          <button type="button" class="btn" @click="query = ''">back to your library</button>
        </template>
        <template v-else-if="demo">
          <div class="empty-title">nothing matched</div>
          <p>Try another title, an AppID, or a tag like <em>roguelike</em> or <em>co-op</em>.</p>
          <button type="button" class="btn primary" @click="query = ''; pivot = 'catalog'">show everything</button>
        </template>
        <template v-else-if="!paired && !library.length">
          <div class="empty-title">no bridge, no catalogue</div>
          <p>
            The console reads your library and the store through Epsilon on
            <code>127.0.0.1:1952</code>. Nothing is fetched from this site — so with the
            bridge off, there is nothing to show.
          </p>
          <div class="empty-actions">
            <button type="button" class="btn primary" @click="probeBridge()">
              connect epsilon
            </button>
            <button type="button" class="btn" @click="demo = true">look at the demo</button>
          </div>
        </template>
        <template v-else-if="loading">
          <div class="empty-title">reading your library…</div>
          <p>Epsilon is scanning config.yaml and local Steam manifests.</p>
        </template>
        <template v-else-if="pivot !== 'catalog'">
          <div class="empty-title">nothing in “{{ PIVOTS.find((p) => p.id === pivot)?.label }}”</div>
          <p>Switch back to all titles or search Steam to add more.</p>
          <button type="button" class="btn primary" @click="pivot = 'catalog'">show all</button>
        </template>
        <template v-else>
          <div class="empty-title">nothing registered yet</div>
          <p>Search a title above and press <strong>register</strong> to write it to config.yaml.</p>
          <button type="button" class="btn primary" @click="focusSearch">search the store</button>
        </template>
      </div>

      <template v-else>
        <section v-for="section in sections" :key="section.id" class="group">
          <div class="group-hd">
            <h2>{{ section.label }} <span class="badge">{{ section.games.length }}</span></h2>
            <span class="hint">
              {{
                section.id === 'owned'
                  ? 'already on your machine or in config.yaml'
                  : section.id === 'results'
                    ? 'click to preview via steamcmd · register grabs keys'
                    : 'click to inspect · right-click to select'
              }}
            </span>
          </div>

          <div class="grid">
            <article
              v-for="(g, i) in section.games"
              :key="g.id"
              class="tile"
              :class="[
                g.tile,
                {
                  light: isLight(g),
                  'has-art': Boolean(tileThumb(g)),
                  sel: selected.has(g.id),
                  act: activeId === g.id,
                  flipped: flipped.has(g.id)
                }
              ]"
              :style="{ backgroundColor: g.accent, animationDelay: Math.min(i * 18, 220) + 'ms' }"
              role="button"
              tabindex="0"
              :aria-label="`${g.title}, AppID ${g.appId}, ${STATUS_LABEL[g.status]}`"
              :aria-pressed="selected.has(g.id)"
              @click="onTile(g)"
              @keydown.enter.prevent="onTile(g)"
              @keydown.space.prevent="onTile(g)"
              @contextmenu.prevent="toggleSelect(g.id)"
            >
              <span class="corner" title="Select" @click.stop="toggleSelect(g.id)">
                <span class="corner-tri" />
                <svg class="corner-tick" viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5" /></svg>
              </span>

              <div class="track">
                <div class="face face-1">
                  <img
                    v-if="tileThumb(g)"
                    class="thumb"
                    :src="tileThumb(g)"
                    alt=""
                    loading="lazy"
                    decoding="async"
                    referrerpolicy="no-referrer"
                    @error="nextTileThumb(g)"
                  />
                  <span v-if="tileThumb(g)" class="tile-scrim" aria-hidden="true" />

                  <div class="face-top">
                    <span class="status" :class="`st-${g.status}`">
                      <i class="dot" />{{ STATUS_LABEL[g.status] }}
                    </span>
                    <span class="appid">#{{ g.appId }}</span>
                  </div>

                  <div v-if="!tileThumb(g)" class="emblem">
                    <svg viewBox="0 0 64 64" v-html="GLYPHS[g.glyph]" />
                  </div>

                  <div class="face-bottom">
                    <div class="title-wrap">
                      <div class="title">{{ g.title }}</div>
                      <div v-if="g.tile !== 't1x1' && g.tags.length" class="sub">
                        {{ g.tags.slice(0, 2).join(' · ') }}
                      </div>
                    </div>
                    <div v-if="totalSize(g) > 0 || g.depots.length" class="meta">
                      <div v-if="totalSize(g) > 0">{{ sizeLabel(g) }}</div>
                      <div v-if="g.tile !== 't1x1' && g.depots.length" class="dim">
                        {{ g.depots.length }} depot{{ g.depots.length > 1 ? 's' : '' }}
                      </div>
                    </div>
                  </div>
                </div>

                <div class="face face-2">
                  <div class="face-top">
                    <span class="face-k">{{ g.inLibrary ? 'library' : 'preview' }}</span>
                    <span class="appid">{{ g.updated || `#${g.appId}` }}</span>
                  </div>
                  <dl class="facts">
                    <div v-if="g.buildId"><dt>build</dt><dd>{{ g.buildId }}</dd></div>
                    <div><dt>state</dt><dd>{{ g.installed ? 'on disk' : g.owned ? 'steam owned' : STATUS_LABEL[g.status] }}</dd></div>
                    <div v-if="g.depots.length"><dt>depots</dt><dd>{{ includedDepots(g).length }} / {{ g.depots.length }}</dd></div>
                    <div v-if="g.inLibrary"><dt>keys</dt><dd>{{ keyCountOf(g) }}<template v-if="g.depots.length"> / {{ g.depots.length }}</template></dd></div>
                    <div v-if="g.tile === 't2x2' && g.dlc.length"><dt>dlc</dt><dd>{{ g.dlc.length }}</dd></div>
                  </dl>
                  <div class="face-bottom">
                    <div class="title">{{ g.title }}</div>
                    <div class="meta">{{ STATUS_LABEL[g.status] }}</div>
                  </div>
                </div>
              </div>
            </article>
          </div>
        </section>
      </template>
    </main>

    <!-- ==================== inspector ==================== -->
    <div v-if="activeGame" class="flyout-scrim" @click="closeFlyout" />

    <aside class="flyout" :class="{ open: !!activeGame }" aria-label="title inspector">
      <template v-if="activeGame">
        <div class="fo-hd">
          <div class="super"><span class="sq" />title inspector</div>
          <button type="button" class="icon-btn" title="Close (Esc)" aria-label="Close inspector" @click="closeFlyout">✕</button>
        </div>

        <div
          class="fo-banner"
          :class="{ 'has-art': Boolean(bannerThumb(activeGame)) }"
          :style="{
            backgroundColor: activeGame.accent,
            color: bannerThumb(activeGame) ? '#fff' : isLight(activeGame) ? '#111' : '#fff'
          }"
        >
          <img
            v-if="bannerThumb(activeGame)"
            class="fo-hero"
            :src="bannerThumb(activeGame)"
            alt=""
            decoding="async"
            referrerpolicy="no-referrer"
            @error="nextBannerThumb(activeGame)"
          />
          <span v-if="bannerThumb(activeGame)" class="fo-hero-scrim" aria-hidden="true" />

          <div class="fo-banner-top">
            <span class="status solid" :class="`st-${activeGame.status}`">
              <i class="dot" />{{ STATUS_LABEL[activeGame.status] }}
            </span>
            <span class="fo-year">{{ activeGame.year || `#${activeGame.appId}` }}</span>
          </div>
          <h3>{{ activeGame.title }}</h3>
          <p>
            {{
              activeGame.developer ||
              (activeGame.installed
                ? 'installed on this machine'
                : activeGame.owned
                  ? 'owned on steam'
                  : activeGame.inLibrary
                    ? 'registered in at0-m'
                    : 'steam store preview')
            }}
          </p>
          <div v-if="!bannerThumb(activeGame)" class="fo-emblem">
            <svg viewBox="0 0 64 64" v-html="GLYPHS[activeGame.glyph]" />
          </div>
        </div>

        <div class="fo-body">
          <dl class="fo-meta">
            <div><dt>appid</dt><dd>{{ activeGame.appId }}</dd></div>
            <div><dt>build</dt><dd>{{ activeGame.buildId || '—' }}</dd></div>
            <div><dt>depots</dt><dd>{{ activeGame.depots.length ? `${activeDepots.length} / ${activeGame.depots.length}` : '—' }}</dd></div>
            <div><dt>size</dt><dd>{{ sizeLabel(activeGame) }}</dd></div>
          </dl>

          <section v-if="activeGame.depots.length || activeGame.detailState === 'loading'" class="fo-sec">
            <div class="fo-sec-hd">
              <h4>depots</h4>
              <button
                v-if="demo && activeGame.depots.length"
                type="button"
                class="link"
                @click="toggleAllDepots(activeGame)"
              >
                {{ activeGame.depots.every((d) => depotOn(activeGame, d)) ? 'deselect all' : 'select all' }}
              </button>
              <span v-else class="fo-note">
                {{ activeGame.inLibrary ? 'from local config + steamcmd' : 'public steamcmd info' }}
              </span>
            </div>

            <div v-if="!activeGame.depots.length && activeGame.detailState === 'loading'" class="fo-loading">
              fetching public depot list…
            </div>

            <ul v-else class="depots">
              <li
                v-for="d in activeGame.depots"
                :key="d.id"
                class="depot"
                :class="{ off: demo && !depotOn(activeGame, d) }"
              >
                <label v-if="demo" class="depot-check">
                  <input
                    type="checkbox"
                    :checked="depotOn(activeGame, d)"
                    :aria-label="`include depot ${d.id}`"
                    @change="toggleDepot(activeGame, d)"
                  />
                  <span />
                </label>

                <div class="depot-main">
                  <div class="depot-line">
                    <span class="depot-id">{{ d.id }}</span>
                    <span class="depot-name">{{ d.name }}</span>
                    <span v-if="d.size > 0" class="depot-size">{{ fmtGB(d.size) }}</span>
                  </div>
                  <div class="depot-line sub">
                    <template v-if="activeGame.live">
                      <span v-if="d.hasKey" class="key on-device">key on this machine</span>
                      <span v-else-if="activeGame.inLibrary" class="key missing">no local key</span>
                      <span v-else class="key neutral">fetched on register</span>
                      <span class="gid">
                        {{
                          activeGame.inLibrary
                            ? d.enabled
                              ? 'in config.yaml'
                              : 'not in config'
                            : d.gid
                              ? `gid ${d.gid}`
                              : 'public manifest'
                        }}
                      </span>
                    </template>
                    <template v-else>
                      <button
                        v-if="d.key"
                        type="button"
                        class="key"
                        :title="revealed.has(d.id) ? 'hide key' : 'reveal key'"
                        @click="toggleReveal(d.id)"
                      >
                        {{ revealed.has(d.id) ? d.key : maskKey(d.key) }}
                      </button>
                      <span v-else class="key missing">no key available</span>
                      <span class="gid">{{ d.gid ? `gid ${d.gid}` : 'no manifest' }}</span>
                    </template>
                  </div>
                </div>
              </li>
            </ul>
          </section>

          <section v-if="activeGame.dlc.length" class="fo-sec">
            <div class="fo-sec-hd">
              <h4>dlc</h4>
              <span class="fo-note">{{ activeGame.dlc.length }} listed</span>
            </div>
            <div class="chips">
              <span v-for="dlc in activeGame.dlc" :key="dlc.appId" class="chip-dlc">
                {{ dlc.name }} <em>{{ dlc.appId }}</em>
              </span>
            </div>
          </section>

          <section v-if="activeGame.live" class="fo-sec">
            <div class="fo-sec-hd">
              <h4>hubcap</h4>
              <span class="fo-note">
                {{ !paired ? 'pair to read' : hubcap?.ok ? 'ready on register' : 'no key on this machine' }}
              </span>
            </div>
            <div v-if="paired && hubcap?.ok" class="key-saved">
              <code>{{ hubcap.masked }}</code>
              <span class="gid">{{ hubcap.is_override ? 'override' : 'from settings' }}</span>
            </div>
            <p v-else-if="paired" class="fo-fine">
              Epsilon has no Hubcap key, so registering cannot resolve missing keys yet. Add one in
              ASSella → Settings → Companions.
            </p>
            <p v-else class="fo-fine">
              This is cached library state. Pair the bridge to register titles or refresh local status.
            </p>
            <p class="fo-fine">
              Preview metadata comes from public SteamCMD info. Hubcap is only queried by Epsilon when
              you click register.
            </p>
          </section>

          <section v-else class="fo-sec">
            <div class="fo-sec-hd">
              <h4>hubcap api key</h4>
              <span class="fo-note">{{ hasKey ? 'saved in this browser' : 'required for manifest fetch' }}</span>
            </div>
            <div v-if="hasKey" class="key-saved">
              <code>{{ keyVisible ? hubcapKey : maskKey(hubcapKey) }}</code>
              <button type="button" class="link" @click="keyVisible = !keyVisible">{{ keyVisible ? 'hide' : 'show' }}</button>
              <button type="button" class="link danger" @click="forgetKey">forget</button>
            </div>
            <form v-else class="key-form" @submit.prevent="applyKey">
              <input
                v-model="keyDraft"
                type="password"
                placeholder="paste hubcap key"
                autocomplete="off"
                spellcheck="false"
                aria-label="Hubcap API key"
              />
              <button type="submit" class="btn">save</button>
            </form>
            <p class="fo-fine">
              Stored in <code>localStorage</code> on this origin.
            </p>
          </section>

          <section class="fo-sec">
            <div class="fo-sec-hd"><h4>apply</h4></div>

            <template v-if="!demo">
              <button
                type="button"
                class="btn primary wide"
                :disabled="registering || !paired"
                @click="activeGame.inLibrary ? refetch() : register()"
              >
                {{
                  registering
                    ? 'registering…'
                    : activeGame.inLibrary
                      ? 're-register / refresh keys'
                      : 'register to at0-m'
                }}
              </button>
              <p class="fo-fine">
                <template v-if="!paired">
                  Pair with Epsilon first — the console never touches
                  <span class="mono">config.yaml</span> on its own.
                </template>
                <template v-else>
                  Epsilon fetches keys via Hubcap on demand, writes
                  <span class="mono">config.yaml</span>, and Steam starts the download.
                </template>
              </p>
            </template>

            <template v-else>
              <button type="button" class="btn primary wide" @click="exportLua()">
                download .lua
              </button>
              <div class="btn-row">
                <button type="button" class="btn" @click="copyYaml()">copy yaml</button>
                <button type="button" class="btn" @click="copyLua()">copy lua</button>
              </div>
              <p class="fo-fine">
                Demo catalog — export the recipe and import it in ASSella
                (<span class="mono">Library → Scan for LUA</span>).
              </p>
            </template>

            <a
              v-if="activeGame.inLibrary || activeGame.installed || activeGame.owned"
              class="steam-link"
              :href="activeGame.installed ? `steam://rungameid/${activeGame.appId}` : `steam://install/${activeGame.appId}`"
            >
              {{ activeGame.installed ? `launch steam://rungameid/${activeGame.appId}` : `open steam://install/${activeGame.appId}` }}
            </a>
          </section>
        </div>

        <div class="fo-ft">
          <span v-if="demo">demo catalog · sample data</span>
          <span v-else-if="activeGame.detailState === 'loading'">loading metadata…</span>
          <span v-else-if="activeGame.detailState === 'error'">
            metadata unavailable ·
            <button type="button" class="link" @click="loadDetail(activeGame, true)">retry</button>
          </span>
          <span v-else-if="!paired">cached {{ cachedAt ? new Date(cachedAt).toLocaleDateString() : '' }} · pair to refresh</span>
          <span v-else>{{ activeGame.installed ? 'installed' : activeGame.inLibrary ? 'in library' : 'store preview' }} · keys stay local</span>
          <button type="button" class="link" @click="toggleSelect(activeGame.id)">
            {{ selected.has(activeGame.id) ? 'deselect' : 'select' }}
          </button>
        </div>
      </template>
    </aside>

    <!-- ==================== app bar ==================== -->
    <div class="appbar" :class="{ open: selected.size > 0 }">
      <div class="appbar-in">
        <div class="appbar-count">
          <span class="count-box">{{ selected.size }}</span>
          <div>
            <div class="count-label">{{ selected.size }} item{{ selected.size === 1 ? '' : 's' }} selected</div>
            <div class="count-sub">
              <template v-if="selectedSize > 0">{{ fmtGB(selectedSize) }} · </template>
              {{ selectedGames.length }} title{{ selectedGames.length === 1 ? '' : 's' }}
            </div>
          </div>
        </div>
        <div class="appbar-actions">
          <button
            type="button"
            class="ab-item"
            :disabled="registering"
            @click="register(selectedGames)"
          >
            <span class="ab-ring">
              <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>
            </span>
            <span>{{ registering ? 'working…' : 'register' }}</span>
          </button>
          <template v-if="demo">
            <button type="button" class="ab-item" @click="exportLua(selectedGames)">
              <span class="ab-ring"><svg viewBox="0 0 24 24"><path d="M12 3v13M7 11l5 5 5-5M5 21h14" /></svg></span>
              <span>export .lua</span>
            </button>
            <button type="button" class="ab-item" @click="copyYaml(selectedGames)">
              <span class="ab-ring"><svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" /><path d="M16 8V4H4v12h4" /></svg></span>
              <span>copy yaml</span>
            </button>
          </template>
          <button type="button" class="ab-item" @click="toggleAllVisible">
            <span class="ab-ring"><svg viewBox="0 0 24 24"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" /></svg></span>
            <span>select all</span>
          </button>
          <button type="button" class="ab-item" @click="clearSelection">
            <span class="ab-ring"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" /></svg></span>
            <span>clear</span>
          </button>
        </div>
      </div>
    </div>

    <!-- ==================== toasts ==================== -->
    <div class="toasts" aria-live="polite">
      <div v-for="t in toasts" :key="t.id" class="toast">
        <div class="toast-t">{{ t.title }}</div>
        <div v-if="t.sub" class="toast-s">{{ t.sub }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.at0m {
  --bg: #0b0b0b;
  --panel: #141414;
  --panel-2: #1b1b1b;
  --line: #262626;
  --line-2: #3a3a3a;
  --text: #ffffff;
  --muted: #8d8d8d;
  --muted-2: #bdbdbd;
  --accent: #0050ef;
  --font: 'Segoe UI Variable Display', 'Segoe UI', system-ui, -apple-system, 'Inter', 'Noto Sans', sans-serif;
  --mono: ui-monospace, 'Cascadia Mono', 'SF Mono', 'Segoe UI Mono', Menlo, monospace;

  min-height: 100svh;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.45;
  padding-bottom: calc(6.5rem + env(safe-area-inset-bottom, 0px));
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

:global(html),
:global(body) {
  background: #0b0b0b;
}

.at0m *,
.at0m *::before,
.at0m *::after {
  box-sizing: border-box;
  border-radius: 0;
  box-shadow: none;
}

.at0m button {
  font: inherit;
  color: inherit;
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
}

.at0m :focus-visible {
  outline: 2px solid var(--text);
  outline-offset: 2px;
}

.at0m ::selection {
  background: var(--accent);
  color: #fff;
}

.at0m ::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
.at0m ::-webkit-scrollbar-track {
  background: #101010;
}
.at0m ::-webkit-scrollbar-thumb {
  background: #333;
}
.at0m ::-webkit-scrollbar-thumb:hover {
  background: var(--accent);
}

.mono,
code {
  font-family: var(--mono);
}

/* ------------------------------- header ------------------------------- */

.hd {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem 2rem;
  padding: 1.35rem clamp(1rem, 3vw, 2.5rem) 0.9rem;
  background: rgba(11, 11, 11, 0.96);
  border-bottom: 1px solid var(--line);
}

.hd-left {
  min-width: 0;
  flex: 1 1 320px;
}

.super {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 10.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 500;
}

.sq {
  width: 9px;
  height: 9px;
  background: var(--accent);
  flex: none;
}

.chip {
  padding: 0.1rem 0.4rem;
  border: 1px solid var(--line-2);
  color: var(--muted-2);
  letter-spacing: 0.14em;
  font-size: 9.5px;
}

.at0m h1 {
  margin: 0.3rem 0 0.6rem;
  font-size: clamp(2.4rem, 5.5vw, 4.1rem);
  font-weight: 200;
  letter-spacing: -0.035em;
  line-height: 0.92;
  color: var(--text);
}

.pivots {
  display: flex;
  align-items: baseline;
  gap: 1.45rem;
  overflow-x: auto;
  scrollbar-width: none;
  scroll-snap-type: x proximity;
  -webkit-overflow-scrolling: touch;
  padding-bottom: 2px;
}
.pivots::-webkit-scrollbar {
  display: none;
}

.pivot {
  position: relative;
  flex: none;
  scroll-snap-align: start;
  font-size: clamp(1.1rem, 2vw, 1.42rem);
  font-weight: 300;
  color: #6b6b6b;
  padding: 0 0 0.32rem;
  transition: color 0.14s ease, transform 0.14s ease;
  white-space: nowrap;
}

.pivot:hover {
  color: var(--muted-2);
}

.pivot.active {
  color: var(--text);
  font-weight: 400;
}

.pivot.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background: var(--accent);
}

.pivot-count {
  font-size: 10px;
  font-family: var(--mono);
  color: var(--muted);
  margin-left: 0.32rem;
  vertical-align: super;
}

.hd-right {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.55rem;
  width: min(100%, 340px);
}

.stats {
  display: flex;
  border: 1px solid var(--line);
  background: var(--panel);
}

.stat {
  flex: 1;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.45rem;
  padding: 0.32rem 0.65rem;
}
.stat + .stat {
  border-left: 1px solid var(--line);
}
.stat-k {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}
.stat-v {
  font-size: 0.95rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.stat-v.accent {
  color: var(--accent);
}

.search {
  display: flex;
  align-items: stretch;
  height: 38px;
  background: #17191d;
  border: 2px solid var(--line-2);
  transition: border-color 0.14s ease;
}
.search:focus-within {
  border-color: var(--text);
}
.search-input {
  flex: 1;
  min-width: 0;
  padding: 0 0.15rem 0 0.65rem;
  background: transparent;
  border: 0;
  color: var(--text);
  font: inherit;
  font-size: 0.85rem;
  outline: none;
  appearance: none;
}
.search-input::-webkit-search-cancel-button,
.search-input::-webkit-search-decoration {
  display: none;
}
.search-input::placeholder {
  color: #6d6d6d;
}
.search-clear,
.search-go {
  flex: none;
  display: grid;
  place-items: center;
  line-height: 1;
}
.search-clear {
  width: 28px;
  color: #9aa0a8;
  font-size: 13px;
}
.search-clear:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.1);
}
.search-go {
  width: 36px;
  background: var(--accent);
  color: #fff;
}
.search-go:hover {
  filter: brightness(1.12);
}
.search-go svg {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.2;
  stroke-linecap: square;
}

.tools {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.tool {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.42rem 0.65rem;
  border: 1px solid var(--line-2);
  background: var(--panel-2);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted-2);
  transition: border-color 0.14s ease, color 0.14s ease, background 0.14s ease;
}
.tool:hover {
  border-color: var(--text);
  color: var(--text);
}
.tool svg {
  width: 13px;
  height: 13px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
}
.tool[aria-pressed='true'] {
  color: #111;
  background: #fff;
  border-color: #fff;
}
.tool .dot {
  width: 6px;
  height: 6px;
  background: #d0453a;
  flex: none;
}
.tool.bridge.online .dot {
  background: #f0a30a;
}
.tool.bridge.live .dot {
  background: #3ecf6a;
}
.tool.bridge.live {
  color: #fff;
  border-color: #3ecf6a;
}

.popover-anchor {
  position: relative;
}

.popover {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 60;
  width: 280px;
  padding: 0.9rem;
  background: #171717;
  border: 1px solid #fff;
  animation: pop-in 0.14s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes pop-in {
  from {
    opacity: 0;
    transform: translateY(-6px);
  }
}

.bridge-pop {
  left: 0;
  right: auto;
  width: min(330px, 88vw);
}

.set-pop {
  right: 0;
  left: auto;
  width: min(300px, 88vw);
}

.pop-k {
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.6rem;
}
.swatches {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.swatch-btn {
  width: 20px;
  height: 20px;
  border: 2px solid transparent;
}
.swatch-btn.on {
  border-color: #fff;
  outline: 1px solid #000;
  outline-offset: -4px;
}

.set-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.set-k {
  font-size: 11px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #a9b0bb;
}

.set-v.off {
  color: #d08a7a;
}

.set-v {
  font-family: var(--mono);
  font-size: 11px;
  color: #e6e9ee;
}

.set-pop select {
  background: #1b1f26;
  color: #e6e9ee;
  border: 1px solid rgba(255, 255, 255, 0.16);
  padding: 3px 6px;
  font-size: 11px;
}

.set-pop input[type='checkbox'] {
  accent-color: var(--accent);
  width: 14px;
  height: 14px;
}

.pop-note {
  margin: 0.7rem 0 0;
  padding-top: 0.6rem;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--muted);
}

.pop-p {
  margin: 0 0 0.75rem;
  font-size: 12px;
  line-height: 1.55;
  color: var(--muted-2);
}
.pop-p code,
.fo-fine code {
  font-size: 11px;
  color: #fff;
  background: #000;
  padding: 0.05rem 0.25rem;
}
.bridge-dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.3rem 0.75rem;
  margin: 0 0 0.8rem;
  font-size: 11.5px;
}
.bridge-dl dt {
  color: var(--muted);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  font-size: 9.5px;
  align-self: center;
}
.bridge-dl dd {
  margin: 0;
  font-family: var(--mono);
  font-size: 11.5px;
}
.bridge-token {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin-bottom: 0.8rem;
}
.bridge-token label {
  font-size: 9.5px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}
.bridge-token input {
  padding: 0.45rem 0.6rem;
  background: #0e0e0e;
  border: 1px solid var(--line-2);
  color: #fff;
  font: inherit;
  font-size: 12px;
  outline: none;
}
.bridge-token input:focus {
  border-color: #fff;
}
.pop-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.pop-actions .btn {
  flex: 1;
}

/* -------------------------------- main -------------------------------- */

.main {
  padding: 1.35rem clamp(1rem, 3vw, 2.5rem) 2rem;
  max-width: 1680px;
  margin: 0 auto;
}

.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.55rem 0.85rem;
  margin-bottom: 1.25rem;
  background: var(--panel);
  border-left: 3px solid var(--accent);
  font-size: 12.5px;
  color: var(--muted-2);
}
.filter-bar strong {
  color: #fff;
  font-variant-numeric: tabular-nums;
}
.filter-q {
  color: var(--muted);
  margin-left: 0.35rem;
}

.empty {
  padding: 4rem 1.25rem;
  text-align: center;
  border: 1px solid var(--line);
  background: #101010;
}
.empty-actions {
  display: flex;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
}
.empty-title {
  font-size: clamp(1.5rem, 4vw, 2.25rem);
  font-weight: 200;
  letter-spacing: -0.02em;
  margin-bottom: 0.5rem;
}
.empty p {
  margin: 0 auto 1.5rem;
  max-width: 46ch;
  color: var(--muted);
  font-size: 13px;
}
.empty em {
  color: var(--muted-2);
  font-style: normal;
  border-bottom: 1px solid var(--line-2);
}

.group {
  margin-bottom: 2.15rem;
}
.group-hd {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.35rem 1rem;
  margin-bottom: 0.6rem;
}
.group-hd h2 {
  margin: 0;
  font-size: 1.08rem;
  font-weight: 300;
  letter-spacing: 0.01em;
  color: #fff;
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}
.badge {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--muted);
  border: 1px solid var(--line);
  padding: 0 0.3rem;
}
.hint {
  font-size: 10.5px;
  color: #7a7a7a;
}

/* -------------------------------- grid -------------------------------- */

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(clamp(138px, 14vw, 168px), 1fr));
  grid-auto-rows: clamp(138px, 14vw, 164px);
  grid-auto-flow: dense;
  gap: 8px;
}

.tile {
  position: relative;
  overflow: hidden;
  cursor: pointer;
  color: #fff;
  outline: 2px solid transparent;
  outline-offset: -2px;
  transition: outline-color 0.12s ease, transform 0.16s cubic-bezier(0.16, 1, 0.3, 1);
  animation: tile-in 0.34s cubic-bezier(0.1, 0.9, 0.2, 1) backwards;
  background-image: linear-gradient(135deg, rgba(255, 255, 255, 0.055) 25%, transparent 25%),
    linear-gradient(225deg, rgba(255, 255, 255, 0.055) 25%, transparent 25%);
  background-size: 26px 26px;
  user-select: none;
}

/* Only switch Face 1 to dark ink when there is NO artwork behind it */
.tile.light:not(.has-art) .face-1,
.tile.light .face-2 {
  color: #101010;
}
.tile.has-art .face-1 {
  color: #fff;
}

.tile:hover {
  outline-color: rgba(255, 255, 255, 0.55);
}
.tile.light:not(.has-art):hover {
  outline-color: rgba(0, 0, 0, 0.45);
}
.tile:active {
  transform: perspective(700px) scale(0.975);
}
.tile.sel {
  outline: 3px solid #fff;
  outline-offset: -3px;
}
.tile.act {
  outline: 3px solid var(--accent);
  outline-offset: -3px;
}

.t1x1 {
  grid-column: span 1;
  grid-row: span 1;
}
.t2x1 {
  grid-column: span 2;
  grid-row: span 1;
}
.t1x2 {
  grid-column: span 1;
  grid-row: span 2;
}
.t2x2 {
  grid-column: span 2;
  grid-row: span 2;
}

@keyframes tile-in {
  from {
    opacity: 0;
    transform: translateX(18px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* selection corner */
.corner {
  position: absolute;
  top: 0;
  right: 0;
  width: 38px;
  height: 38px;
  z-index: 6;
  opacity: 0;
  transition: opacity 0.13s ease;
}
.tile:hover .corner,
.tile.sel .corner,
.tile:focus-visible .corner {
  opacity: 1;
}
.corner-tri {
  position: absolute;
  top: 0;
  right: 0;
  width: 0;
  height: 0;
  border-style: solid;
  border-width: 0 34px 34px 0;
  border-color: transparent rgba(0, 0, 0, 0.5) transparent transparent;
}
.tile.sel .corner-tri {
  border-color: transparent #fff transparent transparent;
}
.corner-tick {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 12px;
  height: 12px;
  fill: none;
  stroke: rgba(255, 255, 255, 0.85);
  stroke-width: 3;
}
.tile.sel .corner-tick {
  stroke: #101010;
}

/* live face flip */
.track {
  height: 200%;
  display: flex;
  flex-direction: column;
  transition: transform 0.58s cubic-bezier(0.19, 1, 0.22, 1);
}
.tile.flipped .track,
.tile:hover .track,
.tile:focus-visible .track {
  transform: translateY(-50%);
}

.face {
  position: relative;
  height: 50%;
  padding: 0.7rem 0.8rem;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 0.4rem;
  min-height: 0;
}
.face-2 {
  background: rgba(0, 0, 0, 0.28);
}
.tile.light .face-2 {
  background: rgba(0, 0, 0, 0.08);
}

.thumb {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  pointer-events: none;
}

.tile-scrim {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(
    to bottom,
    rgba(0, 0, 0, 0.52) 0%,
    rgba(0, 0, 0, 0.08) 40%,
    rgba(0, 0, 0, 0.72) 100%
  );
}

.face-top,
.face-bottom,
.emblem {
  position: relative;
  z-index: 2;
}

.face-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding-right: 1.5rem;
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 9px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  font-weight: 600;
  padding: 0.14rem 0.36rem;
  background: rgba(0, 0, 0, 0.42);
}
.tile.light:not(.has-art) .status {
  background: rgba(0, 0, 0, 0.14);
}
.status .dot {
  width: 5px;
  height: 5px;
  background: currentColor;
  flex: none;
}
.status.st-installed .dot {
  background: #4be384;
}
.status.st-installing .dot {
  background: #52c7ff;
}
.status.st-needs-key .dot {
  background: #f5b942;
}
.status.solid {
  background: rgba(0, 0, 0, 0.55);
}

.appid {
  font-family: var(--mono);
  font-size: 9.5px;
  opacity: 0.8;
  flex: none;
}
.face-k {
  font-size: 9px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 600;
  opacity: 0.8;
}

.emblem {
  flex: 1;
  min-height: 0;
  display: grid;
  place-items: center;
  opacity: 0.95;
}
.emblem svg {
  width: 46px;
  height: 46px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.6;
  stroke-linecap: square;
  stroke-linejoin: miter;
}
.t2x2 .emblem svg {
  width: 92px;
  height: 92px;
  stroke-width: 2.2;
}
.t2x1 .emblem svg {
  width: 56px;
  height: 56px;
}

/* Only use side-by-side layout on wide tiles when showing a vector glyph */
.t2x1:not(.has-art) .face-1 {
  flex-direction: row;
  align-items: center;
  gap: 0.9rem;
  padding-top: 2rem;
}
.t2x1:not(.has-art) .face-1 .face-top {
  position: absolute;
  top: 0.7rem;
  left: 0.8rem;
  right: 0.8rem;
}
.t2x1:not(.has-art) .face-1 .emblem {
  flex: none;
}
.t2x1:not(.has-art) .face-1 .face-bottom {
  flex: 1;
  min-width: 0;
}

.face-bottom {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 0.5rem;
}
.title-wrap {
  min-width: 0;
}

.tile.has-art .face-1 .title,
.tile.has-art .face-1 .sub,
.tile.has-art .face-1 .meta,
.tile.has-art .face-1 .appid {
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.9);
}

.title {
  font-size: 0.84rem;
  font-weight: 500;
  line-height: 1.18;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.t2x1 .title,
.t1x2 .title {
  white-space: normal;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 0.96rem;
}
.t2x2 .title {
  white-space: normal;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 1.38rem;
  font-weight: 300;
  letter-spacing: -0.02em;
}
.face-2 .title {
  font-size: 0.86rem;
  font-weight: 500;
  white-space: nowrap;
  -webkit-line-clamp: unset;
  display: block;
}

.sub {
  font-size: 10px;
  opacity: 0.82;
  margin-top: 0.14rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  text-align: right;
  font-family: var(--mono);
  font-size: 9.5px;
  opacity: 0.88;
  flex: none;
}
.meta .dim {
  opacity: 0.72;
}

.facts {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 10.5px;
  flex: 1;
  justify-content: center;
}
.facts div {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  border-bottom: 1px solid color-mix(in srgb, currentColor 18%, transparent);
  padding-bottom: 0.14rem;
}
.facts dt {
  opacity: 0.75;
  letter-spacing: 0.05em;
}
.facts dd {
  margin: 0;
  font-family: var(--mono);
  font-weight: 600;
}
.t2x2 .facts {
  font-size: 12px;
}

/* ------------------------------- flyout ------------------------------- */

.flyout-scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(0, 0, 0, 0.52);
  animation: fade 0.18s ease;
}

@keyframes fade {
  from {
    opacity: 0;
  }
}

.flyout {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 45;
  width: min(450px, 100%);
  display: flex;
  flex-direction: column;
  background: #131313;
  border-left: 2px solid var(--accent);
  transform: translateX(101%);
  transition: transform 0.26s cubic-bezier(0.16, 1, 0.3, 1);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.flyout.open {
  transform: none;
}

.fo-hd {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.8rem 1rem;
  background: #101010;
  border-bottom: 1px solid var(--line);
}
.icon-btn {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line-2);
  color: var(--muted-2);
  font-size: 12px;
}
.icon-btn:hover {
  background: #fff;
  color: #111;
  border-color: #fff;
}

.fo-banner {
  position: relative;
  min-height: 185px;
  padding: 1.15rem 1.15rem 1.3rem;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  overflow: hidden;
  border-bottom: 2px solid var(--accent);
  background-image: linear-gradient(135deg, rgba(255, 255, 255, 0.07) 25%, transparent 25%),
    linear-gradient(225deg, rgba(255, 255, 255, 0.07) 25%, transparent 25%);
  background-size: 30px 30px;
}
.fo-hero {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  pointer-events: none;
}
.fo-hero-scrim {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(
    to bottom,
    rgba(0, 0, 0, 0.45) 0%,
    rgba(0, 0, 0, 0.2) 38%,
    rgba(0, 0, 0, 0.86) 100%
  );
}
.fo-banner-top {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: auto;
  padding-bottom: 2.2rem;
}
.fo-year {
  font-family: var(--mono);
  font-size: 10px;
  opacity: 0.9;
  padding: 0.12rem 0.36rem;
  background: rgba(0, 0, 0, 0.45);
}
.fo-banner h3 {
  position: relative;
  z-index: 2;
  margin: 0;
  font-size: clamp(1.55rem, 5vw, 2.05rem);
  font-weight: 300;
  letter-spacing: -0.025em;
  line-height: 1.06;
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.75);
}
.fo-banner p {
  position: relative;
  z-index: 2;
  margin: 0.35rem 0 0;
  font-size: 11.5px;
  opacity: 0.9;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.8);
}
.fo-emblem {
  position: absolute;
  right: -10px;
  bottom: -18px;
  opacity: 0.22;
  pointer-events: none;
}
.fo-emblem svg {
  width: 130px;
  height: 130px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.4;
}

.fo-body {
  flex: 1;
  padding: 1rem;
}

.fo-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  margin: 0 0 1.15rem;
  background: var(--line);
  border: 1px solid var(--line);
}
.fo-meta div {
  background: #1a1a1a;
  padding: 0.5rem 0.65rem;
}
.fo-meta dt {
  font-size: 9px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
}
.fo-meta dd {
  margin: 0.15rem 0 0;
  font-family: var(--mono);
  font-size: 0.86rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.fo-sec {
  margin-bottom: 1.35rem;
}
.fo-sec-hd {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.5rem;
  padding-bottom: 0.3rem;
  border-bottom: 1px solid var(--line);
}
.fo-sec-hd h4 {
  margin: 0;
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted-2);
  font-weight: 600;
}
.fo-note {
  font-size: 9.5px;
  color: #8a8a8a;
}
.fo-loading {
  padding: 0.75rem;
  background: #181818;
  border: 1px solid var(--line);
  font-size: 11.5px;
  color: var(--muted);
}

.depots {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: var(--line);
}
.depot {
  display: flex;
  gap: 0.55rem;
  padding: 0.55rem 0.6rem;
  background: #191919;
}
.depot.off {
  opacity: 0.45;
}
.depot-check {
  display: flex;
  align-items: flex-start;
  padding-top: 1px;
  cursor: pointer;
}
.depot-check input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}
.depot-check span {
  width: 13px;
  height: 13px;
  border: 1px solid #fff;
  display: block;
  position: relative;
}
.depot-check input:checked + span {
  background: #fff;
}
.depot-check input:checked + span::after {
  content: '';
  position: absolute;
  left: 3px;
  top: 0;
  width: 4px;
  height: 8px;
  border: solid #111;
  border-width: 0 2px 2px 0;
  transform: rotate(42deg);
}
.depot-check input:focus-visible + span {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.depot-main {
  flex: 1;
  min-width: 0;
}
.depot-line {
  display: flex;
  align-items: baseline;
  gap: 0.45rem;
}
.depot-line.sub {
  margin-top: 0.2rem;
}
.depot-id {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  flex: none;
}
.depot-name {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: var(--muted-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.depot-size {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--muted);
  flex: none;
}
.key {
  font-family: var(--mono);
  font-size: 10px;
  color: #9fd0ff;
  border-bottom: 1px dotted #4a5a6a;
  padding: 0;
  text-align: left;
}
.key:hover {
  color: #fff;
}
.key.on-device {
  color: #7ee2a8;
  border: 0;
  cursor: default;
}
.key.neutral {
  color: #9aa3ad;
  border: 0;
  cursor: default;
}
.key.missing {
  color: #e0a13a;
  border: 0;
  cursor: default;
}
.gid {
  margin-left: auto;
  font-family: var(--mono);
  font-size: 9.5px;
  color: #838383;
  flex: none;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}
.chip-dlc {
  font-size: 11px;
  padding: 0.22rem 0.45rem;
  border: 1px solid var(--line-2);
  color: var(--muted-2);
}
.chip-dlc em {
  font-style: normal;
  font-family: var(--mono);
  font-size: 9.5px;
  color: #6d6d6d;
  margin-left: 0.2rem;
}

.key-form {
  display: flex;
  gap: 0.4rem;
}
.key-form input {
  flex: 1;
  min-width: 0;
  padding: 0.5rem 0.6rem;
  background: #0e0e0e;
  border: 1px solid var(--line-2);
  color: #fff;
  font: inherit;
  font-size: 12px;
  outline: none;
}
.key-form input:focus {
  border-color: #fff;
}
.key-saved {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  background: #0e0e0e;
  border: 1px solid var(--line);
}
.key-saved code {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--muted-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.link {
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: 1px solid var(--line-2);
  padding-bottom: 1px;
}
.link:hover {
  color: #fff;
  border-color: #fff;
}
.link.danger:hover {
  color: #ff6b5a;
  border-color: #ff6b5a;
}

.btn {
  padding: 0.45rem 0.7rem;
  border: 1px solid var(--line-2);
  background: #1e1e1e;
  color: var(--text);
  font-size: 10.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  font-weight: 500;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
  text-align: center;
}
.btn:hover:not(:disabled) {
  background: #2a2a2a;
  border-color: #fff;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  padding: 0.72rem 1rem;
  font-size: 11px;
}
.btn.primary:hover:not(:disabled) {
  filter: brightness(1.12);
}
.btn.wide {
  width: 100%;
}
.btn.ghost {
  background: transparent;
}
.btn.tiny {
  padding: 0.25rem 0.5rem;
  font-size: 9.5px;
}
.btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.btn-row {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.35rem;
  margin-top: 0.4rem;
}
.btn-row .btn {
  font-size: 9.5px;
  letter-spacing: 0.08em;
  padding: 0.48rem 0.35rem;
}

.fo-fine {
  margin: 0.6rem 0 0;
  font-size: 10.5px;
  line-height: 1.55;
  color: #8f8f8f;
}
.steam-link {
  display: inline-block;
  margin-top: 0.7rem;
  font-family: var(--mono);
  font-size: 10.5px;
  color: #7fb6ff;
  border-bottom: 1px dotted #33507a;
  padding-bottom: 1px;
}
.steam-link:hover {
  color: #fff;
  border-color: #fff;
}

.fo-ft {
  position: sticky;
  bottom: 0;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.65rem 1rem calc(0.65rem + env(safe-area-inset-bottom, 0px));
  background: #0f0f0f;
  border-top: 1px solid var(--line);
  font-size: 9.5px;
  letter-spacing: 0.05em;
  color: #858585;
}

/* ------------------------------- app bar ------------------------------ */

.appbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 35;
  background: #080808;
  border-top: 2px solid var(--accent);
  padding-bottom: env(safe-area-inset-bottom, 0px);
  transform: translateY(101%);
  transition: transform 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}
.appbar.open {
  transform: none;
}
.appbar-in {
  max-width: 1680px;
  margin: 0 auto;
  padding: 0.7rem clamp(1rem, 3vw, 2.5rem);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.9rem;
}
.appbar-count {
  display: flex;
  align-items: center;
  gap: 0.65rem;
}
.count-box {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  background: var(--accent);
  color: #fff;
  font-weight: 700;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.count-label {
  font-size: 12.5px;
  font-weight: 500;
}
.count-sub {
  font-size: 10.5px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.appbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 1.1rem;
}
.ab-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.3rem;
  font-size: 9.5px;
  letter-spacing: 0.1em;
  text-transform: lowercase;
  color: var(--muted-2);
}
.ab-item:disabled {
  opacity: 0.5;
  cursor: default;
}
.ab-ring {
  width: 38px;
  height: 38px;
  border: 2px solid #fff;
  border-radius: 50%;
  display: grid;
  place-items: center;
  transition: background 0.12s ease, color 0.12s ease, transform 0.1s ease;
}
.ab-ring svg {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.9;
  stroke-linecap: square;
}
.ab-item:hover:not(:disabled) .ab-ring {
  background: #fff;
  color: #111;
}
.ab-item:active:not(:disabled) .ab-ring {
  transform: scale(0.93);
}

/* -------------------------------- toasts ------------------------------ */

.toasts {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 80;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  width: min(340px, calc(100% - 2rem));
  pointer-events: none;
}
.toast {
  padding: 0.6rem 0.75rem;
  background: #1c1c1c;
  border-left: 3px solid var(--accent);
  animation: toast-in 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
.toast-t {
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.toast-s {
  margin-top: 0.15rem;
  font-size: 11px;
  color: var(--muted);
  line-height: 1.45;
}

@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateX(14px);
  }
}

/* ------------------------------ responsive ---------------------------- */

@media (max-width: 760px) {
  .hd {
    position: static;
    padding-top: 1rem;
    gap: 0.85rem;
  }
  .hd-right {
    width: 100%;
  }
  .stats {
    display: none;
  }
  .pivots {
    gap: 1.15rem;
    margin-right: -1rem;
    padding-right: 1rem;
  }
  .tools {
    justify-content: space-between;
  }
  .bridge-pop,
  .set-pop {
    position: fixed;
    top: 1rem;
    left: 1rem;
    right: 1rem;
    width: auto;
  }
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-auto-rows: 136px;
    gap: 6px;
  }
  .t2x1 {
    grid-column: span 2;
    grid-row: span 1;
  }
  .t1x2 {
    grid-column: span 1;
    grid-row: span 2;
  }
  .t2x2 {
    grid-column: span 2;
    grid-row: span 2;
  }
  .flyout {
    width: 100%;
    border-left: 0;
  }
  .appbar-in {
    justify-content: center;
  }
  .appbar-actions {
    gap: 0.9rem;
  }
}

/* Touch devices: tap opens inspector cleanly without getting stuck on hover flip */
@media (hover: none) {
  .tile:hover .track {
    transform: none;
  }
  .tile.flipped .track {
    transform: translateY(-50%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .at0m *,
  .at0m *::before,
  .at0m *::after {
    animation-duration: 0.001ms !important;
    transition-duration: 0.001ms !important;
  }
  .tile.flipped .track {
    transform: none;
  }
}
</style>