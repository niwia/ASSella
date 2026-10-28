-- bakeneko.lua (0.1)
--
-- Bakeneko pre-seeds Steam's depotcache with Hubcap manifest files so Steam can
-- apply an update without asking an MRC provider for a manifest request code.
--
-- This is deliberately NOT an MRC plugin.  MRC authorises Steam's manifest
-- download; Bakeneko puts the manifest on disk first, so Steam never needs to
-- download it.  Fresh installs already get their keys/config from the normal
-- injection path; Bakeneko's job is the update path.
--
-- Flow per app:
--   1. Find appmanifest_<app>.acf and its installed depot manifest IDs.
--   2. Ask Hubcap /manifest/<app>/contents for the current manifest IDs.
--   3. For installed depots whose IDs differ, fetch the single manifest from
--      /generate/manifest?depot_id=...&manifest_id=... (one call per depot).
--   4. Write <depot>_<manifest>.manifest into every Steam depotcache.
--   5. Steam's update then uses the local manifest and skips GetManifestRequestCode.
--
-- No config.yaml writes, no ManifestIds pinning, no decryption keys.  Those are
-- intentionally out of scope for this version.

Bakeneko = Bakeneko or {
	setup = false,
	running = false,
	VERSION = "0.1",
}

if Bakeneko.setup then
	return
end

local function pluginDirectory()
	local source = debug.getinfo(1, "S").source
	if source:sub(1, 1) == "@" then
		source = source:sub(2)
	end
	return source:match("^(.*)[/\\]") or "."
end

local function configHome()
	local xdg = os.getenv("XDG_CONFIG_HOME")
	if xdg ~= nil and xdg ~= "" then
		return xdg
	end
	return (os.getenv("HOME") or "") .. "/.config"
end

local function home()
	return os.getenv("HOME") or ""
end

local defaults = {
	enabled = false,

	-- Family ordering.  "before" = seed manifests, SpaceBunny MRC as fallback;
	-- "only" = seed, and SpaceBunny must not call MRC; "off" = disabled.
	mode = "before",
	priority = 10,

	api_key = "",
	base_url = "https://hubcapmanifest.com/api/v1",
	apps = {},
	auto_pending = false,
	run_on_initialized = false,
	only_pending = true,
	dry_run = true,
	overwrite = false,
	repair_missing = true,
	max_apps = 8,
	max_manifests = 32,
	timeout = 30,
	-- Proxy fallback for Hubcap requests.  "fallback" = direct, then proxy on
	-- empty/HTML/invalid responses; "always" = proxy first; "never" = direct only.
	proxy_mode = "fallback",
	proxy_cooldown = 60,
	-- The SpaceBunny Cloudflare worker's /hubcap route by default.
	proxy_url = "https://rapid-thunder-fba1wirecutter.7ucking.workers.dev/hubcap",
	-- Optional shared secret for the worker's X-Katamari-Token check.
	proxy_token = "",
	include_uninstalled_depots = false,
	cache_dir = "",
	depotcache_dirs = {},
	debug = false,
}

local function readConfigFile(path)
	local handle = io.open(path, "r")
	if handle == nil then
		return nil
	end
	handle:close()

	local ok, result = pcall(dofile, path)
	if not ok then
		log.warn("[Bakeneko] Failed to read " .. path .. ": " .. tostring(result))
		return nil
	end
	return type(result) == "table" and result or nil
end

local function applyTable(target, source)
	if type(source) ~= "table" then
		return
	end
	for key, value in pairs(source) do
		target[key] = value
	end
end

local function loadConfig()
	local config = {}
	for key, value in pairs(defaults) do
		config[key] = value
	end

	-- 1. Mother config: shared Hubcap and proxy, then the bakeneko section.
	local mother = readConfigFile(configHome() .. "/SLSsteam/katamari.conf.lua")
	if mother ~= nil then
		local hubcap = mother.hubcap
		if type(hubcap) == "table" then
			config.api_key = hubcap.api_key or config.api_key
			config.base_url = hubcap.base_url or config.base_url
			config.timeout = tonumber(hubcap.timeout) or config.timeout
		end

		local proxy = mother.proxy
		if type(proxy) == "table" then
			config.proxy_url = proxy.worker_url or proxy.url or config.proxy_url
			config.proxy_token = proxy.worker_token or proxy.token or config.proxy_token
			config.proxy_mode = proxy.mode or config.proxy_mode
			config.proxy_cooldown = tonumber(proxy.cooldown) or config.proxy_cooldown
		end

		applyTable(config, mother.bakeneko)
	end

	-- 2. Per-plugin override.  If it exists, it wins over the mother.
	local directory = pluginDirectory()
	local candidates = {
		configHome() .. "/SLSsteam/bakeneko.conf.lua",
		directory .. "/bakeneko.conf.lua",
		directory .. "/../bakeneko.conf.lua",
	}

	for _, path in ipairs(candidates) do
		local values = readConfigFile(path)
		if values ~= nil then
			applyTable(config, values)
			break
		end
	end

	Bakeneko.config = config

	-- Keep the live Katamari registration in sync when luaReload re-reads the
	-- mother config.  The table may not exist yet during the first load.
	if type(KATAMARI) == "table" and type(KATAMARI.plugins) == "table" then
		local meta = KATAMARI.plugins.bakeneko
		if meta ~= nil then
			meta.mode = config.mode or meta.mode
			meta.priority = tonumber(config.priority) or meta.priority
		end
	end

	return config
end

local function debugLog(message)
	if Bakeneko.config ~= nil and Bakeneko.config.debug then
		log.debug("[Bakeneko] " .. message)
	end
end

local function apiKey()
	local key = Bakeneko.config.api_key
	if type(key) ~= "string" or key == "" then
		return nil
	end
	return key
end

local function baseUrl()
	return tostring(Bakeneko.config.base_url):gsub("/+$", "")
end

local function curlAvailable()
	return type(curl) == "table" and type(curl.downloadString) == "function"
end

local function proxyUrlFor(url)
	local proxy = Bakeneko.config.proxy_url
	if type(proxy) ~= "string" or proxy == "" then
		return nil
	end

	local prefix = baseUrl()
	if url:sub(1, #prefix) ~= prefix then
		return nil
	end

	return proxy:gsub("/+$", "") .. url:sub(#prefix + 1)
end

local function authHeaders(useProxy)
	local headers = { "Authorization: Bearer " .. apiKey() }

	local token = Bakeneko.config.proxy_token
	if useProxy and type(token) == "string" and token ~= "" then
		headers[#headers + 1] = "X-Katamari-Token: " .. token
	end

	return headers
end

local function httpGet(url, headers, useProxy)
	local target = url
	if useProxy then
		target = proxyUrlFor(url)
		if target == nil then
			return nil, "proxy is not configured"
		end
	end

	local ok, body = pcall(curl.downloadString, target, headers, tonumber(Bakeneko.config.timeout) or 30)
	if not ok then
		return nil, "request failed: " .. tostring(body)
	end
	if type(body) ~= "string" or body == "" then
		return nil, "empty response"
	end

	return body, nil
end

local function looksBlocked(body)
	if type(body) ~= "string" or body == "" then
		return true
	end
	local head = body:sub(1, 200):lower()
	return head:find("<!doctype", 1, true) ~= nil
		or head:find("<html", 1, true) ~= nil
		or head:find("cloudflare", 1, true) ~= nil
		or head:find("attention required", 1, true) ~= nil
end

local function proxyAllowed()
	local untilTime = Bakeneko.proxyCooldownUntil
	return untilTime == nil or os.time() >= untilTime
end

local function noteRateLimit(body)
	if type(body) ~= "string" or not body:find("rate_limited", 1, true) then
		return nil
	end

	local retry = tonumber(body:match('"retry_after"%s*:%s*(%d+)'))
	retry = math.max(1, retry or tonumber(Bakeneko.config.proxy_cooldown) or 60)
	Bakeneko.proxyCooldownUntil = os.time() + retry

	if not Bakeneko.rateWarned then
		Bakeneko.rateWarned = true
		log.warn("[Bakeneko] worker rate limit hit; pausing proxy requests for "
			.. tostring(retry) .. "s")
	end

	return retry
end

local function warnProxyOnce()
	if Bakeneko.proxyWarned then
		return
	end
	Bakeneko.proxyWarned = true
	log.info("[Bakeneko] direct Hubcap request failed; retrying via the worker proxy")
end

-- { { depot = "892971", manifest = "4702704970510748940" }, ... }
local function parseContents(body)
	local result = {}
	for object in body:gmatch("{[^{}]-}") do
		local depot = object:match('"depot_id"%s*:%s*"(%d+)"')
		local manifest = object:match('"manifest_id"%s*:%s*"(%d+)"')
		if depot ~= nil and manifest ~= nil then
			result[#result + 1] = { depot = depot, manifest = manifest }
		end
	end
	return result
end

local function parseInstalledDepots(acf)
	local installed = {}
	local inBlock = false
	local depth = 0
	local currentDepot = nil

	for line in acf:gmatch("[^\r\n]+") do
		if not inBlock then
			if line:match('^%s*"InstalledDepots"%s*$') then
				inBlock = true
				depth = 0
			end
		else
			if line:match("^%s*{%s*$") then
				depth = depth + 1
			elseif line:match("^%s*}%s*$") then
				depth = depth - 1
				if depth <= 0 then
					break
				end
			else
				local depot = line:match('^%s*"(%d+)"%s*$')
				if depot ~= nil and depth == 1 then
					currentDepot = depot
				end

				local manifest = line:match('^%s*"manifest"%s*"(%d+)"')
				if manifest ~= nil and currentDepot ~= nil then
					installed[currentDepot] = manifest
					currentDepot = nil
				end
			end
		end
	end

	return installed
end

local function readFile(path)
	local handle = io.open(path, "rb")
	if handle == nil then
		return nil
	end
	local content = handle:read("*a")
	handle:close()
	return content
end

local function ensureDirectory(path)
	pcall(function()
		os.execute("mkdir -p '" .. tostring(path):gsub("'", "'\\''") .. "'")
	end)
end

local function fileExists(path)
	local handle = io.open(path, "r")
	if handle == nil then
		return false
	end
	handle:close()
	return true
end

local function steamRoots()
	local roots = {}
	local function add(root)
		if root ~= nil and root ~= "" and not roots[root] then
			roots[root] = true
		end
	end

	add(home() .. "/.local/share/Steam")
	add(home() .. "/.steam/steam")
	add(home() .. "/.steam/root")
	add(home() .. "/.var/app/com.valvesoftware.Steam/data/Steam")

	return roots
end

local function libraryFolders()
	local folders = {}
	for root in pairs(steamRoots()) do
		local path = root .. "/steamapps/libraryfolders.vdf"
		local content = readFile(path)
		if content ~= nil then
			for value in content:gmatch('"path"%s*"([^"]+)"') do
				folders[value] = true
			end
		end
	end
	return folders
end

local function steamappsDirs()
	local dirs = {}
	for root in pairs(steamRoots()) do
		local steamapps = root .. "/steamapps"
		local handle = io.open(steamapps, "r")
		if handle ~= nil then
			handle:close()
			dirs[steamapps] = true
		end
	end
	for folder in pairs(libraryFolders()) do
		local steamapps = folder .. "/steamapps"
		local handle = io.open(steamapps, "r")
		if handle ~= nil then
			handle:close()
			dirs[steamapps] = true
		end
	end
	return dirs
end

local function depotcacheDirs()
	local dirs = {}
	local function add(path)
		if path ~= nil and path ~= "" and not dirs[path] then
			dirs[path] = true
		end
	end

	for path in pairs(steamRoots()) do
		if fileExists(path) then
			add(path .. "/depotcache")
		end
	end
	for steamapps in pairs(steamappsDirs()) do
		add(steamapps .. "/depotcache")
		add(steamapps:gsub("/steamapps$", "") .. "/depotcache")
	end
	for _, path in ipairs(Bakeneko.config.depotcache_dirs or {}) do
		add(path)
	end

	return dirs
end

local function acfPath(appId)
	for steamapps in pairs(steamappsDirs()) do
		local path = string.format("%s/appmanifest_%s.acf", steamapps, tostring(appId))
		if fileExists(path) then
			return path
		end
	end
	return nil
end

local function isPendingUpdate(acf)
	local bit = require("bit")
	local flags = tonumber(acf:match('"StateFlags"%s*"(%d+)"')) or 0
	return bit.band(flags, 2) ~= 0 or bit.band(flags, 512) ~= 0
end

local function validManifest(body)
	-- Steam depot manifesto magic: 0xD0 0x17 0xF6 0x71.
	return type(body) == "string"
		and #body > 64
		and body:sub(1, 4) == "\208\023\246\113"
end

local function manifestPath(directory, depot, manifest)
	return directory .. "/" .. depot .. "_" .. manifest .. ".manifest"
end

local function copyToDepotcaches(depot, manifest, body)
	local written = 0
	local dirs = depotcacheDirs()

	for directory in pairs(dirs) do
		ensureDirectory(directory)

		local ok = pcall(function()
			local path = manifestPath(directory, depot, manifest)
			local handle = io.open(path, "wb")
			if handle == nil then
				error("cannot open " .. path)
			end
			handle:write(body)
			handle:close()
		end)

		if ok then
			written = written + 1
			debugLog("wrote " .. manifestPath(directory, depot, manifest))
		else
			log.warn("[Bakeneko] Failed to write manifest to " .. directory)
		end
	end

	return written
end

local function manifestInDepotcaches(depot, manifest)
	for directory in pairs(depotcacheDirs()) do
		if fileExists(manifestPath(directory, depot, manifest)) then
			return true
		end
	end
	return false
end

local function cachedManifest(depot, manifest)
	local cache = Bakeneko.config.cache_dir
	if type(cache) == "string" and cache ~= "" then
		return readFile(manifestPath(cache, depot, manifest))
	end
	return nil
end

local function saveCache(depot, manifest, body)
	local cache = Bakeneko.config.cache_dir
	if type(cache) ~= "string" or cache == "" then
		return
	end

	pcall(function()
		ensureDirectory(cache)
		local handle = io.open(manifestPath(cache, depot, manifest), "wb")
		if handle ~= nil then
			handle:write(body)
			handle:close()
		end
	end)
end

local function tryContents(appId, useProxy)
	local url = baseUrl() .. "/manifest/" .. tostring(appId) .. "/contents"
	local body, err = httpGet(url, authHeaders(useProxy), useProxy)

	if body ~= nil then
		local retry = noteRateLimit(body)
		if retry ~= nil then
			return nil, "rate limited for " .. tostring(retry) .. "s"
		end
	end

	if body == nil or looksBlocked(body) then
		return nil, err or "blocked response"
	end

	local list = parseContents(body)
	if #list == 0 then
		return nil, "no manifests in response"
	end

	return list, nil
end

local function fetchContents(appId)
	local mode = Bakeneko.config.proxy_mode or "fallback"
	local list, err

	if mode ~= "always" then
		list, err = tryContents(appId, false)
		if list ~= nil then
			return list
		end
	end

	if mode ~= "never" and proxyAllowed() then
		warnProxyOnce()
		local proxied, proxyErr = tryContents(appId, true)
		if proxied ~= nil then
			return proxied
		end
		err = proxyErr or err
	end

	log.warn("[Bakeneko] Hubcap contents failed for " .. tostring(appId) .. ": " .. tostring(err))
	return nil
end

local function manifestUrl(item)
	return baseUrl() .. "/generate/manifest?depot_id=" .. item.depot
		.. "&manifest_id=" .. item.manifest
end

local function tryManifest(item, useProxy)
	local body, err = httpGet(manifestUrl(item), authHeaders(useProxy), useProxy)

	if body ~= nil then
		local retry = noteRateLimit(body)
		if retry ~= nil then
			return nil, "rate limited for " .. tostring(retry) .. "s"
		end
	end

	if body == nil then
		return nil, err
	end
	if not validManifest(body) then
		return nil, "not a Steam manifest"
	end

	return body, nil
end

local function fetchManifest(item)
	local mode = Bakeneko.config.proxy_mode or "fallback"
	local body, err

	if mode ~= "always" then
		body, err = tryManifest(item, false)
		if body ~= nil then
			return body
		end
	end

	if mode ~= "never" and proxyAllowed() then
		warnProxyOnce()
		local proxied, proxyErr = tryManifest(item, true)
		if proxied ~= nil then
			return proxied
		end
		err = proxyErr or err
	end

	return nil, err
end

local function syncApp(appId)
	local path = acfPath(appId)
	if path == nil then
		debugLog("no appmanifest for " .. tostring(appId) .. ", skipping")
		return 0
	end

	local acf = readFile(path) or ""

	if Bakeneko.config.only_pending and not isPendingUpdate(acf) then
		debugLog("app " .. tostring(appId) .. " has no pending update, skipping")
		return 0
	end

	local installed = parseInstalledDepots(acf)
	local remote = fetchContents(appId)
	if remote == nil then
		return 0
	end

	local candidates = {}
	for _, item in ipairs(remote) do
		local installedManifest = installed[item.depot]

		if installedManifest ~= nil then
			local idDiffers = installedManifest ~= item.manifest
			local fileMissing = not manifestInDepotcaches(item.depot, item.manifest)

			if idDiffers or (Bakeneko.config.repair_missing and fileMissing) then
				candidates[#candidates + 1] = item
			end
		elseif Bakeneko.config.include_uninstalled_depots then
			candidates[#candidates + 1] = item
		end
	end

	if #candidates == 0 then
		log.info("[Bakeneko] " .. tostring(appId) .. " manifests are already current")
		return 0
	end

	local limit = math.min(#candidates, tonumber(Bakeneko.config.max_manifests) or 32)
	local written = 0

	for index = 1, limit do
		local item = candidates[index]

		if manifestInDepotcaches(item.depot, item.manifest) and not Bakeneko.config.overwrite then
			debugLog("already cached: " .. item.depot .. "_" .. item.manifest)
			-- Nothing to do; do not spend a Hubcap call.
		else
		local cache = cachedManifest(item.depot, item.manifest)

		if Bakeneko.config.dry_run then
			log.notify("[Bakeneko] would fetch " .. item.depot .. "_" .. item.manifest
				.. " for app " .. tostring(appId))
		else
			local body = cache

			if body == nil or Bakeneko.config.overwrite then
				local fetched, err = fetchManifest(item)

				if fetched == nil then
					log.warn("[Bakeneko] manifest fetch failed for " .. item.depot
						.. ": " .. tostring(err))
				else
					body = fetched
					saveCache(item.depot, item.manifest, body)
				end
			end

			if body ~= nil and validManifest(body) then
				local count = copyToDepotcaches(item.depot, item.manifest, body)
				if count > 0 then
					written = written + 1
					log.notify("[Bakeneko] seeded " .. item.depot .. "_" .. item.manifest
						.. " for app " .. tostring(appId) .. " (" .. count .. " cache dir(s))")
				end
			end
		end
		end
	end

	return written
end

local function pendingApps()
	local apps = {}
	local seen = {}
	local bit = require("bit")

	for steamapps in pairs(steamappsDirs()) do
		local listing = io.popen("ls -1 '" .. steamapps:gsub("'", "'\\''")
			.. "'/appmanifest_*.acf 2>/dev/null")
		if listing ~= nil then
			for path in listing:lines() do
				local appId = path:match("appmanifest_(%d+)%.acf$")
				if appId ~= nil and not seen[appId] then
					local content = readFile(path)
					if content ~= nil and isPendingUpdate(content) then
						seen[appId] = true
						apps[#apps + 1] = appId
					end
				end
			end
			listing:close()
		end
	end

	table.sort(apps)
	return apps
end

local function appList()
	local list = {}
	local seen = {}

	local function add(value)
		local appId = tostring(value)
		if appId:match("^%d+$") and not seen[appId] then
			seen[appId] = true
			list[#list + 1] = appId
		end
	end

	for _, appId in ipairs(Bakeneko.config.apps or {}) do
		add(appId)
	end

	if Bakeneko.config.auto_pending then
		for _, appId in ipairs(pendingApps()) do
			add(appId)
		end
	end

	local limit = tonumber(Bakeneko.config.max_apps) or 8
	while #list > limit do
		table.remove(list)
	end

	return list
end

function Bakeneko.run(reason)
	if Bakeneko.running then
		return
	end
	if not Bakeneko.config.enabled or Bakeneko.config.mode == "off" then
		return
	end

	if type(KATAMARI) == "table" and type(KATAMARI.manifestMode) == "function"
			and KATAMARI.manifestMode() == "spacebunny-only" then
		return
	end
	if not curlAvailable() then
		log.warn("[Bakeneko] curl.downloadString is unavailable")
		return
	end
	if apiKey() == nil then
		log.warn("[Bakeneko] Hubcap API key is missing; edit bakeneko.conf.lua")
		return
	end

	Bakeneko.running = true
	local ok, err = pcall(function()
		local apps = appList()

		if #apps == 0 then
			log.info("[Bakeneko] no apps configured (set apps or auto_pending)")
			return
		end

		log.notify("[Bakeneko] syncing " .. #apps .. " app(s) [" .. tostring(reason) .. "]")
		local written = 0

		for _, appId in ipairs(apps) do
			written = written + syncApp(appId)
		end

		log.info("[Bakeneko] run complete: " .. written .. " manifest(s) seeded")
	end)

	Bakeneko.running = false

	if not ok then
		log.warn("[Bakeneko] run failed: " .. tostring(err))
	end
end

loadConfig()

KATAMARI = KATAMARI or { plugins = {} }
KATAMARI.plugins.bakeneko = {
	version = Bakeneko.VERSION,
	role = "manifest-seed",
	priority = tonumber(Bakeneko.config.priority) or 10,
	mode = Bakeneko.config.mode or "before",
}

-- Optional programmatic trigger from another plugin:
--   Bakeneko.run("manual")
if type(SLS) == "table" and type(SLS.registerCallback) == "function" then
	SLS.registerCallback("SLSsteam::initialized", function()
		if Bakeneko.config.run_on_initialized then
			Bakeneko.run("initialized")
		end
	end)
	SLS.registerCallback("SLSsteam::luaReload", function()
		loadConfig()
		Bakeneko.run("luaReload")
	end)
end

Bakeneko.setup = true

log.notify("bakeneko.lua " .. Bakeneko.VERSION .. " loaded ("
	.. (Bakeneko.config.enabled and "enabled" or "disabled")
	.. ", " .. (Bakeneko.config.dry_run and "dry-run" or "live") .. ")")
