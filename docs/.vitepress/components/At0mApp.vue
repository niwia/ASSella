<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

/* ------------------------------------------------------------------ *
 * at0m — web console (UI preview)
 *
 * Metro/Windows-8 design language: flat planes, 0px radius, no shadows,
 * tight tile gutters, panorama typography, accent-driven highlights.
 * Data below is a demo catalog: no Hubcap / Steam fetch is wired yet.
 * ------------------------------------------------------------------ */

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

const GAMES = [
  {
    id: 'elden', title: 'Elden Ring', developer: 'FromSoftware', year: 2022,
    appId: '1245620', buildId: '18456314', status: 'ready', featured: true, tile: 't2x2',
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
    appId: '1086940', buildId: '17932104', status: 'ready', featured: true, tile: 't2x1',
    accent: '#8C0095', glyph: 'prism', updated: '4 h ago',
    tags: ['rpg', 'turn-based', 'co-op'],
    depots: [
      { id: '1086941', name: 'Baldur\'s Gate 3 — base', size: 142.1, key: '0e5dd8b43415e6b5fad0be29c67a420d39dab654b270d01cc2ed86aeaf009897', gid: '2488721615174967870' },
      { id: '1086942', name: 'Language pack', size: 2.7, key: '6b3469834fc6d42ace327f602ca7bb3308689e39f5ae475cf21ef7b4969d8935', gid: '3797821284027531825' }
    ],
    dlc: []
  },
  {
    id: 'hades2', title: 'Hades II', developer: 'Supergiant Games', year: 2024,
    appId: '1145350', buildId: '17304882', status: 'needs-key', featured: true, tile: 't2x1',
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
    appId: '1091500', buildId: '17640033', status: 'ready', featured: true, tile: 't1x1',
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
    appId: '632360', buildId: '16123980', status: 'registered', featured: false, tile: 't1x1',
    accent: '#008299', glyph: 'helix', updated: 'yesterday',
    tags: ['roguelike', 'co-op', 'shooter'],
    depots: [
      { id: '632361', name: 'Risk of Rain 2 — base', size: 4.2, key: '4356534c90e96bddbe204082378f5bfbd72edeaec88266920527ede2df9dd71c', gid: '5859347729115854006' }
    ],
    dlc: []
  },
  {
    id: 'drg', title: 'Deep Rock Galactic', developer: 'Ghost Ship Games', year: 2020,
    appId: '548430', buildId: '15983112', status: 'ready', featured: false, tile: 't2x1',
    accent: '#FA6800', glyph: 'cube', updated: 'yesterday',
    tags: ['co-op', 'fps', 'procedural'],
    depots: [
      { id: '548431', name: 'Deep Rock Galactic — base', size: 3.4, key: 'e63dc7c0e0a2d63e9d0df820f30bae68adc9be183fa0bdc7c43a7bf80dc4e224', gid: '5863080139693169371' }
    ],
    dlc: []
  },
  {
    id: 'stardew', title: 'Stardew Valley', developer: 'ConcernedApe', year: 2016,
    appId: '413150', buildId: '17234018', status: 'registered', featured: false, tile: 't1x1',
    accent: '#60A917', glyph: 'grid', updated: '2 days ago',
    tags: ['farming sim', 'pixel', 'relaxing'],
    depots: [
      { id: '413151', name: 'Stardew Valley — base', size: 1.2, key: '36c34b51ad68019e905f5fe9b6ee2460461839bb4ea814deeb27c0f5870da09a', gid: '6563760767147878054' }
    ],
    dlc: []
  },
  {
    id: 'factorio', title: 'Factorio', developer: 'Wube Software', year: 2020,
    appId: '427520', buildId: '18005216', status: 'no-build', featured: false, tile: 't1x1',
    accent: '#E3C800', glyph: 'hex', updated: '3 days ago',
    tags: ['automation', 'base building', 'strategy'],
    depots: [
      { id: '427521', name: 'Factorio — base', size: 4.8, key: '82b9963ed4a4607cd19bb59c2dcda3dc2e3048c5f0096d29413e34fb65c7e214', gid: null }
    ],
    dlc: [{ appId: '645730', name: 'Space Age' }]
  },
  {
    id: 'hollow', title: 'Hollow Knight', developer: 'Team Cherry', year: 2017,
    appId: '367520', buildId: '15278912', status: 'ready', featured: false, tile: 't1x1',
    accent: '#1BA1E2', glyph: 'shard', updated: '3 days ago',
    tags: ['metroidvania', 'platformer', '2d'],
    depots: [
      { id: '367521', name: 'Hollow Knight — base', size: 7.5, key: 'af1f0e3dc6ca1fff420613930cb0d67a5855150ef20a805d3630e99a3ea1d571', gid: '1378972138293554776' }
    ],
    dlc: []
  },
  {
    id: 'portal2', title: 'Portal 2', developer: 'Valve', year: 2011,
    appId: '620', buildId: '14981123', status: 'ready', featured: false, tile: 't1x1',
    accent: '#00ABA9', glyph: 'ring', updated: '4 days ago',
    tags: ['puzzle', 'co-op', 'first-person'],
    depots: [
      { id: '621', name: 'Portal 2 — base', size: 11.4, key: '2699e885090db9b07d604e0ad00bf2a0e9c27f91038800e253c74677403164a0', gid: '2974777751465872247' }
    ],
    dlc: []
  },
  {
    id: 'disco', title: 'Disco Elysium', developer: 'ZA/UM', year: 2019,
    appId: '632470', buildId: '15702338', status: 'needs-key', featured: false, tile: 't1x1',
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
    appId: '504230', buildId: '14450192', status: 'ready', featured: false, tile: 't1x1',
    accent: '#AA00FF', glyph: 'wave', updated: '1 week ago',
    tags: ['platformer', 'indie', 'soundtrack'],
    depots: [
      { id: '504231', name: 'Celeste — base', size: 1.2, key: '852f6801394115277df7e7298fbc6573c257f29ea41d26d8c31cd506091c370e', gid: '2690539352691467569' }
    ],
    dlc: []
  },
  {
    id: 'tabletop', title: 'Tabletop Simulator', developer: 'Berserk Games', year: 2015,
    appId: '286160', buildId: '15118840', status: 'ready', featured: false, tile: 't1x1',
    accent: '#0050EF', glyph: 'grid', updated: '1 week ago',
    tags: ['board game', 'sandbox', 'physics'],
    depots: [
      { id: '286161', name: 'Tabletop Simulator — base', size: 3.9, key: '0e72752950792fb8c80b0268fb623b66503c88d2e2ccede57c8422dfb159336e', gid: '2362897436156866247' }
    ],
    dlc: []
  },
  {
    id: 'ftk', title: 'For The King', developer: 'IronOak Games', year: 2018,
    appId: '527230', buildId: '14330622', status: 'no-build', featured: false, tile: 't1x1',
    accent: '#647687', glyph: 'cube', updated: '2 weeks ago',
    tags: ['turn-based', 'roguelike', 'co-op'],
    depots: [
      { id: '527231', name: 'For The King — base', size: 3.1, key: '827bd6b2eeecff5c88bffabb05c3d87d2726928b95ca5c524fe7c174535989b4', gid: null }
    ],
    dlc: []
  }
]

const PIVOTS = [
  { id: 'catalog', label: 'catalog' },
  { id: 'ready', label: 'ready' },
  { id: 'needs-key', label: 'needs key' },
  { id: 'registered', label: 'registered' }
]

const STATUS_LABEL = {
  ready: 'ready',
  'needs-key': 'needs key',
  'no-build': 'no build',
  registered: 'registered'
}

/* ------------------------------- state ------------------------------- */

const query = ref('')
const pivot = ref('catalog')
const activeId = ref(null)
const selected = ref(new Set())
const excluded = ref(new Set())
const revealed = ref(new Set())
const flipped = ref(new Set())
const accent = ref(ACCENTS[0].hex)
const hubcapKey = ref('')
const keyDraft = ref('')
const keyVisible = ref(false)
const bridge = ref(false)
const bridgeVersion = ref('')
const bridgeToken = ref('')
const paired = ref(false)
const probing = ref(false)
const charmOpen = ref(false)
const bridgeOpen = ref(false)
const toasts = ref([])
const searchEl = ref(null)

let toastSeq = 0
let liveTimer = null
const toastTimers = new Set()

/* ----------------------------- computed ------------------------------ */

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return GAMES.filter((g) => {
    if (pivot.value !== 'catalog' && g.status !== pivot.value) return false
    if (!q) return true
    const hay = [g.title, g.appId, g.developer, g.buildId, ...g.tags].join(' ').toLowerCase()
    return hay.includes(q)
  })
})

const isSearching = computed(() => query.value.trim().length > 0 || pivot.value !== 'catalog')
const sections = computed(() => {
  if (!filtered.value.length) return []
  if (isSearching.value) return [{ id: 'results', label: 'results', games: filtered.value }]
  const featured = filtered.value.filter((g) => g.featured)
  const rest = filtered.value.filter((g) => !g.featured)
  const out = []
  if (featured.length) out.push({ id: 'featured', label: 'featured & ready', games: featured })
  if (rest.length) out.push({ id: 'catalog', label: 'catalog', games: rest })
  return out
})
const activeGame = computed(() => GAMES.find((g) => g.id === activeId.value) || null)
const selectedGames = computed(() => GAMES.filter((g) => selected.value.has(g.id)))
const selectedSize = computed(() => selectedGames.value.reduce((n, g) => n + totalSize(g), 0))
const readyCount = computed(() => GAMES.filter((g) => g.status === 'ready' || g.status === 'registered').length)
const activeDepots = computed(() => (activeGame.value ? includedDepots(activeGame.value) : []))
const hasKey = computed(() => hubcapKey.value.trim().length >= 8)

/* ------------------------------ helpers ------------------------------ */

function totalSize(game) {
  return game.depots.reduce((n, d) => n + d.size, 0)
}

function depotOf(game, depot) {
  return `${game.id}:${depot.id}`
}

function depotOn(game, depot) {
  return !excluded.value.has(depotOf(game, depot))
}

function includedDepots(game) {
  return game.depots.filter((d) => depotOn(game, d))
}

function isLight(game) {
  const hex = game.accent.replace('#', '')
  const channels = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
  const lin = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  const luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
  // White on this accent reads below 4.5:1, so the tile switches to ink.
  return 1.05 / (luminance + 0.05) < 4.5
}

function maskKey(key) {
  return `${key.slice(0, 6)}…${key.slice(-6)}`
}

function fmtGB(n) {
  return `${n.toFixed(1)} GB`
}

function toggleSet(set, value) {
  const next = new Set(set.value)
  next.has(value) ? next.delete(value) : next.add(value)
  set.value = next
}

function toggleDepot(game, depot) {
  toggleSet(excluded, depotOf(game, depot))
}

function toggleReveal(id) {
  toggleSet(revealed, id)
}

function toggleSelect(id) {
  toggleSet(selected, id)
}

function toggleAllVisible() {
  const ids = filtered.value.map((g) => g.id)
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
  charmOpen.value = false
  bridgeOpen.value = false
}

function pickAccent(hex) {
  accent.value = hex
  charmOpen.value = false
  toast('accent updated', hex)
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

async function probeBridge() {
  if (probing.value) return
  probing.value = true
  bridgeOpen.value = true
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 3200)
  try {
    const res = await fetch('http://127.0.0.1:1952/health', {
      signal: controller.signal,
      mode: 'cors',
      cache: 'no-store'
    })
    if (!res.ok) throw new Error(`http ${res.status}`)
    const body = await res.json().catch(() => ({}))
    bridge.value = true
    bridgeVersion.value = body.version || 'running'

    // The console token decides whether this machine will act for us. /health
    // is unauthenticated, so ask /v1/session before claiming it is paired.
    paired.value = false
    if (bridgeToken.value) {
      const session = await fetch('http://127.0.0.1:1952/v1/session', {
        signal: controller.signal,
        mode: 'cors',
        cache: 'no-store',
        headers: { Authorization: `Bearer ${bridgeToken.value}` }
      })
      paired.value = session.ok
    }

    if (paired.value) toast('console paired', `epsilon ${bridgeVersion.value}`)
    else if (bridge.value) toast('bridge connected', 'Paste the console token from ASSella to register games.')
  } catch {
    bridge.value = false
    paired.value = false
    bridgeVersion.value = ''
    toast('bridge not reachable', 'Epsilon is not running on 127.0.0.1:1952 — export instead.')
  } finally {
    clearTimeout(timer)
    probing.value = false
  }
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
  const list = games && games.length ? games : activeGame.value ? [activeGame.value] : []
  if (!list.length) return
  copyText(yamlFor(list), 'config.yaml snippet')
}

function copyLua(games) {
  const list = games && games.length ? games : activeGame.value ? [activeGame.value] : []
  if (!list.length) return
  copyText(luaFor(list), '.lua contents')
}

function register(games) {
  const list = games && games.length ? games : activeGame.value ? [activeGame.value] : []
  if (!list.length) return
  // The catalog is still demo data. Epsilon's /v1/register would really add
  // these appids (they are real Steam IDs), so the preview exports instead.
  // Swap this for the real call once the tiles are fed by /v1/library.
  toast(
    'preview catalog',
    paired.value
      ? 'Registrations are disabled while the catalog is demo data — exporting instead.'
      : 'Pair with Epsilon to enable registering — exporting instead.'
  )
  exportLua(list)
}

/* ----------------------------- lifecycle ----------------------------- */

function onDocumentClick(e) {
  if (!e.target.closest?.('.popover-anchor')) closePopovers()
}

function isTyping() {
  const el = document.activeElement
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
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
    searchEl.value?.focus()
    return
  }
  if (isTyping() || e.metaKey || e.ctrlKey || e.altKey) return
  if (e.key === '/' || (e.key.length === 1 && /[\w-]/.test(e.key))) {
    searchEl.value?.focus()
  }
}

function cycleLiveTiles() {
  if (document.hidden) return
  const tiles = GAMES.filter((g) => filtered.value.includes(g))
  if (tiles.length < 2) return
  const next = new Set(flipped.value)
  for (let i = 0; i < 2; i += 1) {
    const pick = tiles[Math.floor(Math.random() * tiles.length)]
    if (!pick) continue
    next.has(pick.id) ? next.delete(pick.id) : next.add(pick.id)
  }
  flipped.value = next
}

onMounted(() => {
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

  // ASSella hands the console token over in the fragment, which the web host
  // never sees. Store it, then strip it from the address bar and history.
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
  }

  document.addEventListener('click', onDocumentClick)
  window.addEventListener('keydown', onKeydown)
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  if (!reduce) liveTimer = setInterval(cycleLiveTiles, 5200)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  window.removeEventListener('keydown', onKeydown)
  if (liveTimer) clearInterval(liveTimer)
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

watch(activeId, () => {
  closePopovers()
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
          <span class="chip">preview</span>
        </div>
        <h1>at0m</h1>
        <nav class="pivots" aria-label="catalog pivots">
          <button
            v-for="p in PIVOTS"
            :key="p.id"
            type="button"
            class="pivot"
            :class="{ active: pivot === p.id }"
            :aria-pressed="pivot === p.id"
            @click="pivot = p.id"
          >
            {{ p.label }}
            <span v-if="p.id !== 'catalog'" class="pivot-count">
              {{ GAMES.filter((g) => g.status === p.id).length }}
            </span>
          </button>
        </nav>
      </div>

      <div class="hd-right">
        <div class="stats" role="status">
          <div class="stat">
            <span class="stat-k">results</span>
            <span class="stat-v">{{ filtered.length }}</span>
          </div>
          <div class="stat">
            <span class="stat-k">ready</span>
            <span class="stat-v">{{ readyCount }}</span>
          </div>
          <div class="stat">
            <span class="stat-k">selected</span>
            <span class="stat-v accent">{{ selected.size }}</span>
          </div>
        </div>

        <div class="search">
          <input
            ref="searchEl"
            v-model="query"
            type="search"
            class="search-input"
            placeholder="search title, appid, tag…"
            autocomplete="off"
            spellcheck="false"
            aria-label="Search the catalog"
          />
          <button
            v-if="query"
            type="button"
            class="search-clear"
            title="Clear (Esc)"
            @click="query = ''"
          >
            ✕
          </button>
          <div class="search-go" aria-hidden="true">
            <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></svg>
          </div>
        </div>

        <div class="tools">
          <button
            type="button"
            class="tool bridge popover-anchor"
            :class="{ live: bridge }"
            :aria-expanded="bridgeOpen"
            @click.stop="bridgeOpen = !bridgeOpen"
          >
            <span class="dot" />
            <span>bridge · {{ paired ? 'paired' : bridge ? 'online' : 'offline' }}</span>
          </button>

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

          <div class="popover-anchor accent-wrap">
            <button
              type="button"
              class="tool accent-btn"
              :aria-expanded="charmOpen"
              @click.stop="charmOpen = !charmOpen"
            >
              <span class="swatch" />
              <span>accent</span>
            </button>
            <div v-if="charmOpen" class="popover accent-pop" @click.stop>
              <div class="pop-k">start accent</div>
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
              <p class="pop-note">Right-click a tile to multi-select. Type anywhere to search.</p>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- ==================== bridge popover ==================== -->
    <div v-if="bridgeOpen" class="bridge-pop" @click.stop>
      <div class="pop-k">epsilon bridge</div>
      <p class="pop-p">
        The page can read the catalog and hand you a recipe, but only a local service can write
        <code>~/.config/SLSsteam/config.yaml</code>.
      </p>
      <dl class="bridge-dl">
        <dt>endpoint</dt>
        <dd>127.0.0.1:1952</dd>
        <dt>state</dt>
        <dd>{{ bridge ? `connected · ${bridgeVersion}` : 'not running' }}</dd>
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
        <button type="button" class="btn" :disabled="probing" @click="probeBridge">
          {{ probing ? 'checking…' : 'check again' }}
        </button>
        <button type="button" class="btn ghost" @click="bridgeOpen = false">close</button>
      </div>
    </div>

    <!-- ==================== catalog ==================== -->
    <main class="main">
      <div v-if="isSearching" class="filter-bar">
        <div>
          showing <strong>{{ filtered.length }}</strong> matching titles
          <span v-if="query" class="filter-q">“{{ query }}”</span>
        </div>
        <button type="button" class="btn tiny" @click="query = ''; pivot = 'catalog'">reset view</button>
      </div>

      <div v-if="!filtered.length" class="empty">
        <div class="empty-title">nothing matched</div>
        <p>Try another title, an AppID, or a tag like <em>roguelike</em> or <em>co-op</em>.</p>
        <button type="button" class="btn primary" @click="query = ''; pivot = 'catalog'">show everything</button>
      </div>

      <template v-else>
        <section v-for="section in sections" :key="section.id" class="group">
          <div class="group-hd">
            <h2>{{ section.label }} <span class="badge">{{ section.games.length }}</span></h2>
            <span class="hint">{{ section.id === 'featured' ? 'click to inspect · right-click to select' : 'live tiles cycle the recipe every few seconds' }}</span>
          </div>
          <div class="grid">
            <article
              v-for="(g, i) in section.games"
              :key="g.id"
              class="tile"
              :class="[g.tile, { light: isLight(g), sel: selected.has(g.id), act: activeId === g.id, flipped: flipped.has(g.id) }]"
              :style="{ background: g.accent, animationDelay: Math.min(i * 22, 240) + 'ms' }"
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
                <div class="face">
                  <div class="face-top">
                    <span class="status"><i class="dot" />{{ STATUS_LABEL[g.status] }}</span>
                    <span class="appid">#{{ g.appId }}</span>
                  </div>
                  <div class="emblem">
                    <svg viewBox="0 0 64 64" v-html="GLYPHS[g.glyph]" />
                  </div>
                  <div class="face-bottom">
                    <div class="title-wrap">
                      <div class="title">{{ g.title }}</div>
                      <div v-if="g.tile !== 't1x1'" class="sub">{{ g.tags.slice(0, 2).join(' · ') }}</div>
                    </div>
                    <div class="meta">
                      <div>{{ fmtGB(totalSize(g)) }}</div>
                      <div v-if="g.tile !== 't1x1'" class="dim">
                        {{ g.depots.length }} depot{{ g.depots.length > 1 ? 's' : '' }}
                      </div>
                    </div>
                  </div>
                </div>

                <div class="face face-2">
                  <div class="face-top">
                    <span class="face-k">recipe</span>
                    <span class="appid">{{ g.updated }}</span>
                  </div>
                  <dl class="facts">
                    <div><dt>build</dt><dd>{{ g.buildId }}</dd></div>
                    <div><dt>depots</dt><dd>{{ includedDepots(g).length }} / {{ g.depots.length }}</dd></div>
                    <div><dt>keys</dt><dd>{{ g.depots.filter((d) => d.key).length }} / {{ g.depots.length }}</dd></div>
                    <div v-if="g.tile === 't2x2'"><dt>dlc</dt><dd>{{ g.dlc.length }}</dd></div>
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
    <div v-if="activeGame" class="scrim" @click="closeFlyout" />

    <aside class="flyout" :class="{ open: !!activeGame }" aria-label="title inspector">
      <template v-if="activeGame">
        <div class="fo-hd">
          <div class="super"><span class="sq" />title inspector</div>
          <button type="button" class="icon-btn" title="Close (Esc)" @click="closeFlyout">✕</button>
        </div>

        <div class="fo-banner" :style="{ background: activeGame.accent, color: isLight(activeGame) ? '#111' : '#fff' }">
          <div class="fo-banner-top">
            <span class="status solid">{{ STATUS_LABEL[activeGame.status] }}</span>
            <span class="fo-year">{{ activeGame.year }}</span>
          </div>
          <h3>{{ activeGame.title }}</h3>
          <p>{{ activeGame.developer }}</p>
          <div class="fo-emblem">
            <svg viewBox="0 0 64 64" v-html="GLYPHS[activeGame.glyph]" />
          </div>
        </div>

        <div class="fo-body">
          <dl class="fo-meta">
            <div><dt>appid</dt><dd>{{ activeGame.appId }}</dd></div>
            <div><dt>build</dt><dd>{{ activeGame.buildId }}</dd></div>
            <div><dt>depots</dt><dd>{{ activeDepots.length }} / {{ activeGame.depots.length }}</dd></div>
            <div><dt>size</dt><dd>{{ fmtGB(activeDepots.reduce((n, d) => n + d.size, 0)) }}</dd></div>
          </dl>

          <section class="fo-sec">
            <div class="fo-sec-hd">
              <h4>depots</h4>
              <button type="button" class="link" @click="activeGame.depots.forEach((d) => toggleDepot(activeGame, d))">
                toggle all
              </button>
            </div>
            <ul class="depots">
              <li v-for="d in activeGame.depots" :key="d.id" class="depot" :class="{ off: !depotOn(activeGame, d) }">
                <label class="depot-check">
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
                    <span class="depot-size">{{ fmtGB(d.size) }}</span>
                  </div>
                  <div class="depot-line sub">
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
                  </div>
                </div>
              </li>
            </ul>
          </section>

          <section v-if="activeGame.dlc.length" class="fo-sec">
            <div class="fo-sec-hd"><h4>dlc</h4></div>
            <div class="chips">
              <span v-for="dlc in activeGame.dlc" :key="dlc.appId" class="chip-dlc">
                {{ dlc.name }} <em>{{ dlc.appId }}</em>
              </span>
            </div>
          </section>

          <section class="fo-sec">
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
              Stored in <code>localStorage</code> on this origin. Never sent to us — only to Hubcap,
              from your browser.
            </p>
          </section>

          <section class="fo-sec">
            <div class="fo-sec-hd"><h4>apply</h4></div>
            <button
              v-if="hasKey && bridge"
              type="button"
              class="btn primary wide"
              @click="register()"
            >
              register to at0-m
            </button>
            <button
              v-else
              type="button"
              class="btn primary wide"
              @click="exportLua()"
            >
              export .lua
            </button>
            <div class="btn-row">
              <button type="button" class="btn" @click="exportLua()">download .lua</button>
              <button type="button" class="btn" @click="copyYaml()">copy yaml</button>
              <button type="button" class="btn" @click="copyLua()">copy lua</button>
            </div>
            <p class="fo-fine">
              <template v-if="!hasKey">Add a Hubcap key to unlock the register path.</template>
              <template v-else-if="!bridge">
                Bridge offline — export the recipe and import it in ASSella
                (<span class="mono">Library → Scan for LUA</span>), or install Epsilon.
              </template>
              <template v-else>
                Epsilon will write <span class="mono">config.yaml</span> and Steam will start the download.
              </template>
            </p>
            <a
              v-if="activeGame.status === 'registered' || activeGame.status === 'ready'"
              class="steam-link"
              :href="`steam://install/${activeGame.appId}`"
            >
              open steam://install/{{ activeGame.appId }}
            </a>
          </section>
        </div>

        <div class="fo-ft">
          <span>demo catalog · hubcap + steam fetch not wired yet</span>
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
            <div class="count-sub">{{ fmtGB(selectedSize) }} · {{ selectedGames.length }} recipe{{ selectedGames.length === 1 ? '' : 's' }}</div>
          </div>
        </div>
        <div class="appbar-actions">
          <button type="button" class="ab-item" @click="register(selectedGames)">
            <span class="ab-ring"><svg viewBox="0 0 24 24"><path d="M12 3v13M7 11l5 5 5-5M5 21h14" /></svg></span>
            <span>register</span>
          </button>
          <button type="button" class="ab-item" @click="exportLua(selectedGames)">
            <span class="ab-ring"><svg viewBox="0 0 24 24"><path d="M12 3v13M7 11l5 5 5-5M5 21h14" /></svg></span>
            <span>export .lua</span>
          </button>
          <button type="button" class="ab-item" @click="copyYaml(selectedGames)">
            <span class="ab-ring"><svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" /><path d="M16 8V4H4v12h4" /></svg></span>
            <span>copy yaml</span>
          </button>
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
  padding-bottom: 7rem;
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
  gap: 1.25rem 2rem;
  padding: 1.5rem clamp(1rem, 3vw, 2.5rem) 1rem;
  background: var(--bg);
  border-bottom: 1px solid var(--line);
}

.hd-left {
  min-width: 0;
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
  margin: 0.35rem 0 0.7rem;
  font-size: clamp(2.6rem, 6vw, 4.25rem);
  font-weight: 200;
  letter-spacing: -0.035em;
  line-height: 0.92;
  color: var(--text);
}

.pivots {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem 1.6rem;
}

.pivot {
  position: relative;
  font-size: clamp(1.1rem, 2.2vw, 1.45rem);
  font-weight: 300;
  color: #6f6f6f;
  padding: 0 0 0.3rem;
  transition: color 0.14s ease;
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
  margin-left: 0.35rem;
  vertical-align: super;
}

.hd-right {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.6rem;
  min-width: min(100%, 320px);
}

.stats {
  display: flex;
  gap: 0;
  border: 1px solid var(--line);
  background: var(--panel);
}

.stat {
  flex: 1;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.35rem 0.7rem;
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
  background: var(--panel-2);
  border: 2px solid var(--line-2);
  transition: border-color 0.14s ease;
}
.search:focus-within {
  border-color: var(--text);
}
.search-input {
  flex: 1;
  min-width: 0;
  padding: 0.55rem 0.7rem;
  background: transparent;
  border: 0;
  color: var(--text);
  font: inherit;
  font-size: 0.85rem;
  outline: none;
  appearance: none;
}
.search-input::-webkit-search-cancel-button {
  display: none;
}
.search-input::placeholder {
  color: #6d6d6d;
}
.search-clear {
  padding: 0 0.6rem;
  color: var(--muted);
  font-size: 0.8rem;
}
.search-clear:hover {
  color: var(--text);
}
.search-go {
  display: grid;
  place-items: center;
  width: 40px;
  background: var(--accent);
  color: #fff;
  flex: none;
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
.tool.bridge.live .dot {
  background: #3ecf6a;
}
.tool.bridge.live {
  color: #fff;
  border-color: #3ecf6a;
}
.accent-btn .swatch {
  width: 12px;
  height: 12px;
  background: var(--accent);
  border: 1px solid #ffffff55;
}

.popover-anchor {
  position: relative;
}

.popover {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 60;
  width: 250px;
  padding: 0.85rem;
  background: #181818;
  border: 1px solid #fff;
}
.pop-k {
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.6rem;
}
.swatches {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}
.swatch-btn {
  height: 30px;
  border: 2px solid transparent;
}
.swatch-btn.on {
  border-color: #fff;
  outline: 1px solid #000;
  outline-offset: -4px;
}
.pop-note {
  margin: 0.7rem 0 0;
  padding-top: 0.6rem;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--muted);
}

/* --------------------------- bridge popover --------------------------- */

.bridge-pop {
  position: fixed;
  top: 8.5rem;
  right: clamp(1rem, 3vw, 2.5rem);
  z-index: 70;
  width: min(340px, calc(100% - 2rem));
  padding: 1rem;
  background: #171717;
  border: 1px solid #fff;
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
  gap: 0.3rem;
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
  padding: 4rem 1rem;
  text-align: center;
  border: 1px solid var(--line);
  background: #101010;
}
.empty-title {
  font-size: clamp(1.5rem, 4vw, 2.25rem);
  font-weight: 200;
  letter-spacing: -0.02em;
  margin-bottom: 0.5rem;
}
.empty p {
  margin: 0 0 1.5rem;
  color: var(--muted);
  font-size: 13px;
}
.empty em {
  color: var(--muted-2);
  font-style: normal;
  border-bottom: 1px solid var(--line-2);
}

.group {
  margin-bottom: 2rem;
}
.group-hd {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.55rem;
}
.group-hd h2 {
  margin: 0;
  font-size: 1.05rem;
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
  letter-spacing: 0.02em;
}

/* -------------------------------- grid -------------------------------- */

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  grid-auto-rows: 148px;
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
}
.tile.light {
  color: #101010;
}
.tile:hover {
  outline-color: rgba(255, 255, 255, 0.5);
}
.tile.light:hover {
  outline-color: rgba(0, 0, 0, 0.45);
}
.tile:active {
  transform: scale(0.985);
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
.t2x2 {
  grid-column: span 2;
  grid-row: span 2;
}

@media (max-width: 430px) {
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-auto-rows: 140px;
  }
  .t2x2 {
    grid-column: span 2;
    grid-row: span 2;
  }
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
  z-index: 5;
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
  border-color: transparent rgba(0, 0, 0, 0.4) transparent transparent;
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
  transition: transform 0.6s cubic-bezier(0.19, 1, 0.22, 1);
}
.tile.flipped .track {
  transform: translateY(-50%);
}
.tile:hover .track {
  transform: translateY(0);
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
  background: rgba(0, 0, 0, 0.26);
}
.tile.light .face-2 {
  background: rgba(0, 0, 0, 0.1);
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
  padding: 0.12rem 0.32rem;
  background: rgba(0, 0, 0, 0.28);
}
.tile.light .status {
  background: rgba(0, 0, 0, 0.14);
}
.status .dot {
  width: 5px;
  height: 5px;
  background: currentColor;
  flex: none;
}
.status.solid {
  background: rgba(0, 0, 0, 0.35);
}
.appid {
  font-family: var(--mono);
  font-size: 9.5px;
  opacity: 0.75;
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
  width: 58px;
  height: 58px;
}
.t2x1 .face {
  flex-direction: row;
  align-items: center;
  gap: 0.9rem;
}
.t2x1 .face-top {
  position: absolute;
  top: 0.7rem;
  left: 0.8rem;
  right: 0.8rem;
  width: auto;
}
.t2x1 .emblem {
  flex: none;
}
.t2x1 .face-bottom {
  flex: 1;
  min-width: 0;
}
.t2x1 .face {
  padding-top: 2rem;
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
.title {
  font-size: 0.82rem;
  font-weight: 500;
  line-height: 1.15;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.t2x2 .title {
  font-size: 1.5rem;
  font-weight: 300;
  letter-spacing: -0.02em;
  white-space: normal;
}
.t2x1 .title {
  font-size: 0.95rem;
  font-weight: 500;
}
.sub {
  font-size: 10px;
  opacity: 0.78;
  margin-top: 0.12rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  text-align: right;
  font-family: var(--mono);
  font-size: 9.5px;
  opacity: 0.85;
  flex: none;
}
.meta .dim {
  opacity: 0.7;
}

.facts {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
  font-size: 10.5px;
  flex: 1;
  justify-content: center;
}
.facts div {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  border-bottom: 1px solid currentColor;
  border-color: color-mix(in srgb, currentColor 18%, transparent);
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

.scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: none;
  background: rgba(0, 0, 0, 0.5);
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
  width: min(430px, 100%);
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
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.85rem 1rem;
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
  padding: 1.2rem 1.15rem 1.35rem;
  overflow: hidden;
  background-image: linear-gradient(135deg, rgba(255, 255, 255, 0.07) 25%, transparent 25%),
    linear-gradient(225deg, rgba(255, 255, 255, 0.07) 25%, transparent 25%);
  background-size: 30px 30px;
}
.fo-banner-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.75rem;
}
.fo-year {
  font-family: var(--mono);
  font-size: 10px;
  opacity: 0.85;
}
.fo-banner h3 {
  position: relative;
  z-index: 1;
  margin: 0;
  font-size: clamp(1.6rem, 5vw, 2.1rem);
  font-weight: 300;
  letter-spacing: -0.025em;
  line-height: 1.05;
  max-width: 78%;
}
.fo-banner p {
  position: relative;
  z-index: 1;
  margin: 0.35rem 0 0;
  font-size: 11.5px;
  opacity: 0.85;
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
  top: 0px;
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
.key.missing {
  color: #e0a13a;
  border: 0;
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
.btn:hover {
  background: #2a2a2a;
  border-color: #fff;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  padding: 0.7rem 1rem;
  font-size: 11px;
}
.btn.primary:hover {
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
  opacity: 0.55;
  cursor: default;
}
.btn-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.35rem;
  margin-top: 0.4rem;
}
.btn-row .btn {
  font-size: 9.5px;
  letter-spacing: 0.08em;
  padding: 0.45rem 0.3rem;
}

.fo-fine {
  margin: 0.6rem 0 0;
  font-size: 10.5px;
  line-height: 1.55;
  color: #8f8f8f;
}
.steam-link {
  display: block;
  margin-top: 0.6rem;
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
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
.ab-item:hover .ab-ring {
  background: #fff;
  color: #111;
}
.ab-item:active .ab-ring {
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

@media (max-width: 720px) {
  .hd {
    position: static;
    padding-top: 1rem;
  }
  .stats {
    display: none;
  }
  .bridge-pop {
    top: 1rem;
    left: 1rem;
    right: 1rem;
    width: auto;
  }
  .t2x1 {
    grid-column: span 2;
  }
  .flyout {
    width: 100%;
  }
  .scrim {
    display: block;
  }
  .appbar-in {
    justify-content: center;
  }
  .appbar-actions {
    gap: 0.9rem;
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
