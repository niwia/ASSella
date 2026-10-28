-- spacebunny.lua (1.8.3)
--
-- SLSsteam download interceptor: rewrites package 0 depot lists, injects
-- configured AES decryption keys, and resolves manifest request codes.
--
-- ======================= 1.8.1 changes from 1.8.0 =====================
--  38. All Hubcap auto-fetch logic moved to hubcap.lua. This plugin now
--      focuses solely on download interception (depot injection, key
--      serving, MRC resolution). When hubcap.lua is loaded, SpaceBunny
--      delegates missing-key resolution to HubcapResolver via graceful
--      pcall checks. When it is absent, everything still works using
--      only the keys from config.yaml.
--
-- ======================= 1.8.2 changes from 1.8.1 =====================
--  39. Fixed the split's load-order regression: the clock/formatting
--      helpers and providerKey now sit above the breaker, and the libc
--      FFI block sits above validateMRC. In 1.8.1 those were later
--      locals, so GetMRC saw nil globals and returned no MRC at all.
--  40. Canonical filename is spacebunny.lua. Hubcap is an optional
--      resolver companion; SpaceBunny runs without it.
--
-- ======================= 1.8.3 changes from 1.8.2 =====================
--  41. mrc_config.lua is reloadable. The provider list and tuning are
--      re-read on SLSsteam::luaReload (caches and breaker state reset), so
--      editing the file no longer needs a Steam restart. A broken config
--      keeps the previous providers instead of half-applying itself, and
--      parse errors / invalid provider entries / unknown tuning keys are
--      now logged by name.
--  42. Fixed budget starvation in the provider chain. With the shipped
--      four-provider mrc_config.lua a stalled primary plus its proxy used
--      the whole 8s budget, so opensteamtool and the trailing proxy were
--      always reported "no-budget" and never actually tried. Each provider
--      still ahead in the list now reserves MIN_ATTEMPT_SEC (default 1s),
--      and the proxy variant respects that reservation too.
--  43. Dropped the hard-coded "dead proxy" URL blacklist: the worker's
--      /manifest route now answers 200 with an MRC, so blacklisting it
--      silently removed a working proxy from the config.
--
-- Everything from 1.8.0 is kept: multi-provider MRC with classified
-- circuit breakers, total budget, provisional/trusted caching, URL
-- repair, monotonic clock, runInHook safety wrapper, restoreDepotIds,
-- stale pointer re-baselining, and all earlier fixes (1.5 through 1.7.9).
--
-- Local test build. Not hashed into plugins_manifest.json, not published.

SpaceBunny = SpaceBunny or {
	setup = false,

	-- Hooks
	getPackageHook = nil, getPackageTramp = nil,
	getBinaryHook = nil, getBinaryTramp = nil,
	getMRCHook = nil, getMRCTramp = nil,

	-- GetPackage vars
	PackageDepotIds = {},
	OriginalDepotCaptured = false,
	Package = nil,
	RewriteDepots = false,

	-- GetMRC vars
}

SpaceBunny.VERSION = "1.8.3"

SpaceBunny.DEBUG = false

-- SLSsteam re-runs every plugin file on any change in plugins/.  Hooks are
-- already installed after the first successful load; re-running would patch
-- the same functions again and stack callbacks.  Config reloads still go
-- through the luaReload callback registered below.
if SpaceBunny.setup then
	return
end

-- =========================================================================
-- Hubcap bridge
--
-- hubcap.lua exposes the HubcapResolver global. We never require it;
-- we just check if it exists and is set up before calling into it.
-- Every call is wrapped so a missing or broken hubcap.lua cannot
-- affect SpaceBunny's core download interception.
-- =========================================================================

local function hubcapEnabled()
	return type(HubcapResolver) == "table"
		and HubcapResolver.setup == true
		and type(HubcapResolver.HUBCAP) == "table"
		and HubcapResolver.HUBCAP.enabled == true
end

local function hubcapResolveDepotKey(depotId, appId)
	if not hubcapEnabled() then
		return nil, "Hubcap not available"
	end

	local ok, key, source = pcall(HubcapResolver.resolveDepotKey, depotId, appId)

	if not ok then
		SpaceBunny.safeLog("warn", "HubcapResolver.resolveDepotKey threw: " .. tostring(key))
		return nil, "hubcap error"
	end

	return key, source
end

local function hubcapLearnDepotApp(depotId, appId)
	if not hubcapEnabled() then
		return
	end

	pcall(function()
		if type(HubcapResolver.learnDepotApp) == "function" then
			HubcapResolver.learnDepotApp(depotId, appId)
		end
	end)
end

local function hubcapSyncKeys(keysTable)
	if not hubcapEnabled() then
		return
	end

	pcall(function()
		if type(HubcapResolver.syncKeys) == "function" then
			HubcapResolver.syncKeys(keysTable)
		end
	end)
end

-- =========================================================================
-- MRC authority model (1.8)
-- =========================================================================

SpaceBunny.MRC_PROVIDERS = {
	{
		name = "manifestdex",
		url = "https://manifest.manifestdex.com/",
		proxy_url = nil,
		headers = { "User-Agent: ManifestDeX/1.0" },
		is_proxy = false,
		provisional = true,
		timeout = 5,
		max_junk_skips = 2,
		junk_skip_duration = 300,
		max_transient_skips = 3,
		transient_skip_duration = 30,
	},
	{
		name = "wudrm",
		url = "https://gmrc.wudrm.com/manifest/",
		proxy_url = "https://rapid-thunder-fba1wirecutter.7ucking.workers.dev/rescue/",
		headers = nil,
		is_proxy = false,
		provisional = false,
		timeout = 4,
		max_junk_skips = 2,
		junk_skip_duration = 300,
		max_transient_skips = 3,
		transient_skip_duration = 30,
	},
	-- OpenSteamTool is disabled for now. Restore this entry when its
	-- Cloudflare challenge is no longer blocking MRC requests:
	-- {
	--	name = "opensteamtool",
	--	url = "https://manifest.opensteamtool.com/manifest/",
	--	proxy_url = nil,
	--	headers = { "User-Agent: OpenSteamTool/1.0" },
	--	is_proxy = false,
	--	provisional = true,
	--	timeout = 4,
	--	max_junk_skips = 2,
	--	junk_skip_duration = 300,
	--	max_transient_skips = 3,
	--	transient_skip_duration = 30,
	-- },
}

local DEFAULT_PROVIDERS = SpaceBunny.MRC_PROVIDERS

local GENERIC_PROVIDER_DEFAULTS = {
	headers = nil,
	is_proxy = false,
	provisional = true,
	timeout = 5,
	max_junk_skips = 2,
	junk_skip_duration = 300,
	max_transient_skips = 3,
	transient_skip_duration = 30,
	proxy_url = nil,
}

local KNOWN_BAD_URLS = {
	["https://manifest.manifestdex.com/manifest"] = "https://manifest.manifestdex.com/",
	["http://manifest.manifestdex.com/manifest"] = "https://manifest.manifestdex.com/",
}

local function trimTrailingSlash(url)
	return (tostring(url):gsub("/+$", ""))
end

local function isUrl(u)
	return type(u) == "string" and u ~= "" and u:match("^https?://") ~= nil
end

local function providerDefaults(name)
	for _, p in ipairs(DEFAULT_PROVIDERS) do
		if p.name == name then
			return p
		end
	end

	return GENERIC_PROVIDER_DEFAULTS
end

SpaceBunny.resolveProvider = function(cfg)
	if type(cfg) ~= "table" then
		return nil
	end

	if type(cfg.name) ~= "string" or cfg.name == "" or cfg.name == "none" then
		return nil
	end

	if not isUrl(cfg.url) then
		return nil
	end

	if cfg.headers ~= nil and cfg.headers ~= false and type(cfg.headers) ~= "table" then
		return nil
	end

	local defaults = providerDefaults(cfg.name)

	local out = {
		name = cfg.name,
		url = cfg.url,
		timeout = tonumber(cfg.timeout) or defaults.timeout,
		max_junk_skips = tonumber(cfg.max_junk_skips) or defaults.max_junk_skips,
		junk_skip_duration = tonumber(cfg.junk_skip_duration) or defaults.junk_skip_duration,
		max_transient_skips = tonumber(cfg.max_transient_skips) or defaults.max_transient_skips,
		transient_skip_duration = tonumber(cfg.transient_skip_duration) or defaults.transient_skip_duration,
	}

	if cfg.provisional ~= nil then
		out.provisional = cfg.provisional and true or false
	else
		out.provisional = defaults.provisional and true or false
	end

	if cfg.headers == false then
		out.headers = nil
	elseif cfg.headers ~= nil then
		out.headers = cfg.headers
	else
		out.headers = defaults.headers
	end

	if cfg.is_proxy ~= nil then
		out.is_proxy = cfg.is_proxy and true or false
	else
		out.is_proxy = defaults.is_proxy and true or false
	end

	if cfg.proxy_url == false then
		out.proxy_url = nil
	elseif isUrl(cfg.proxy_url) then
		out.proxy_url = cfg.proxy_url
	else
		out.proxy_url = defaults.proxy_url
	end

	local fixedUrl = KNOWN_BAD_URLS[trimTrailingSlash(out.url)]

	if fixedUrl ~= nil then
		print("[SpaceBunny] Repaired " .. out.name .. " URL: " .. out.url
			.. " -> " .. fixedUrl)
		out.url = fixedUrl
	end

	-- Proxy URLs are no longer blacklisted here. The one entry this table
	-- used to hold (the worker's /manifest route) started answering 200 with
	-- an MRC on 2026-10-27, so a hard-coded list only produced false
	-- negatives. A proxy that is down is classified at request time like any
	-- other provider.

	if out.name == "wudrm" and out.url:match("^http://") then
		out.url = out.url:gsub("^http://", "https://", 1)
		print("[SpaceBunny] Upgraded wudrm provider URL to https")
	end

	return out
end

SpaceBunny.MRC_AUTHORITY = SpaceBunny.MRC_PROVIDERS[1]
SpaceBunny.MRC_RESCUE = SpaceBunny.MRC_PROVIDERS[2]

SpaceBunny.MRC_TUNING = {
	FULL_SEC = 5,
	PROBE_SEC = 2,
	PROBE_TIMEOUTS_TO_MARK_DOWN = 2,
	DOWN_TTL_MS = 15000,
	TOTAL_BUDGET_SEC = 8,
	-- Floor (and per-provider reservation) for the chain. A provider list is
	-- only useful if every entry is actually reachable: without this, one
	-- slow primary consumes the whole budget and the tail of the list is
	-- always reported as "no-budget".
	MIN_ATTEMPT_SEC = 1,
	RECOVER_AFTER = 2,
	HEALTHY_MS = 800,
	PROVISIONAL_TTL = 300,
	AUTHORITY_TTL = 86400,
	FAIL_TTL = 120,
	ALPHA = 0.3,
	MRCCACHE_MAX = 5000,
	MRCFAILCACHE_MAX = 2000,
}

-- Snapshot of the built-in tuning so a reload starts from a clean slate
-- instead of inheriting keys from a config file that was just removed.
local BASE_MRC_TUNING = {}

for k, v in pairs(SpaceBunny.MRC_TUNING) do
	BASE_MRC_TUNING[k] = v
end

SpaceBunny.resetMrcTuning = function()
	for k, v in pairs(BASE_MRC_TUNING) do
		SpaceBunny.MRC_TUNING[k] = v
	end
end

local function sanitizeMrcTuning(t)
	if type(t) ~= "table" then
		return
	end

	if t.FULL_MS ~= nil and t.FULL_SEC == nil then
		local v = tonumber(t.FULL_MS)
		if v ~= nil then
			t.FULL_SEC = v / 1000
		end
	end
	if t.PROBE_MS ~= nil and t.PROBE_SEC == nil then
		local v = tonumber(t.PROBE_MS)
		if v ~= nil then
			t.PROBE_SEC = v / 1000
		end
	end
	t.FULL_MS = nil
	t.PROBE_MS = nil

	local function num(key, lo, hi)
		local v = t[key]
		if v == nil then
			return
		end
		v = tonumber(v)
		if v == nil or v < lo or v > hi then
			print("[SpaceBunny] Invalid MRC tuning " .. key .. ", keeping default.")
			t[key] = nil
		else
			t[key] = v
		end
	end

	num("FULL_SEC", 0.5, 30)
	num("PROBE_SEC", 0.2, 10)
	num("PROBE_TIMEOUTS_TO_MARK_DOWN", 1, 20)
	num("DOWN_TTL_MS", 1000, 600000)
	num("TOTAL_BUDGET_SEC", 2, 30)
	num("MIN_ATTEMPT_SEC", 0.2, 5)
	num("RECOVER_AFTER", 1, 20)
	num("HEALTHY_MS", 100, 5000)
	num("PROVISIONAL_TTL", 10, 86400)
	num("AUTHORITY_TTL", 60, 604800)
	num("FAIL_TTL", 10, 3600)
	num("MRCCACHE_MAX", 100, 50000)
	num("MRCFAILCACHE_MAX", 100, 20000)
	if t.ALPHA ~= nil then
		local a = tonumber(t.ALPHA)
		if a == nil or a <= 0 or a >= 1 then
			print("[SpaceBunny] Invalid MRC tuning ALPHA, keeping default.")
			t.ALPHA = nil
		else
			t.ALPHA = a
		end
	end
end

SpaceBunny.applyMrcTuning = function(user)
	if type(user) ~= "table" then
		return
	end

	local candidate = {}

	for k, v in pairs(user) do
		candidate[k] = v
	end

	sanitizeMrcTuning(candidate)

	for k in pairs(candidate) do
		if SpaceBunny.MRC_TUNING[k] == nil then
			print("[SpaceBunny] Ignoring unknown MRC tuning key '" .. tostring(k) .. "'")
		end
	end

	for k, v in pairs(candidate) do
		if SpaceBunny.MRC_TUNING[k] ~= nil then
			SpaceBunny.MRC_TUNING[k] = v
		end
	end
end

SpaceBunny.logProviderSummary = function()
	local list = SpaceBunny.MRC_PROVIDERS
	local t = SpaceBunny.MRC_TUNING

	print("[SpaceBunny] MRC providers loaded: " .. tostring(#list)
		.. " (total budget " .. tostring(t.TOTAL_BUDGET_SEC) .. "s)")

	for i, p in ipairs(list) do
		local role

		if p.is_proxy then
			role = "proxy"
		elseif p.provisional then
			role = "provisional"
		else
			role = "trusted"
		end

		print("[SpaceBunny]   " .. i .. ". " .. p.name .. " [" .. role
			.. ", " .. tostring(p.timeout) .. "s] " .. tostring(p.url)
			.. (p.proxy_url ~= nil and (" + " .. p.proxy_url) or ""))
	end
end

local function mrcConfigPath()
	local home = os.getenv("HOME") or ""
	local configHome = os.getenv("XDG_CONFIG_HOME")

	if configHome == nil or configHome == "" then
		configHome = home .. "/.config"
	end

	return configHome .. "/SLSsteam/mrc_config.lua"
end

local function migrateOldConfig(mrc_cfg)
	if type(mrc_cfg.authority) ~= "table" or not mrc_cfg.authority.name then
		return mrc_cfg
	end

	if mrc_cfg.providers then
		return mrc_cfg
	end

	local providers = {}

	if mrc_cfg.authority.name ~= "none" then
		local auth = SpaceBunny.resolveProvider(mrc_cfg.authority)

		if auth then
			table.insert(providers, auth)
		end
	end

	if mrc_cfg.rescue and mrc_cfg.rescue.name ~= "none" then
		local rescue = SpaceBunny.resolveProvider(mrc_cfg.rescue)

		if rescue then
			table.insert(providers, rescue)
		end
	end

	mrc_cfg.providers = providers
	print("[SpaceBunny] Migrated legacy MRC config to multi-provider format")

	return mrc_cfg
end

local function resetMRCState()
	SpaceBunny.MRCCache = {}
	SpaceBunny.MRCFailCache = {}
	SpaceBunny.MRCJunkCache = {}
	SpaceBunny.MRCAuthorityHealth = {
		provider = nil,
		ewma = nil,
		consecutiveFailures = 0,
		consecutiveProbeTimeouts = 0,
		consecutiveHealthy = 0,
		markedDownUntil = 0,
	}
end

-- Applies an already-parsed config table. Returns true when the provider
-- list was replaced. Invalid entries are reported instead of silently
-- disappearing, because a typo in mrc_config.lua otherwise looks exactly
-- like "the provider is down".
SpaceBunny.applyMRCConfig = function(mrc_cfg)
	if type(mrc_cfg) ~= "table" then
		print("[SpaceBunny] mrc_config.lua must return a table, using defaults")
		return false
	end

	mrc_cfg = migrateOldConfig(mrc_cfg)

	if type(mrc_cfg.providers) ~= "table" or #mrc_cfg.providers == 0 then
		print("[SpaceBunny] mrc_config.lua has no providers list, using defaults")
		return false
	end

	local providers = {}

	for i, cfg in ipairs(mrc_cfg.providers) do
		local merged = SpaceBunny.resolveProvider(cfg)

		if merged ~= nil then
			providers[#providers + 1] = merged
		else
			local name = (type(cfg) == "table" and tostring(cfg.name)) or "?"

			print("[SpaceBunny] Ignoring MRC provider #" .. i .. " ('" .. name
				.. "'): needs a name and a http(s) url")
		end
	end

	if #providers == 0 then
		print("[SpaceBunny] No valid MRC providers configured, using defaults")
		return false
	end

	-- Tuning is only applied once the provider list is known to be usable,
	-- so a broken config cannot leave half of its settings behind.
	SpaceBunny.resetMrcTuning()
	SpaceBunny.applyMrcTuning(mrc_cfg.tuning)

	SpaceBunny.MRC_PROVIDERS = providers
	SpaceBunny.MRC_AUTHORITY = providers[1]
	SpaceBunny.MRC_RESCUE = providers[2]

	return true
end

-- Reads mrc_config.lua. `initial` is true for the first load, where the
-- built-in defaults are already in place and a missing file is normal;
-- a reload keeps the previous providers if the file is broken.
SpaceBunny.loadMRCConfig = function(initial)
	local path = mrcConfigPath()
	local ok, cfg = pcall(dofile, path)
	local loaded = false
	local missing = (not ok) and tostring(cfg):find("No such file") ~= nil

	-- If there is no mrc_config.lua, use the mother config's spacebunny.mrc.
	-- A present mrc_config.lua always wins, so per-machine provider tests do
	-- not require editing the big family file.
	if missing then
		local home = os.getenv("HOME") or ""
		local configHome = os.getenv("XDG_CONFIG_HOME")
		if configHome == nil or configHome == "" then
			configHome = home .. "/.config"
		end

		local mother_ok, mother = pcall(dofile,
			configHome .. "/SLSsteam/katamari.conf.lua")
		if mother_ok and type(mother) == "table"
				and type(mother.spacebunny) == "table"
				and type(mother.spacebunny.mrc) == "table" then
			ok = true
			cfg = mother.spacebunny.mrc
			missing = false
		end
	end

	if ok then
		loaded = SpaceBunny.applyMRCConfig(cfg)
	elseif not missing then
		if initial then
			print("[SpaceBunny] mrc_config.lua failed to parse: " .. tostring(cfg))
			print("[SpaceBunny] ... using the built-in MRC defaults instead")
		else
			print("[SpaceBunny] mrc_config.lua could not be reloaded ("
				.. tostring(cfg) .. "), keeping the previous providers")
		end
	end

	if not loaded and missing then
		SpaceBunny.MRC_PROVIDERS = DEFAULT_PROVIDERS
		SpaceBunny.MRC_AUTHORITY = DEFAULT_PROVIDERS[1]
		SpaceBunny.MRC_RESCUE = DEFAULT_PROVIDERS[2]
		SpaceBunny.resetMrcTuning()
		print("[SpaceBunny] No MRC config found, using defaults")
	elseif not loaded and initial then
		SpaceBunny.MRC_PROVIDERS = DEFAULT_PROVIDERS
		SpaceBunny.MRC_AUTHORITY = DEFAULT_PROVIDERS[1]
		SpaceBunny.MRC_RESCUE = DEFAULT_PROVIDERS[2]
		SpaceBunny.resetMrcTuning()
	end

	SpaceBunny.logProviderSummary()

	return loaded
end

SpaceBunny.reloadMRCConfig = function()
	resetMRCState()

	local loaded = SpaceBunny.loadMRCConfig(false)

	if loaded then
		print("[SpaceBunny] Reloaded MRC configuration ("
			.. #SpaceBunny.MRC_PROVIDERS .. " providers)")
	end

	return loaded
end

-- A broken config must never stop the plugin from loading; the loader
-- already reports what it could not use.
pcall(SpaceBunny.loadMRCConfig, true)

-- =========================================================================
-- Clock and formatting
-- =========================================================================

local nowMs = nil
local function nowSec()
	if nowMs ~= nil then
		local ms = nowMs()
		if ms ~= nil then
			return ms / 1000
		end
	end
	return os.time()
end

local function monoMs()
	if nowMs ~= nil then
		local ms = nowMs()
		if ms ~= nil then
			return ms
		end
	end
	return os.time() * 1000
end

SpaceBunny.setClock = function(fn)
	nowMs = fn
end

local function providerKey(provider)
	return tostring(provider.name) .. "|" .. tostring(provider.url or "")
end

-- =========================================================================
-- Logging and junk circuit breaker
-- =========================================================================

SpaceBunny.safeLog = function(level, msg)
	pcall(function()
		if type(msg) == "string" then
			log[level]("[SpaceBunny] " .. msg)
		else
			log[level]("[SpaceBunny] " .. tostring(msg))
		end
	end)
end

SpaceBunny.MRCJunkCache = SpaceBunny.MRCJunkCache or {}

local function failurePolicy(provider, kind)
	if kind == "junk" then
		return provider.max_junk_skips or 2,
			provider.junk_skip_duration or 300
	end

	return provider.max_transient_skips or 3,
		provider.transient_skip_duration or 30
end

SpaceBunny.providerSkipRemaining = function(provider)
	local skip = SpaceBunny.MRCJunkCache[providerKey(provider)]

	if skip == nil then
		return 0
	end

	local now = nowSec() or 0

	if skip.skip_until > now then
		return math.ceil(skip.skip_until - now)
	end

	if skip.skip_until > 0 then
		SpaceBunny.MRCJunkCache[providerKey(provider)] = nil
	end

	return 0
end

SpaceBunny.isProviderSkipped = function(provider)
	return SpaceBunny.providerSkipRemaining(provider) > 0
end

SpaceBunny.recordProviderFailure = function(provider, kind)
	local key = providerKey(provider)
	local entry = SpaceBunny.MRCJunkCache[key]
	local now = nowSec() or 0

	if entry == nil then
		entry = {
			count = 0,
			skip_until = 0,
		}
		SpaceBunny.MRCJunkCache[key] = entry
	end

	entry.count = entry.count + 1

	local limit, duration = failurePolicy(provider, kind)

	if entry.count >= limit then
		entry.skip_until = now + duration
		entry.count = 0

		SpaceBunny.safeLog("warn", "MRC provider " .. provider.name
			.. " circuit broken after " .. limit .. " " .. kind
			.. " failures, skipping for " .. duration .. "s")
	end
end

SpaceBunny.recordJunkResponse = function(provider)
	SpaceBunny.recordProviderFailure(provider, "junk")
end

SpaceBunny.recordProviderSuccess = function(provider)
	SpaceBunny.MRCJunkCache[providerKey(provider)] = nil
end

-- =========================================================================
-- Helpers
-- =========================================================================

SpaceBunny.sameIntList = function(a, b)
	if type(a) ~= "table" or type(b) ~= "table" then
		return false
	end

	if #a ~= #b then
		return false
	end

	for i = 1, #a do
		if a[i] ~= b[i] then
			return false
		end
	end

	return true
end


-- =========================================================================
-- MRC Authority Health
-- =========================================================================

SpaceBunny.MRCAuthorityHealth = SpaceBunny.MRCAuthorityHealth or {
	provider = nil,
	ewma = nil,
	consecutiveFailures = 0,
	consecutiveProbeTimeouts = 0,
	consecutiveHealthy = 0,
	markedDownUntil = 0,
}

SpaceBunny.syncAuthorityHealth = function(authority)
	local h = SpaceBunny.MRCAuthorityHealth
	local key = providerKey(authority)

	if h.provider == key then
		return
	end

	if h.provider ~= nil then
		print("[SpaceBunny] MRC authority changed (" .. tostring(h.provider)
			.. " -> " .. key .. "), resetting health counters")
	end

	h.provider = key
	h.ewma = nil
	h.consecutiveFailures = 0
	h.consecutiveProbeTimeouts = 0
	h.consecutiveHealthy = 0
	h.markedDownUntil = 0
end

local function authorityBudget(provider)
	local t = SpaceBunny.MRC_TUNING
	local h = SpaceBunny.MRCAuthorityHealth

	if (h.consecutiveFailures or 0) > 0 then
		return t.PROBE_SEC or 2
	end

	return provider.timeout or t.FULL_SEC or 5
end

-- =========================================================================
-- MRC Cache and Resolution
-- =========================================================================

SpaceBunny.MRCCache = SpaceBunny.MRCCache or {}
SpaceBunny.MRCFailCache = SpaceBunny.MRCFailCache or {}

local function tableSize(t)
	local n = 0
	for _ in pairs(t) do
		n = n + 1
	end
	return n
end

local function maybePruneCaches()
	local t = SpaceBunny.MRC_TUNING
	local now = nowSec()

	if tableSize(SpaceBunny.MRCFailCache) > (t.MRCFAILCACHE_MAX or 2000) then
		for k, until_ in pairs(SpaceBunny.MRCFailCache) do
			if until_ <= now then
				SpaceBunny.MRCFailCache[k] = nil
			end
		end
		if tableSize(SpaceBunny.MRCFailCache) > (t.MRCFAILCACHE_MAX or 2000) then
			local n = 0
			for k in pairs(SpaceBunny.MRCFailCache) do
				SpaceBunny.MRCFailCache[k] = nil
				n = n + 1
				if n > 100 then
					break
				end
			end
		end
	end

	if tableSize(SpaceBunny.MRCCache) > (t.MRCCACHE_MAX or 5000) then
		for k, v in pairs(SpaceBunny.MRCCache) do
			if type(v) == "table" and v.source == "rescue"
				and (now - (v.at or 0)) >= (v.ttl or t.PROVISIONAL_TTL or 300) then
				SpaceBunny.MRCCache[k] = nil
			end
		end
		if tableSize(SpaceBunny.MRCCache) > (t.MRCCACHE_MAX or 5000) then
			local n = 0
			for k in pairs(SpaceBunny.MRCCache) do
				SpaceBunny.MRCCache[k] = nil
				n = n + 1
				if n > 100 then
					break
				end
			end
		end
	end
end

local function authorityIsMarkedDown()
	local h = SpaceBunny.MRCAuthorityHealth

	return h.markedDownUntil ~= nil and h.markedDownUntil > monoMs()
end

SpaceBunny.recordAuthorityResult = function(latencyMs, ok)
	local t = SpaceBunny.MRC_TUNING
	local h = SpaceBunny.MRCAuthorityHealth

	if not ok then
		h.consecutiveFailures = h.consecutiveFailures + 1
		h.consecutiveHealthy = 0

		if latencyMs ~= nil and h.ewma == nil then
			h.ewma = latencyMs
		end
	else
		if latencyMs ~= nil and latencyMs <= t.HEALTHY_MS then
			h.consecutiveFailures = 0
			h.consecutiveHealthy = h.consecutiveHealthy + 1
			h.consecutiveProbeTimeouts = 0

			if h.consecutiveHealthy >= (t.RECOVER_AFTER or 2) then
				local recovered = h.consecutiveHealthy
				h.markedDownUntil = 0
				h.consecutiveHealthy = 0
				SpaceBunny.safeLog("debug", "MRC authority recovered ("
					.. recovered .. " fast successes)")
			end
		else
			h.consecutiveHealthy = 0
			h.consecutiveFailures = 0
			h.consecutiveProbeTimeouts = 0
		end

		if latencyMs ~= nil then
			if h.ewma == nil then
				h.ewma = latencyMs
			else
				h.ewma = t.ALPHA * latencyMs + (1 - t.ALPHA) * h.ewma
			end
		end
	end
end

SpaceBunny.recordAuthorityProbeTimeout = function()
	local t = SpaceBunny.MRC_TUNING
	local h = SpaceBunny.MRCAuthorityHealth

	h.consecutiveProbeTimeouts = h.consecutiveProbeTimeouts + 1

	if h.consecutiveProbeTimeouts >= (t.PROBE_TIMEOUTS_TO_MARK_DOWN or 2) then
		h.markedDownUntil = monoMs() + (t.DOWN_TTL_MS or 60000)
		SpaceBunny.safeLog("warn", "MRC authority " .. SpaceBunny.MRC_AUTHORITY.name
			.. " marked down for " .. (t.DOWN_TTL_MS or 60000) .. "ms after "
			.. h.consecutiveProbeTimeouts .. " consecutive probe timeouts")
	end
end

local function sbLooksLikeHtml(body)
	if type(body) ~= "string" then
		return true
	end
	local head = body:sub(1, 256):lower()
	return head:find("<!doctype") ~= nil
		or head:find("<html") ~= nil
		or head:find("<head") ~= nil
end

-- =========================================================================
-- MRC prerequisites (libc)
--
-- The MRC helpers need uint64 formatting and a monotonic clock before the
-- Steam-specific FFI setup below. Keeping these above validateMRC also keeps
-- the lexical `ffi` local visible to it; a later `local ffi` would make the
-- earlier functions read a nil global.
-- =========================================================================

local ffi = require("ffi")

pcall(ffi.cdef, [[
	typedef long time_t;
	typedef struct { time_t tv_sec; long tv_nsec; } timespec_t;
	int clock_gettime(int clk_id, timespec_t* tp);
	int snprintf(char*, size_t size, const char*, ...);
	unsigned long long strtoull(const char*, char**, int);
]])

do
	local ok = pcall(function()
		local ts = ffi.new("timespec_t")
		local clock_gettime = ffi.C.clock_gettime
		local CLOCK_MONOTONIC = 1

		nowMs = function()
			if clock_gettime(CLOCK_MONOTONIC, ts) ~= 0 then
				return nil
			end

			return tonumber(ts.tv_sec) * 1000 + tonumber(ts.tv_nsec) / 1e6
		end
	end)

	if not ok then
		print("[SpaceBunny] Monotonic clock unavailable, using wall-clock windows")
	end
end

SpaceBunny.u64str = function(value)
	local buf = ffi.new("char[24]")
	ffi.C.snprintf(buf, 24, "%llu", ffi.cast("uint64_t", value))
	return ffi.string(buf)
end

SpaceBunny.u32str = function(value)
	local buf = ffi.new("char[16]")
	ffi.C.snprintf(buf, 16, "%u", ffi.cast("uint32_t", value))
	return ffi.string(buf)
end

SpaceBunny.validateMRC = function(raw)
	if raw == nil then
		return nil
	end

	local s = tostring(raw):gsub("^%s+", ""):gsub("%s+$", "")

	if #s >= 2 and s:sub(1, 1) == '"' and s:sub(-1) == '"' then
		s = s:sub(2, -2)
	end

	if not s:match("^%d+$") then
		return nil
	end

	if s:match("^0+$") then
		return nil
	end

	if #s > 20 then
		return nil
	end

	local num = ffi.C.strtoull(s, nil, 10)
	local back = SpaceBunny.u64str(num)
	local norm = s:gsub("^0+", "")
	if norm == "" then
		norm = "0"
	end
	if back ~= norm then
		return nil
	end

	return s
end

SpaceBunny.classifyMRCFailure = function(raw, elapsedMs, timeoutSec)
	if raw == nil or raw == "" then
		local budgetMs = (tonumber(timeoutSec) or 0) * 1000

		if elapsedMs ~= nil and budgetMs > 0 and elapsedMs >= budgetMs * 0.9 then
			return "transient", "stall"
		end

		return "transient", "empty"
	end

	if sbLooksLikeHtml(raw) then
		return "junk", "html-challenge"
	end

	local s = tostring(raw):gsub("^%s+", ""):gsub("%s+$", "")

	local status = s:match("^error code:%s*(%d%d%d)$") or s:match("^(%d%d%d)$")

	if status ~= nil then
		local n = tonumber(status)

		if n >= 500 then
			return "transient", "http-5xx"
		end

		if n >= 400 then
			return "junk", "http-4xx"
		end
	end

	if s:match("^%d+$") then
		return "junk", "bad-code"
	end

	return "junk", "bad-body"
end

SpaceBunny.fetchMRCFrom = function(provider, manifestIdStr, timeoutSec, urlOverride)
	local base = urlOverride or provider.url
	local url = base:gsub("/$", "") .. "/" .. manifestIdStr
	local started = monoMs()

	local skipRemaining = SpaceBunny.providerSkipRemaining(provider)

	if skipRemaining > 0 then
		SpaceBunny.safeLog("debug", "MRC provider " .. provider.name
			.. " is circuit-broken, skipping (" .. skipRemaining .. "s left)")
		return nil, "skipped", nil
	end

	local ok, raw

	if provider.headers ~= nil and #provider.headers > 0 then
		ok, raw = pcall(curl.downloadString, url, provider.headers, timeoutSec)
	else
		ok, raw = pcall(curl.downloadString, url, timeoutSec)
	end

	local elapsed = nil

	if started ~= nil then
		elapsed = monoMs() - started
	end

	if not ok then
		SpaceBunny.safeLog("debug", "MRC provider " .. provider.name
			.. " raised an error: " .. tostring(raw))
		SpaceBunny.recordProviderFailure(provider, "transient")

		return nil, "error", elapsed
	end

	local codeStr = SpaceBunny.validateMRC(raw)

	if codeStr == nil then
		local kind, reason = SpaceBunny.classifyMRCFailure(raw, elapsed, timeoutSec)

		SpaceBunny.safeLog("debug", "MRC provider " .. provider.name
			.. " returned " .. reason .. " for " .. manifestIdStr)
		SpaceBunny.recordProviderFailure(provider, kind)

		return nil, reason, elapsed
	end

	SpaceBunny.recordProviderSuccess(provider)

	SpaceBunny.safeLog("debug", "MRC " .. provider.name .. " answered " .. manifestIdStr
		.. " in " .. tostring(math.floor(elapsed or 0)) .. "ms")

	if elapsed ~= nil and timeoutSec ~= nil
			and elapsed > timeoutSec * 1000 + 1000 then
		SpaceBunny.safeLog("debug", "MRC provider " .. provider.name
			.. " exceeded its connect budget (" .. math.floor(elapsed)
			.. "ms > " .. math.floor(timeoutSec * 1000) .. "ms)")
	end

	return codeStr, "ok", elapsed
end

-- `reserveSec` is the time the caller has already set aside for providers
-- further down the list. The proxy variant must respect it too, otherwise a
-- stalled direct attempt plus its proxy still starves the tail.
SpaceBunny.fetchMRCWithProxy = function(provider, manifestIdStr, timeoutSec, deadlineMs,
		reserveSec)
	local codeStr, reason, elapsed =
		SpaceBunny.fetchMRCFrom(provider, manifestIdStr, timeoutSec)

	if codeStr ~= nil then
		return codeStr, reason, elapsed, false
	end

	if provider.is_proxy then
		return nil, reason, elapsed, false
	end

	if provider.proxy_url == nil or provider.proxy_url == "" then
		return nil, reason, elapsed, false
	end

	local remainingMs = nil

	if deadlineMs ~= nil then
		remainingMs = deadlineMs - monoMs()

		if reserveSec ~= nil and reserveSec > 0 then
			remainingMs = remainingMs - reserveSec * 1000
		end
	end

	if remainingMs ~= nil and remainingMs < 1000 then
		SpaceBunny.safeLog("debug", "MRC provider " .. provider.name
			.. " direct failed (" .. tostring(reason)
			.. "), no budget left for proxy")
		return nil, reason, elapsed, false
	end

	SpaceBunny.safeLog("debug", "MRC provider " .. provider.name
		.. " direct failed (" .. tostring(reason)
		.. "), trying proxy")

	local proxyProvider = {
		name = provider.name .. "-proxy",
		url = provider.proxy_url,
		headers = provider.headers,
		is_proxy = true,
		provisional = true,
		timeout = math.min(provider.timeout or 5, 3),
		max_junk_skips = provider.max_junk_skips or 2,
		junk_skip_duration = provider.junk_skip_duration or 300,
		max_transient_skips = provider.max_transient_skips or 3,
		transient_skip_duration = provider.transient_skip_duration or 30,
	}

	if remainingMs ~= nil then
		proxyProvider.timeout = math.max(1,
			math.min(proxyProvider.timeout, remainingMs / 1000))
	end

	local proxyCode, proxyReason = SpaceBunny.fetchMRCFrom(
		proxyProvider, manifestIdStr, proxyProvider.timeout)

	return proxyCode, proxyReason, elapsed, proxyCode ~= nil
end

SpaceBunny.getManifestRequestCode = function(manifestId)
	if manifestId == nil or manifestId == 0 then
		return nil
	end

	local t = SpaceBunny.MRC_TUNING
	local manifestIdStr = SpaceBunny.u64str(manifestId)
	local now = nowSec()
	local deadline = monoMs() + (t.TOTAL_BUDGET_SEC or 8) * 1000

	SpaceBunny.syncAuthorityHealth(SpaceBunny.MRC_AUTHORITY)

	maybePruneCaches()

	local cached = SpaceBunny.MRCCache[manifestIdStr]

	if cached ~= nil and type(cached) == "table" and cached.value ~= nil then
		local age = now - (cached.at or 0)
		local ttl = cached.ttl

		if ttl == nil then
			ttl = (cached.source == "authority")
				and (t.AUTHORITY_TTL or 86400)
				or (t.PROVISIONAL_TTL or 300)
		end

		if age < ttl then
			local label = (cached.source == "authority") and "AUTHORITATIVE" or "PROVISIONAL"

			SpaceBunny.safeLog("debug", "Using " .. label .. " MRC for " .. manifestIdStr
				.. " from " .. tostring(cached.provider or "?")
				.. " (age " .. math.floor(age) .. "s of " .. ttl .. "s)")

			return ffi.C.strtoull(cached.value, nil, 10)
		end

		SpaceBunny.safeLog("debug", "MRC for " .. manifestIdStr
			.. " expired after " .. math.floor(age) .. "s, re-asking")
	end

	local failUntil = SpaceBunny.MRCFailCache[manifestIdStr]

	if failUntil ~= nil and failUntil > now then
		SpaceBunny.safeLog("debug",
			"MRC for " .. manifestIdStr .. " is in failure backoff, not fetching")
		return nil
	elseif failUntil ~= nil then
		SpaceBunny.MRCFailCache[manifestIdStr] = nil
	end

	local directProviders = {}
	local proxyProvider = nil

	for _, provider in ipairs(SpaceBunny.MRC_PROVIDERS) do
		if provider.is_proxy then
			proxyProvider = provider
		else
			table.insert(directProviders, provider)
		end
	end

	local attempted = false
	local report = {}

	local function note(provider, reason, elapsed)
		if reason == "skipped" then
			report[#report + 1] = provider.name .. "=skipped("
				.. SpaceBunny.providerSkipRemaining(provider) .. "s)"
		else
			attempted = true
			report[#report + 1] = provider.name .. "=" .. tostring(reason)
				.. "(" .. math.floor(elapsed or 0) .. "ms)"
		end
	end

	local function remainingSec()
		return (deadline - monoMs()) / 1000
	end

	local function cacheAnswer(provider, codeStr, usedProxy)
		local authoritative = not usedProxy and not provider.provisional
		local ttl = authoritative
			and (t.AUTHORITY_TTL or 86400)
			or (t.PROVISIONAL_TTL or 300)

		SpaceBunny.MRCCache[manifestIdStr] = {
			value = codeStr,
			source = authoritative and "authority" or "rescue",
			provider = providerKey(provider),
			at = nowSec(),
			ttl = ttl,
		}
		SpaceBunny.MRCFailCache[manifestIdStr] = nil

		if authoritative then
			SpaceBunny.safeLog("debug", "MRC for " .. manifestIdStr
				.. " resolved by " .. provider.name .. " (authoritative for "
				.. ttl .. "s)")
		else
			SpaceBunny.safeLog("warn", "MRC for " .. manifestIdStr
				.. " came from " .. provider.name .. " - provisional for "
				.. ttl .. "s")
		end
	end

	local minAttempt = t.MIN_ATTEMPT_SEC or 1

	for i, provider in ipairs(directProviders) do
		local isAuthority = providerKey(provider) == providerKey(SpaceBunny.MRC_AUTHORITY)
		local remaining = remainingSec()
		local floor = math.min(minAttempt, math.max(remaining, 0))

		if remaining < minAttempt then
			report[#report + 1] = provider.name .. "=no-budget"
		else
			local budget = provider.timeout or t.FULL_SEC or 5

			if isAuthority then
				budget = math.min(budget, authorityBudget(provider) or budget)

				if authorityIsMarkedDown() then
					SpaceBunny.safeLog("debug", "MRC authority is marked down; trying it anyway with the probe budget")
				end
			end

			-- Every provider still ahead of this one keeps its own floor, so a
			-- slow primary can spend the rest of the budget but never the
			-- whole list's. The last direct provider also leaves room for a
			-- trailing proxy-only entry.
			local stillAhead = (#directProviders - i)
				+ (proxyProvider ~= nil and 1 or 0)
			local reserveAfter = stillAhead * floor
			local fairShare = remaining - reserveAfter

			budget = math.max(floor, math.min(budget, fairShare))
			budget = math.min(budget, remaining)

			local codeStr, reason, elapsed, usedProxy =
				SpaceBunny.fetchMRCWithProxy(provider, manifestIdStr, budget,
					deadline, reserveAfter)

			note(provider, reason, elapsed)

			if isAuthority and reason ~= "skipped" then
				local timedOut = codeStr == nil and elapsed ~= nil
					and elapsed >= budget * 1000 * 0.9

				if timedOut then
					SpaceBunny.recordAuthorityProbeTimeout()
				end

				SpaceBunny.recordAuthorityResult(elapsed, codeStr ~= nil and not usedProxy)
			end

			if codeStr ~= nil then
				cacheAnswer(provider, codeStr, usedProxy)

				return ffi.C.strtoull(codeStr, nil, 10)
			end
		end
	end

	if proxyProvider ~= nil then
		local remaining = remainingSec()

		if remaining < minAttempt then
			report[#report + 1] = proxyProvider.name .. "=no-budget"
		else
			local budget = math.max(math.min(minAttempt, remaining),
				math.min(proxyProvider.timeout or 3, remaining))
			local proxyCode, proxyReason, proxyElapsed =
				SpaceBunny.fetchMRCWithProxy(proxyProvider, manifestIdStr, budget, deadline, 0)

			note(proxyProvider, proxyReason, proxyElapsed)

			if proxyCode ~= nil then
				cacheAnswer(proxyProvider, proxyCode, true)

				return ffi.C.strtoull(proxyCode, nil, 10)
			end
		end
	end

	local summary = table.concat(report, ", ")

	if attempted then
		SpaceBunny.MRCFailCache[manifestIdStr] = nowSec() + (t.FAIL_TTL or 120)
		SpaceBunny.safeLog("error", "All MRC providers failed for manifest "
			.. manifestIdStr .. ": " .. summary
			.. "; suppressing for " .. (t.FAIL_TTL or 120) .. "s")
	else
		SpaceBunny.safeLog("warn", "No MRC provider was attempted for manifest "
			.. manifestIdStr .. ": " .. summary)
	end

	return nil
end

-- =========================================================================
-- Steam FFI setup
-- =========================================================================


local tier0 = ffi.load("tier0_s")

pcall(ffi.cdef, [[ void* place_lua_hook(const int, const void*); ]])

ffi.cdef[[
	void* Plat_Alloc(size_t);
	void Plat_Free(void*);

	typedef struct
	{
		uint32_t* mem;
		uint32_t alloc;
		uint32_t grow;
		uint32_t size;
	} SB_CUtlVector;

	typedef struct
	{
		uint32_t packageId;
		uint8_t __pad0x0[0x44];
		SB_CUtlVector depots;
	} SB_PackageInfo_t;

	typedef SB_PackageInfo_t*(*SB_GetPackage_t)(void*, uint32_t, uint32_t, uint32_t);
	typedef int(*SB_GetBinary_t)(void*, uint32_t, const char*, int8_t*, uint32_t);
	typedef bool(*SB_GetMRC_t)(void*, uint32_t, uint32_t, uint64_t, const char*, uint64_t*);
]]

-- =========================================================================
-- Hook safety
-- =========================================================================

SpaceBunny.runInHook = function(label, fallback, fn)
	local mutex = nil
	local locked = false
	local result

	local ok, err = pcall(function()
		mutex = LuaMutex()
		locked = true
		result = fn(mutex)
	end)

	if locked then
		local unlocked, uerr = pcall(function()
			mutex:unlock()
		end)

		if not unlocked then
			SpaceBunny.safeLog("error",
				"[" .. label .. "] mutex unlock failed: " .. tostring(uerr))
		end
	end

	if not ok then
		SpaceBunny.safeLog("error",
			"[" .. label .. "] unhandled error, returning fallback: " .. tostring(err))

		if type(fallback) == "function" then
			local got, value = pcall(fallback)

			if got then
				return value
			end

			return nil
		end

		return fallback
	end

	return result
end

-- =========================================================================
-- Pattern scanning
-- =========================================================================

local function resolvePattern(label, pattern, module)
	local ok, scan = pcall(memhlp.patternScan, pattern, module)

	if not ok or scan == nil then
		log.notifyError("Pattern scan failed for " .. label .. " - SLSsteam update?")
		return nil
	end

	local resolved, target = pcall(memhlp.getJmpTarget, scan)

	if not resolved or target == nil then
		log.notifyError("getJmpTarget failed for " .. label .. " - SLSsteam update?")
		return nil
	end

	return target
end

local steamClientMod = memhlp.getModule("steamclient.so")

if steamClientMod == nil then
	log.notifyError("Failed to find steamclient.so")
	return
end

local getPackageInfoPtr = resolvePattern("GetPackage", "E8 ? ? ? ? 83 C4 ? 83 78 ? ? 0F 84", steamClientMod)
if getPackageInfoPtr == nil then
	return
end

local getMRCPtr = resolvePattern("GetMRC", "E8 ? ? ? ? 83 C4 ? 83 F8 ? 0F 85 ? ? ? ? 83 EC ? 6A ? FF 75 ? E8 ? ? ? ? 83 C4 ? 88 45", steamClientMod)
if getMRCPtr == nil then
	return
end

local configStoreMapGetBinary = VFTableInfo_t("21IClientConfigStoreMap", "GetBinary")
if not configStoreMapGetBinary:init() then
	log.notifyError("Failed to find GetBinary!")
	return
end

local configStoreGetBinary = VFTableInfo_t("12CConfigStore", "GetBinary", configStoreMapGetBinary.index)
if not configStoreGetBinary:init() then
	log.notifyError("Failed to find GetBinary on CConfigStore!")
	return
end

-- =========================================================================
-- GetPackage
-- =========================================================================

SpaceBunny.isUsablePackage = function(pkg)
	if pkg == nil then
		return false
	end

	if pkg.packageId ~= 0 then
		SpaceBunny.safeLog("warn",
			"Cached package id is " .. SpaceBunny.u32str(pkg.packageId) .. ", expected 0")
		return false
	end

	local depots = pkg.depots

	if depots == nil or depots.mem == nil then
		SpaceBunny.safeLog("warn", "Cached package has a null depot vector, skipping injection")
		return false
	end

	if depots.size > 0x10000 then
		SpaceBunny.safeLog("warn", "Cached package reports an implausible depot count, skipping injection")
		return false
	end

	if depots.alloc < depots.size then
		SpaceBunny.safeLog("warn", "Cached package depot alloc < size, skipping injection")
		return false
	end

	return true
end

SpaceBunny.getAllDepots = function()
	local merged = {}
	local seen = {}

	local function append(list)
		for _, id in ipairs(list) do
			if type(id) == "number" and id > 0 and id == math.floor(id) and not seen[id] then
				seen[id] = true
				merged[#merged + 1] = id
			end
		end
	end

	append(SpaceBunny.PackageDepotIds)
	append(SpaceBunny.AdditionalDepots)

	return merged
end

SpaceBunny.writeDepotIds = function()
	local pkg = SpaceBunny.Package

	if not SpaceBunny.isUsablePackage(pkg) then
		return
	end

	local depots = pkg.depots
	local mem = depots.mem
	local size = depots.size
	local grow = depots.grow

	SpaceBunny.safeLog("debug", "writeDepotIds into Package "
		.. SpaceBunny.u32str(pkg.packageId) .. " at " .. tostring(pkg))

	if not SpaceBunny.OriginalDepotCaptured then
		for i = 0, size - 1 do
			SpaceBunny.PackageDepotIds[#SpaceBunny.PackageDepotIds + 1] = mem[i]
		end
		SpaceBunny.OriginalDepotCaptured = true

		SpaceBunny.safeLog("debug",
			"Backed up " .. #SpaceBunny.PackageDepotIds .. " original depot ids")
	end

	local merged = SpaceBunny.getAllDepots()

	if #merged < 1 then
		SpaceBunny.safeLog("warn", "Refusing to write an empty depot vector, keeping original")
		return
	end

	if size == #merged then
		local same = true

		for i = 0, size - 1 do
			if mem[i] ~= merged[i + 1] then
				same = false
				break
			end
		end

		if same then
			SpaceBunny.safeLog("debug", "The depot vector is already the merged list, leaving it alone")
			return
		end
	end

	local new = ffi.cast("uint32_t*", tier0.Plat_Alloc(4 * #merged))

	if new == nil then
		SpaceBunny.safeLog("error",
			"Plat_Alloc failed for " .. #merged .. " depots, keeping original")
		return
	end

	for i = 0, #merged - 1 do
		new[i] = merged[i + 1]
	end

	depots.mem = new
	depots.size = #merged
	depots.alloc = #merged
	depots.grow = grow
end

SpaceBunny.restoreDepotIds = function()
	local pkg = SpaceBunny.Package

	if not SpaceBunny.isUsablePackage(pkg) then
		return false
	end

	local originals = SpaceBunny.PackageDepotIds

	if not SpaceBunny.OriginalDepotCaptured then
		return false
	end

	local depots = pkg.depots
	local mem = depots.mem
	local size = depots.size
	local grow = depots.grow

	if size < #originals then
		SpaceBunny.safeLog("debug", "The depot vector is smaller than the backup, leaving it alone")
		return false
	end

	for i = 0, #originals - 1 do
		if mem[i] ~= originals[i + 1] then
			SpaceBunny.safeLog("debug",
				"The depot vector is not the one we wrote, leaving it alone")
			return false
		end
	end

	if size == #originals then
		return true
	end

	if #originals == 0 then
		depots.mem = nil
		depots.size = 0
		depots.alloc = 0
		depots.grow = grow
		SpaceBunny.safeLog("debug", "Restored an originally empty depot list")
		return true
	end

	local new = ffi.cast("uint32_t*", tier0.Plat_Alloc(4 * #originals))

	if new == nil then
		SpaceBunny.safeLog("error", "Plat_Alloc failed while restoring the depot list")
		return false
	end

	for i = 0, #originals - 1 do
		new[i] = originals[i + 1]
	end

	depots.mem = new
	depots.size = #originals
	depots.alloc = #originals
	depots.grow = grow

	SpaceBunny.safeLog("debug", "Restored " .. #originals .. " original depot ids")
	return true
end

-- =========================================================================
-- Hooks
-- =========================================================================

SpaceBunny.hkGetPackage = ffi.cast("SB_GetPackage_t", function(pPkgInfoCache, pkgId, a2, a3)
	local trampResult = nil

	return SpaceBunny.runInHook("hkGetPackage", function() return trampResult end, function()
		local pPkg = SpaceBunny.getPackageTramp(pPkgInfoCache, pkgId, a2, a3)
		trampResult = pPkg

		if pkgId ~= 0 then
			return pPkg
		end

		if pPkg == nil then
			return pPkg
		end

		if not SpaceBunny.RewriteDepots then
			if SpaceBunny.Package ~= nil and SpaceBunny.Package ~= pPkg then
				SpaceBunny.safeLog("debug", "Package 0 pointer moved ("
					.. tostring(SpaceBunny.Package) .. " -> " .. tostring(pPkg)
					.. "), re-baselining")
				SpaceBunny.Package = nil
				SpaceBunny.PackageDepotIds = {}
				SpaceBunny.OriginalDepotCaptured = false
			end
			return pPkg
		end

		if SpaceBunny.Package == nil or SpaceBunny.Package ~= pPkg then
			if SpaceBunny.Package ~= nil then
				SpaceBunny.safeLog("debug", "Package 0 pointer changed ("
					.. tostring(SpaceBunny.Package) .. " -> " .. tostring(pPkg)
					.. "), discarding stale backup of "
					.. #SpaceBunny.PackageDepotIds .. " depots")
			end
			SpaceBunny.Package = pPkg
			SpaceBunny.PackageDepotIds = {}
			SpaceBunny.OriginalDepotCaptured = false
		end

		SpaceBunny.writeDepotIds()

		if SpaceBunny.isUsablePackage(SpaceBunny.Package) then
			local depots = SpaceBunny.Package.depots
			SpaceBunny.RewriteDepots = false
			SpaceBunny.safeLog("debug", "Injected " .. depots.size .. " depots into package 0")
		end

		return pPkg
	end)
end)

-- =========================================================================
-- GetBinary
-- =========================================================================

SpaceBunny.keyToBinary = function(depotId, key)
	if type(key) ~= "string" then
		SpaceBunny.safeLog("error", "Invalid key type for " .. tostring(depotId))
		return nil
	end

	local trimmed = key:gsub("^%s+", ""):gsub("%s+$", "")
	trimmed = trimmed:gsub('^"', ""):gsub('"$', ""):gsub("^'", ""):gsub("'$", "")

	if #trimmed ~= 64 then
		SpaceBunny.safeLog("error", "Invalid key length (" .. #trimmed
			.. ", expected 64) for " .. tostring(depotId))
		return nil
	end

	local bytes = {}

	for i = 1, 64, 2 do
		local sub = trimmed:sub(i, i + 1)

		if not sub:match("^%x%x$") then
			SpaceBunny.safeLog("error",
				"Invalid hex digit in " .. sub .. " in key for " .. tostring(depotId))
			return nil
		end

		bytes[#bytes + 1] = tonumber(sub, 16)
	end

	return bytes
end

SpaceBunny.hkGetBinary = ffi.cast("SB_GetBinary_t", function(pConfigStore, store, pChName, pOut, outSize)
	local trampSize = 0

	return SpaceBunny.runInHook("hkGetBinary", function() return trampSize end, function()
		local size = SpaceBunny.getBinaryTramp(pConfigStore, store, pChName, pOut, outSize)
		trampSize = size

		if pChName == nil or pChName == ffi.cast("void*", 0) then
			return size
		end

		if pOut == nil or pOut == ffi.cast("void*", 0) then
			SpaceBunny.safeLog("debug", "GetBinary received a null output buffer")
			return size
		end

		local chNameStr = ffi.string(pChName)
		local nameMatch = chNameStr:match("%d+\\DecryptionKey")

		if nameMatch == nil then
			return size
		end

		local depotId = tonumber(nameMatch:match("%d+"))

		if depotId == nil then
			return size
		end

		local key = SpaceBunny.DecryptionKeys[depotId]

		-- Delegate to Hubcap if the key is missing and hubcap.lua is loaded
		if key == nil and hubcapEnabled() then
			local fetched, source = hubcapResolveDepotKey(depotId, nil)

			if fetched ~= nil then
				key = fetched
				SpaceBunny.DecryptionKeys[depotId] = key
				SpaceBunny.safeLog("info", "Using the Hubcap key for depot " .. tostring(depotId))
			else
				SpaceBunny.safeLog("debug", "No key for depot " .. tostring(depotId)
					.. ": " .. tostring(source))
			end
		end

		if key == nil then
			if size < 1 then
				SpaceBunny.safeLog("warn", "Missing decryptionkey for " .. tostring(depotId))
			end

			return size
		end

		local bytes = SpaceBunny.keyToBinary(depotId, key)

		if bytes == nil then
			return size
		end

		if outSize < #bytes then
			SpaceBunny.safeLog("error", "Output buffer for depot " .. tostring(depotId)
				.. " is " .. tostring(outSize) .. " bytes, need " .. #bytes
				.. " - skipping key injection")
			return size
		end

		SpaceBunny.safeLog("debug", "Using config key for " .. tostring(depotId))

		for i = 0, #bytes - 1 do
			pOut[i] = bytes[i + 1]
		end

		return #bytes
	end)
end)

-- =========================================================================
-- GetMRC
-- =========================================================================

SpaceBunny.hkGetMRC = ffi.cast("SB_GetMRC_t", function(a1, appId, depotId, manifestId, pChBranch, pOutMRC)
	local trampResult = false

	return SpaceBunny.runInHook("hkGetMRC", function() return trampResult end, function()
		-- Katamari mode: Bakeneko-only deliberately disables MRC provider calls.
		if not SpaceBunny.MRC_ENABLED then
			SpaceBunny.safeLog("warn", "MRC providers disabled by spacebunny.conf (mrc_enabled=false)")
			return
		end

		if type(KATAMARI) == "table" and type(KATAMARI.manifestMode) == "function"
				and KATAMARI.manifestMode() == "bakeneko-only" then
			SpaceBunny.safeLog("warn", "Katamari is in bakeneko-only mode; skipping MRC providers")
			return
		end

		-- Teach Hubcap which game owns this depot
		if appId ~= nil and appId ~= 0 and depotId ~= nil and depotId ~= 0 then
			hubcapLearnDepotApp(depotId, appId)

			-- If Hubcap is active and we don't have the key yet, try resolving it
			if hubcapEnabled() and SpaceBunny.ConfigLoaded
					and SpaceBunny.DecryptionKeys[depotId] == nil then
				local fetched, source = hubcapResolveDepotKey(depotId, appId)

				if fetched ~= nil then
					SpaceBunny.DecryptionKeys[depotId] = fetched
				else
					SpaceBunny.safeLog("info", "No key for depot " .. SpaceBunny.u32str(depotId)
						.. " yet: " .. tostring(source))
				end
			end
		end

		local success = SpaceBunny.getMRCTramp(a1, appId, depotId, manifestId, pChBranch, pOutMRC)
		trampResult = success

		if pOutMRC == nil or pOutMRC[0] ~= 0 then
			return success
		end

		local code = SpaceBunny.getManifestRequestCode(manifestId)

		if code == nil then
			SpaceBunny.safeLog("error",
				"Failed to get MRC for " .. SpaceBunny.u32str(depotId))
			return success
		end

		SpaceBunny.safeLog("debug", "Using MRC " .. SpaceBunny.u64str(code)
			.. " for " .. SpaceBunny.u32str(depotId))

		pOutMRC[0] = code
		return true
	end)
end)

-- =========================================================================
-- Hook installation
-- =========================================================================

SpaceBunny.STANDALONE = false
SpaceBunny.MRC_ENABLED = true
SpaceBunny.PRIORITY = 20

pcall(function()
	local home = os.getenv("HOME") or ""
	local configHome = os.getenv("XDG_CONFIG_HOME")
	if configHome == nil or configHome == "" then
		configHome = home .. "/.config"
	end
	local mother_ok, mother = pcall(dofile,
		configHome .. "/SLSsteam/katamari.conf.lua")
	if mother_ok and type(mother) == "table"
			and type(mother.spacebunny) == "table" then
		local section = mother.spacebunny
		if type(section.standalone) == "boolean" then
			SpaceBunny.STANDALONE = section.standalone
		end
		if type(section.mrc_enabled) == "boolean" then
			SpaceBunny.MRC_ENABLED = section.mrc_enabled
		end
		if tonumber(section.priority) ~= nil then
			SpaceBunny.PRIORITY = tonumber(section.priority)
		end
	end

	local cfg_path = configHome .. "/SLSsteam/spacebunny.conf.lua"
	local ok, cfg = pcall(dofile, cfg_path)
	if ok and type(cfg) == "table" then
		if type(cfg.standalone) == "boolean" then
			SpaceBunny.STANDALONE = cfg.standalone
		end
		if type(cfg.mrc_enabled) == "boolean" then
			SpaceBunny.MRC_ENABLED = cfg.mrc_enabled
		end
		if tonumber(cfg.priority) ~= nil then
			SpaceBunny.PRIORITY = tonumber(cfg.priority)
		end
	end
end)

if type(Downloader) == "table" and Downloader.setup then
	if SpaceBunny.STANDALONE then
		print("[SpaceBunny] Another download interceptor is active, " ..
			"running anyway because standalone = true")
	else
		print("[SpaceBunny] Another download interceptor is active, standing down")
		print("[SpaceBunny] To test this plugin next to it, set " ..
			"standalone = true in ~/.config/SLSsteam/spacebunny.conf.lua")
		return
	end
end

local function installHook(label, hookName, target, callback, ctype)
	local ok, hookOrErr = pcall(LuaHook, hookName, target)
	if not ok or hookOrErr == nil then
		log.notifyError("Failed to create " .. label .. " hook: " .. tostring(hookOrErr))
		return nil, nil
	end

	local hook = hookOrErr
	local raw = ffi.C.place_lua_hook(hook.index, callback)
	if raw == nil then
		log.notifyError("Failed to install " .. label .. " hook: place_lua_hook returned NULL")
		return nil, nil
	end

	return hook, ffi.cast(ctype, raw)
end

SpaceBunny.getPackageHook, SpaceBunny.getPackageTramp = installHook(
	"GetPackage", "GetPackage", getPackageInfoPtr,
	SpaceBunny.hkGetPackage, "SB_GetPackage_t")

SpaceBunny.getBinaryHook, SpaceBunny.getBinaryTramp = installHook(
	"GetBinary", "GetBinary", configStoreGetBinary.ptr,
	SpaceBunny.hkGetBinary, "SB_GetBinary_t")

SpaceBunny.getMRCHook, SpaceBunny.getMRCTramp = installHook(
	"GetManifestRequestCode", "GetManifestRequestCode", getMRCPtr,
	SpaceBunny.hkGetMRC, "SB_GetMRC_t")

if SpaceBunny.getPackageTramp == nil or SpaceBunny.getBinaryTramp == nil
		or SpaceBunny.getMRCTramp == nil then
	SpaceBunny.setup = false
	log.notifyError("SpaceBunny hooks were not fully installed; plugin disabled")
	return
end

SpaceBunny.setup = true

-- =========================================================================
-- Config
-- =========================================================================

SpaceBunny.resetSettings = function()
	SpaceBunny.AdditionalDepots = {}
	SpaceBunny.DecryptionKeys = {}
	SpaceBunny.RewriteDepots = false
end

if SpaceBunny.AdditionalDepots == nil then SpaceBunny.AdditionalDepots = {} end
if SpaceBunny.DecryptionKeys == nil then SpaceBunny.DecryptionKeys = {} end
if SpaceBunny.PackageDepotIds == nil then SpaceBunny.PackageDepotIds = {} end
if SpaceBunny.OriginalDepotCaptured == nil then SpaceBunny.OriginalDepotCaptured = false end

local function countKeys(t)
	local n = 0
	for _ in pairs(t) do
		n = n + 1
	end
	return n
end

SpaceBunny.configLoaded = function()
	local ok, err = pcall(function()
	local previousDepots = SpaceBunny.AdditionalDepots
	local injectionPending = SpaceBunny.RewriteDepots

	SpaceBunny.resetSettings()
	SpaceBunny.ConfigLoaded = true

	local newDepots = SLS.config:getIntList("AdditionalDepots")
	local depotListChanged = not SpaceBunny.sameIntList(previousDepots, newDepots)

	SpaceBunny.AdditionalDepots = newDepots

	SpaceBunny.RewriteDepots = depotListChanged or injectionPending

	SpaceBunny.safeLog("debug", "Queued " .. #SpaceBunny.AdditionalDepots
		.. " additional depots for injection")

	local keyNode = SLS.config:getNode("DecryptionKeys")

	if not keyNode.isDefined then
		log.notifyWarn("Missing DecryptionKeys in config!")
		return
	end

	local keys = keyNode:asPairList()
	local skippedKeys = 0

	for _, v in ipairs(keys) do
		local parsed, depotId, key = pcall(function()
			return v[1]:asInt(), v[2]:asString()
		end)

		if parsed and type(depotId) == "number" and type(key) == "string" then
			SpaceBunny.DecryptionKeys[depotId] = key
		else
			skippedKeys = skippedKeys + 1

			local label = "?"
			pcall(function()
				label = v[1]:asString()
			end)

			SpaceBunny.safeLog("warn",
				"Skipping malformed DecryptionKeys entry: " .. tostring(label))
		end
	end

	if skippedKeys > 0 then
		log.notifyWarn("[SpaceBunny] Skipped " .. skippedKeys
			.. " malformed DecryptionKeys entr" .. (skippedKeys == 1 and "y" or "ies"))
	end

	SpaceBunny.safeLog("debug", "Loaded " .. countKeys(SpaceBunny.DecryptionKeys) .. " decryption keys")

	-- Keep Hubcap's key mirror in sync so it knows what we already have
	hubcapSyncKeys(SpaceBunny.DecryptionKeys)
	end)

	if not ok then
		SpaceBunny.safeLog("error", "configLoaded failed: " .. tostring(err))

		if SpaceBunny.DEBUG then
			error(err, 0)
		end
	end
end


SpaceBunny.reloadFamilySettings = function()
	local home = os.getenv("HOME") or ""
	local configHome = os.getenv("XDG_CONFIG_HOME")
	if configHome == nil or configHome == "" then
		configHome = home .. "/.config"
	end

	local mother_ok, mother = pcall(dofile,
		configHome .. "/SLSsteam/katamari.conf.lua")
	if mother_ok and type(mother) == "table"
			and type(mother.spacebunny) == "table" then
		local section = mother.spacebunny
		if type(section.mrc_enabled) == "boolean" then
			SpaceBunny.MRC_ENABLED = section.mrc_enabled
		end
		if tonumber(section.priority) ~= nil then
			SpaceBunny.PRIORITY = tonumber(section.priority)
		end
	end

	local cfg_ok, cfg = pcall(dofile,
		configHome .. "/SLSsteam/spacebunny.conf.lua")
	if cfg_ok and type(cfg) == "table" then
		if type(cfg.mrc_enabled) == "boolean" then
			SpaceBunny.MRC_ENABLED = cfg.mrc_enabled
		end
		if tonumber(cfg.priority) ~= nil then
			SpaceBunny.PRIORITY = tonumber(cfg.priority)
		end
	end

	if type(KATAMARI) == "table" and type(KATAMARI.plugins) == "table" then
		local meta = KATAMARI.plugins.spacebunny
		if meta ~= nil then
			meta.mrc_enabled = SpaceBunny.MRC_ENABLED ~= false
			meta.priority = SpaceBunny.PRIORITY or meta.priority
		end
	end
end

SpaceBunny.luaReload = function()
	local ok, err = pcall(function()
	SpaceBunny.restoreDepotIds()
	SpaceBunny.resetSettings()

	-- Re-read mrc_config.lua so an edited provider list (or tuning) takes
	-- effect on reload instead of requiring a Steam restart. Cache and
	-- breaker state from the previous providers is dropped.
	SpaceBunny.reloadFamilySettings()
	SpaceBunny.reloadMRCConfig()
	end)

	if not ok then
		SpaceBunny.safeLog("error", "luaReload failed: " .. tostring(err))

		if SpaceBunny.DEBUG then
			error(err, 0)
		end
	end
end

KATAMARI = KATAMARI or { plugins = {} }
KATAMARI.plugins.spacebunny = {
	version = SpaceBunny.VERSION,
	role = "mrc-provider",
	priority = SpaceBunny.PRIORITY or 20,
	mrc_enabled = SpaceBunny.MRC_ENABLED ~= false,
}

SLS.registerCallback("SLSsteam::configLoaded", SpaceBunny.configLoaded)
SLS.registerCallback("SLSsteam::luaReload", SpaceBunny.luaReload)

log.notify("spacebunny.lua " .. SpaceBunny.VERSION .. " loaded!"
	.. (hubcapEnabled() and " (Hubcap auto-fetch active)" or " (no Hubcap plugin)"))
