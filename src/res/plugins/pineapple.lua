-- pinapple_v1.lua (1.0)
--
-- Pinapple is the manifest fallback for the Katamari family.
--
-- When every MRC provider is down, Steam cannot get a manifest request code,
-- so it cannot learn which manifest to download and the update stops with
-- "Missing decryption key" / no manifest.  Pinapple writes the manifest file
-- straight into Steam's depotcache before Steam asks for it, so the download
-- never needs the code at all.
--
-- It is deliberately NOT an MRC plugin and it does NOT hook anything:
--   * no Lua hooks means it can never fight mario/spacebunny for GetMRC or
--     GetPackage, and it survives every game/app update of the client;
--   * it only writes <depot>_<manifest>.manifest into depotcache, which is a
--     plain directory format the x64 client still reads.
--
-- Why the fetch happens in a child process
-- ----------------------------------------
-- SLSsteam runs every Lua callback while holding Lua::stateMutex, and mario's
-- hooks take the same lock (LuaMutex).  A synchronous Hubcap request takes
-- 0.6 s (worker proxy) to 19 s (direct) per call on this machine, so calling
-- curl.downloadString here would stall Steam's depot thread for the whole
-- run.  Instead every request is launched as a detached `curl` child that
-- writes body/exit-code files into a spool directory; Pinapple checks those
-- files from a cheap tick (Network::recvPkt, at most once per second) and
-- keeps one request in flight.
--
-- Flow
-- ----
--   1. plan:  read appmanifest_<app>.acf, find InstalledDepots, queue a
--             Hubcap /manifest/<app>/contents request for each app.
--   2. tick:  when the contents request lands, compute which installed
--             depots need a newer (or missing) manifest and queue one
--             /generate/manifest?depot_id=&manifest_id= request each.
--   3. tick:  validate the depot magic (0xD0 0x17 0xF6 0x71) and write the
--             file into every depotcache directory.
--
-- Family
-- ------
-- Registers KATAMARI.plugins.pinapple (role "manifest-seed"), reads
-- katamari.conf.lua for family/hubcap/proxy plus an optional `pinapple`
-- block, then pinapple.conf.lua as an override.

Pinapple = Pinapple or {
	VERSION = "1.0",
	setup = false,
}

if Pinapple.setup then
	return
end

-- =========================================================================
-- Paths
-- =========================================================================

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

-- =========================================================================
-- Config
-- =========================================================================

local defaults = {
	enabled = true,

	-- pending = only apps with an update waiting
	-- always  = seed every configured app, update or not
	-- off     = disabled
	mode = "pending",
	priority = 35,

	-- SpaceBunny-only means "the MRC path is expected to work".  Pinapple is
	-- the fallback for when it does not, so by default it still seeds.  Set
	-- this to true to obey manifest_mode literally and stand down.
	follow_manifest_mode = false,

	api_key = "",
	base_url = "https://hubcapmanifest.com/api/v1",

	-- auto     = worker proxy first when configured, direct on failure
	-- fallback = direct first, worker proxy on failure
	-- proxy    = worker proxy only
	-- direct   = direct only
	proxy_mode = "auto",
	proxy_url = "",
	proxy_token = "",
	proxy_cooldown = 60,

	apps = {},
	auto_pending = true,
	max_apps = 8,
	max_manifests = 32,

	-- Per-request limit handed to curl --max-time.
	timeout = 25,

	overwrite = false,
	repair_missing = true,
	include_uninstalled_depots = false,
	depotcache_dirs = {},
	spool_dir = "",
	dry_run = false,

	run_on_initialized = true,
	run_on_reload = true,
	run_on_config_loaded = true,

	poll_interval = 1,
	debug = false,
	notify = false,
}

local function readConfigFile(path)
	local handle = io.open(path, "r")
	if handle == nil then
		return nil
	end
	handle:close()

	local ok, result = pcall(dofile, path)
	if not ok then
		log.warn("[Pinapple] Failed to read " .. path .. ": " .. tostring(result))
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

	-- 1. Mother config: shared Hubcap and proxy, then the pinapple section.
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

		applyTable(config, mother.pinapple)
	end

	-- 2. Per-plugin override.  If it exists, it wins over the mother.
	local directory = pluginDirectory()
	local candidates = {
		configHome() .. "/SLSsteam/pinapple.conf.lua",
		directory .. "/pinapple.conf.lua",
		directory .. "/../pinapple.conf.lua",
	}

	for _, path in ipairs(candidates) do
		local values = readConfigFile(path)
		if values ~= nil then
			applyTable(config, values)
			break
		end
	end

	-- 3. Global override, as documented in KATAMARI.md.
	applyTable(config, _G.PinappleConfig)

	config.spool = config.spool_dir ~= "" and config.spool_dir
		or (configHome() .. "/SLSsteam/pinapple")

	Pinapple.config = config

	if type(KATAMARI) == "table" and type(KATAMARI.plugins) == "table" then
		local meta = KATAMARI.plugins.pinapple
		if meta ~= nil then
			meta.priority = tonumber(config.priority) or meta.priority
			meta.mode = config.mode or meta.mode
			meta.version = Pinapple.VERSION
		end
	end

	return config
end

-- =========================================================================
-- Logging
-- =========================================================================

local function notify(message)
	if Pinapple.config ~= nil and Pinapple.config.notify then
		log.notify(message)
	else
		log.info(message)
	end
end

local function debugLog(message)
	if Pinapple.config ~= nil and Pinapple.config.debug then
		log.debug("[Pinapple] " .. message)
	end
end

-- =========================================================================
-- Small utilities
-- =========================================================================

local function quote(value)
	return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
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

local function writeFile(path, content)
	local handle = io.open(path, "wb")
	if handle == nil then
		return false, "cannot open " .. path
	end
	handle:write(content)
	handle:close()
	return true
end

local function readNumber(path)
	local content = readFile(path)
	if content == nil then
		return nil
	end
	return tonumber((content:gsub("%s+$", "")))
end

local function ensureDirectory(path)
	if path == nil or path == "" then
		return
	end
	os.execute("mkdir -p " .. quote(path))
end

local function fileExists(path)
	local handle = io.open(path, "r")
	if handle == nil then
		return false
	end
	handle:close()
	return true
end

-- Returns the curl command to put in front of the -fsS flags, or nil.
--
-- SLS plugins run inside the Steam runtime container, which ships no curl at
-- all.  Its bind-mounted /run/host/usr/bin/curl cannot start either, because
-- the container's own libcurl.so.4 wins the symbol lookup and it dies with
-- "undefined symbol: curl_global_trace".  The runtime's launch client can run
-- the host binary with the host libraries, so prefer that when it works and
-- fall back to a plain curl for non-containerised installs.
local function curlPath()
	local ok = os.execute("steam-runtime-launch-client --host -- /usr/bin/curl --version > /dev/null 2>&1")
	if ok == true or ok == 0 then
		return "steam-runtime-launch-client --host -- /usr/bin/curl"
	end

	local handle = io.popen("command -v curl 2>/dev/null")
	if handle ~= nil then
		local path = handle:read("*l")
		handle:close()
		if path ~= nil and path ~= "" then
			return quote(path)
		end
	end

	for _, candidate in ipairs({ "/usr/bin/curl", "/bin/curl", "/usr/local/bin/curl" }) do
		if fileExists(candidate) then
			return quote(candidate)
		end
	end
	return nil
end

local CURL = curlPath()

-- =========================================================================
-- Steam layout
-- =========================================================================

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
		local content = readFile(root .. "/steamapps/libraryfolders.vdf")
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
		if fileExists(steamapps) then
			dirs[steamapps] = true
		end
	end
	for folder in pairs(libraryFolders()) do
		local steamapps = folder .. "/steamapps"
		if fileExists(steamapps) then
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

	for root in pairs(steamRoots()) do
		local path = root .. "/depotcache"
		if fileExists(path) then
			add(path)
		end
	end
	for steamapps in pairs(steamappsDirs()) do
		local beside = steamapps .. "/depotcache"
		local root = steamapps:gsub("/steamapps$", "") .. "/depotcache"
		if fileExists(beside) then
			add(beside)
		end
		if fileExists(root) then
			add(root)
		end
	end
	-- Explicitly configured directories are created on demand.
	for _, path in ipairs(Pinapple.config.depotcache_dirs or {}) do
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

-- { ["2675401"] = "6014590443281738961", ... }
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

-- =========================================================================
-- Manifest files
-- =========================================================================

local function manifestPath(directory, depot, manifest)
	return directory .. "/" .. depot .. "_" .. manifest .. ".manifest"
end

-- Steam depot manifesto magic: 0xD0 0x17 0xF6 0x71.
local function isManifest(body)
	return type(body) == "string"
		and #body > 64
		and body:sub(1, 4) == "\208\023\246\113"
end

local function manifestInDepotcaches(depot, manifest)
	for directory in pairs(depotcacheDirs()) do
		if fileExists(manifestPath(directory, depot, manifest)) then
			return true
		end
	end
	return false
end

-- Write through a temporary name so Steam never picks up a half-written file.
local function writeManifest(directory, depot, manifest, body)
	ensureDirectory(directory)

	local final = manifestPath(directory, depot, manifest)
	local temporary = final .. ".pinapple"

	local ok, err = writeFile(temporary, body)
	if not ok then
		return false, err
	end

	local renamed, renameErr = os.rename(temporary, final)
	if not renamed then
		os.remove(temporary)
		return false, tostring(renameErr)
	end

	return true
end

local function copyToDepotcaches(depot, manifest, body)
	local written = 0
	for directory in pairs(depotcacheDirs()) do
		local ok, err = writeManifest(directory, depot, manifest, body)
		if ok then
			written = written + 1
			debugLog("wrote " .. manifestPath(directory, depot, manifest))
		else
			log.warn("[Pinapple] Failed to write manifest to " .. directory .. ": " .. tostring(err))
		end
	end
	return written
end

-- =========================================================================
-- Hubcap requests
-- =========================================================================

local function apiKey()
	local key = Pinapple.config.api_key
	if type(key) ~= "string" or key == "" then
		return nil
	end
	return key
end

local function baseUrl()
	return tostring(Pinapple.config.base_url):gsub("/+$", "")
end

local function proxyUrl()
	local url = Pinapple.config.proxy_url
	if type(url) ~= "string" or url == "" then
		return nil
	end
	return url:gsub("/+$", "")
end

local function proxyAllowed()
	local untilTime = Pinapple.proxyCooldownUntil
	return untilTime == nil or os.time() >= untilTime
end

-- { { depot = "2675401", manifest = "6014590443281738961" }, ... }
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

local function directUrl(job)
	if job.kind == "contents" then
		return baseUrl() .. "/manifest/" .. job.app .. "/contents"
	end
	return baseUrl() .. "/generate/manifest?depot_id=" .. job.depot
		.. "&manifest_id=" .. job.manifest
end

local function urlFor(job)
	local direct = directUrl(job)
	if job.route ~= "proxy" then
		return direct
	end

	local proxy = proxyUrl()
	if proxy == nil then
		return direct
	end

	local prefix = baseUrl()
	if direct:sub(1, #prefix) ~= prefix then
		return direct
	end
	return proxy .. direct:sub(#prefix + 1)
end

-- The Authorization header carries a shareable API key, so it goes through a
-- curl --config file instead of argv (argv is visible in `ps`).
local function writeHeaders(path, job)
	local key = apiKey()
	if key:find('["\r\n]') ~= nil then
		return false, "api key contains a character curl config cannot quote"
	end

	local lines = { 'header = "Authorization: Bearer ' .. key .. '"' }

	local token = Pinapple.config.proxy_token
	if job.route == "proxy" and type(token) == "string" and token ~= "" then
		if token:find('["\r\n]') ~= nil then
			return false, "proxy token contains a character curl config cannot quote"
		end
		lines[#lines + 1] = 'header = "X-Katamari-Token: ' .. token .. '"'
	end

	return writeFile(path, table.concat(lines, "\n") .. "\n")
end

-- =========================================================================
-- Engine
-- =========================================================================

local STATE = {
	queue = {},
	job = nil,
	active = false,
	requests = 0,
	seeded = 0,
	failed = 0,
	tokens = {},
	runId = 0,
	lastTick = 0,
}

local function spoolPath(token, suffix)
	return Pinapple.config.spool .. "/" .. token .. "." .. suffix
end

local function cleanupJob(job)
	if job == nil or job.token == nil then
		return
	end
	for _, suffix in ipairs({ "body", "rc", "err", "hdr" }) do
		os.remove(spoolPath(job.token, suffix))
	end
end

-- Body/rc/err/hdr files are ours alone.  Sweeping the directory removes leftovers
-- from a run that was abandoned (a hung curl can still write its files after the
-- abandon deadline) as well as anything left by a previous process.
local function sweepSpool()
	local dir = Pinapple.config.spool
	local listing = io.popen("ls -1 " .. quote(dir) .. " 2>/dev/null")
	if listing == nil then
		return
	end
	for name in listing:lines() do
		if name:match("%.body$") or name:match("%.rc$") or name:match("%.err$") or name:match("%.hdr$") then
			os.remove(dir .. "/" .. name)
		end
	end
	listing:close()
end

-- The mother config's proxy.mode uses fallback|always|never; pinapple's own
-- proxy_mode uses auto|proxy|direct|fallback.  Translate once so both spellings
-- behave the same.
local function proxyMode()
	local mode = Pinapple.config.proxy_mode or "auto"
	if mode == "always" then
		return "proxy"
	end
	if mode == "never" or mode == "off" then
		return "direct"
	end
	return mode
end

local function initialRoute()
	local mode = proxyMode()
	if mode == "proxy" then
		return "proxy"
	end
	if mode == "direct" or mode == "fallback" then
		return "direct"
	end
	if proxyUrl() ~= nil and proxyAllowed() then
		return "proxy"
	end
	return "direct"
end

local function alternateRoute(route)
	if route == "proxy" then
		return "direct"
	end
	if proxyMode() == "direct" then
		return nil
	end
	if proxyUrl() ~= nil and proxyAllowed() then
		return "proxy"
	end
	return nil
end

-- One route switch per job: a blocked direct request is retried through the
-- worker, and a busy worker is retried direct.  Never more than that.
local function retryWithAlternate(job, reason)
	local alternate = alternateRoute(job.route)
	if (job.attempts or 0) >= 1 or alternate == nil then
		return false
	end

	log.info(string.format("[Pinapple] %s %s failed via %s (%s); retrying via %s",
		job.kind, tostring(job.app or job.depot), job.route, reason, alternate))

	job.route = alternate
	job.attempts = (job.attempts or 0) + 1
	table.insert(STATE.queue, 1, job)
	return true
end

local function launch(job)
	job.started = os.time()
	job.token = string.format("r%d-%s-%d", STATE.runId, job.kind == "contents"
		and ("c" .. job.app) or ("m" .. job.depot), job.attempts or 0)

	local body = spoolPath(job.token, "body")
	local rc = spoolPath(job.token, "rc")
	local err = spoolPath(job.token, "err")
	local hdr = spoolPath(job.token, "hdr")
	local limit = tonumber(Pinapple.config.timeout) or 25

	local ok, headerErr = writeHeaders(hdr, job)
	if not ok then
		log.warn("[Pinapple] " .. tostring(headerErr))
		STATE.failed = STATE.failed + 1
		return false
	end

	os.remove(rc)

	local command = "(" .. CURL
		.. " -fsS --max-time " .. limit
		.. " --connect-timeout " .. math.min(15, limit)
		.. " --config " .. quote(hdr)
		.. " -o " .. quote(body)
		.. " " .. quote(urlFor(job))
		.. " 2> " .. quote(err)
		.. " ; echo $? > " .. quote(rc)
		.. " ) &"

	debugLog(string.format("%s %s via %s", job.kind, tostring(job.app or job.depot), job.route))
	os.execute(command)

	STATE.requests = STATE.requests + 1
	STATE.tokens[#STATE.tokens + 1] = job.token
	STATE.job = job
	return true
end

local function candidatesFor(job, remote)
	local config = Pinapple.config
	local wanted = {}

	for _, item in ipairs(remote) do
		local installed = job.installed[item.depot]

		if installed ~= nil then
			local idle = installed == item.manifest
			local present = manifestInDepotcaches(item.depot, item.manifest)

			-- A manifest already in depotcache needs no request: Steam will
			-- find it by name.  overwrite is the only reason to fetch again.
			if config.overwrite or not present then
				if not idle or config.repair_missing then
					wanted[#wanted + 1] = item
				end
			end
		elseif config.include_uninstalled_depots then
			wanted[#wanted + 1] = item
		end
	end

	return wanted
end

local function handleContents(job, body)
	local list = parseContents(body)
	if #list == 0 then
		if retryWithAlternate(job, "no manifests in response") then
			return
		end
		log.warn("[Pinapple] app " .. tostring(job.app) .. ": no manifests in Hubcap response")
		STATE.failed = STATE.failed + 1
		return
	end

	local wanted = candidatesFor(job, list)
	if #wanted == 0 then
		log.info("[Pinapple] app " .. tostring(job.app) .. ": manifests already current")
		return
	end

	local limit = math.min(#wanted, tonumber(Pinapple.config.max_manifests) or 32)

	if Pinapple.config.dry_run then
		for index = 1, limit do
			notify("[Pinapple] would seed " .. wanted[index].depot .. "_"
				.. wanted[index].manifest .. " for app " .. tostring(job.app))
		end
		return
	end

	log.info(string.format("[Pinapple] app %s: %d manifest(s) to seed", tostring(job.app), limit))

	-- Insert in order at the front so this app finishes before the next app's
	-- contents request, and depots are seeded one at a time.
	for index = limit, 1, -1 do
		local item = wanted[index]
		table.insert(STATE.queue, 1, {
			kind = "manifest",
			app = job.app,
			depot = item.depot,
			manifest = item.manifest,
			route = job.route,
			attempts = 0,
		})
	end
end

local function handleManifest(job, body)
	if not isManifest(body) then
		if retryWithAlternate(job, "not a Steam manifest") then
			return
		end
		log.warn("[Pinapple] depot " .. tostring(job.depot) .. ": response is not a Steam manifest")
		STATE.failed = STATE.failed + 1
		return
	end

	if not Pinapple.config.overwrite and manifestInDepotcaches(job.depot, job.manifest) then
		debugLog("already cached: " .. job.depot .. "_" .. job.manifest)
		return
	end

	local count = copyToDepotcaches(job.depot, job.manifest, body)
	if count == 0 then
		STATE.failed = STATE.failed + 1
		return
	end

	STATE.seeded = STATE.seeded + 1
	notify("[Pinapple] seeded " .. job.depot .. "_" .. job.manifest
		.. " for app " .. tostring(job.app) .. " (" .. count .. " cache dir(s))")
end

local function noteRateLimit(job, errText, body)
	local text = (errText or "") .. (body or "")
	if text:find("429", 1, true) == nil and text:find("rate_limited", 1, true) == nil then
		return false
	end

	local retry = tonumber(Pinapple.config.proxy_cooldown) or 60
	Pinapple.proxyCooldownUntil = os.time() + retry

	if job.route == "proxy" then
		log.warn("[Pinapple] worker rate limit hit; direct requests only for "
			.. tostring(retry) .. "s")
	end
	return true
end

local function handleFailure(job, rc)
	local errText = readFile(spoolPath(job.token, "err")) or ""
	local firstLine = errText:match("([^\r\n]+)") or ""
	local body = readFile(spoolPath(job.token, "body"))

	noteRateLimit(job, errText, body)

	local reason = (rc == 0 and "empty response") or ("curl " .. tostring(rc))
	if retryWithAlternate(job, reason) then
		return
	end

	log.warn(string.format("[Pinapple] %s %s failed via %s: %s",
		job.kind, tostring(job.app or job.depot), job.route,
		(firstLine ~= "" and firstLine) or reason))
	STATE.failed = STATE.failed + 1
end

local function finish()
	local message = string.format(
		"[Pinapple] run complete: %d request(s), %d manifest(s) seeded, %d failed",
		STATE.requests, STATE.seeded, STATE.failed)
	notify(message)

	for _, token in ipairs(STATE.tokens) do
		for _, suffix in ipairs({ "body", "rc", "err", "hdr" }) do
			os.remove(spoolPath(token, suffix))
		end
	end
	sweepSpool()

	STATE.queue = {}
	STATE.tokens = {}
	STATE.job = nil
	STATE.active = false
end

local function pump()
	if not STATE.active then
		return
	end

	local job = STATE.job

	if job ~= nil then
		local rc = readNumber(spoolPath(job.token, "rc"))

		if rc == nil then
			local limit = tonumber(Pinapple.config.timeout) or 25
			if os.time() - job.started > limit + 10 then
				log.warn("[Pinapple] " .. job.kind .. " " .. tostring(job.app or job.depot)
					.. " timed out")
				cleanupJob(job)
				STATE.job = nil
				STATE.failed = STATE.failed + 1
			else
				return
			end
		else
			STATE.job = nil
			local body = readFile(spoolPath(job.token, "body"))
			cleanupJob(job)

			if rc ~= 0 then
				handleFailure(job, rc)
			elseif job.kind == "contents" then
				if body == nil then
					log.warn("[Pinapple] app " .. tostring(job.app) .. ": empty response")
					STATE.failed = STATE.failed + 1
				else
					handleContents(job, body)
				end
			else
				if body == nil then
					log.warn("[Pinapple] depot " .. tostring(job.depot) .. ": empty response")
					STATE.failed = STATE.failed + 1
				else
					handleManifest(job, body)
				end
			end
		end
	end

	if STATE.job ~= nil then
		return
	end

	local nextJob = table.remove(STATE.queue, 1)
	if nextJob == nil then
		finish()
		return
	end

	nextJob.route = nextJob.route or initialRoute()
	nextJob.attempts = nextJob.attempts or 0
	launch(nextJob)
end

-- Cheap tick: SLSsteam fires this on every received packet, and every Lua
-- callback runs under the same mutex the depot hooks use, so keep it tiny.
local function tick()
	if not STATE.active then
		return
	end

	local now = os.time()
	if now == STATE.lastTick then
		return
	end
	STATE.lastTick = now

	pump()
end

-- =========================================================================
-- Planning
-- =========================================================================

local function installedApps(onlyPending)
	local apps = {}
	local seen = {}

	for steamapps in pairs(steamappsDirs()) do
		local listing = io.popen("ls -1 " .. quote(steamapps) .. "/appmanifest_*.acf 2>/dev/null")
		if listing ~= nil then
			for path in listing:lines() do
				local appId = path:match("appmanifest_(%d+)%.acf$")
				if appId ~= nil and not seen[appId] then
					local content = readFile(path)
					if content ~= nil and (not onlyPending or isPendingUpdate(content)) then
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

local function appList(onlyPending)
	local list = {}
	local seen = {}

	local function add(value)
		local appId = tostring(value)
		if appId:match("^%d+$") and not seen[appId] then
			seen[appId] = true
			list[#list + 1] = appId
		end
	end

	for _, appId in ipairs(Pinapple.config.apps or {}) do
		add(appId)
	end

	if Pinapple.config.auto_pending then
		for _, appId in ipairs(installedApps(onlyPending)) do
			add(appId)
		end
	end

	local limit = tonumber(Pinapple.config.max_apps) or 8
	while #list > limit do
		table.remove(list)
	end

	return list
end

local function familyAllows()
	if type(KATAMARI) ~= "table" or type(KATAMARI.manifestMode) ~= "function" then
		return true, "no family registry"
	end

	local ok, mode = pcall(KATAMARI.manifestMode)
	if not ok or type(mode) ~= "string" then
		return true, "family mode unavailable"
	end

	if mode ~= "spacebunny-only" then
		return true, mode
	end

	if Pinapple.config.follow_manifest_mode == true then
		return false, mode
	end

	return true, mode .. " (fallback override)"
end

function Pinapple.run(reason)
	local config = Pinapple.config

	if STATE.active then
		-- The poll runs off "Network::recvPkt", so a client sitting idle at the
		-- login screen can leave a run hanging with no ticks at all.  Do not
		-- let that frozen run block every later trigger.
		local stuck = STATE.job
		local limit = tonumber(config.timeout) or 25

		if stuck == nil or os.time() - (stuck.started or 0) > limit + 30 then
			log.warn("[Pinapple] previous run stopped receiving ticks; abandoning it")
			STATE.queue = {}
			STATE.tokens = {}
			STATE.job = nil
			STATE.active = false
		else
			debugLog("run already in progress; ignoring " .. tostring(reason))
			return
		end
	end

	if not config.enabled then
		debugLog("disabled; ignoring " .. tostring(reason))
		return
	end
	if config.mode == "off" then
		debugLog("mode is off; ignoring " .. tostring(reason))
		return
	end
	if CURL == nil then
		log.warn("[Pinapple] no usable curl (host launch client or PATH); manifest seeding is unavailable")
		return
	end
	if apiKey() == nil then
		log.warn("[Pinapple] Hubcap API key is missing; edit pinapple.conf.lua")
		return
	end

	local allowed, familyMode = familyAllows()
	if not allowed then
		debugLog("family manifest mode is " .. tostring(familyMode) .. "; not seeding")
		return
	end

	local onlyPending = config.mode == "pending"
	local apps = appList(onlyPending)
	if #apps == 0 then
		debugLog("no apps need manifests (" .. tostring(reason) .. ")")
		return
	end

	ensureDirectory(config.spool)
	sweepSpool()

	STATE.queue = {}
	STATE.tokens = {}
	STATE.requests = 0
	STATE.seeded = 0
	STATE.failed = 0
	STATE.runId = STATE.runId + 1
	STATE.lastTick = os.time()

	local planned = 0

	for _, appId in ipairs(apps) do
		local path = acfPath(appId)
		if path == nil then
			debugLog("no appmanifest for " .. appId .. ", skipping")
		else
			local acf = readFile(path) or ""

			if onlyPending and not isPendingUpdate(acf) then
				debugLog("app " .. appId .. " has no pending update, skipping")
			else
				STATE.queue[#STATE.queue + 1] = {
					kind = "contents",
					app = appId,
					installed = parseInstalledDepots(acf),
					attempts = 0,
				}
				planned = planned + 1
			end
		end
	end

	if planned == 0 then
		debugLog("nothing to seed (" .. tostring(reason) .. ")")
		return
	end

	STATE.active = true
	notify(string.format("[Pinapple] planning %d app(s) [%s, family %s]",
		planned, tostring(reason), familyMode))

	pump()
end

Pinapple.tick = tick
Pinapple.loadConfig = loadConfig

-- =========================================================================
-- Registration
-- =========================================================================

loadConfig()

KATAMARI = KATAMARI or { plugins = {} }
KATAMARI.plugins.pinapple = {
	version = Pinapple.VERSION,
	role = "manifest-seed",
	priority = tonumber(Pinapple.config.priority) or 35,
	mode = Pinapple.config.mode or "pending",
	fallback_for = "bakeneko",
	description = "seeds depotcache manifests when MRC providers are down",
}

-- One info line per trigger: these fire a handful of times per session, and
-- they are the only way to tell "the run declined" from "we were never asked".
if type(SLS) == "table" and type(SLS.registerCallback) == "function" then
	SLS.registerCallback("SLSsteam::initialized", function()
		log.info("[Pinapple] callback initialized")
		if Pinapple.config.run_on_initialized then
			Pinapple.run("initialized")
		end
	end)

	SLS.registerCallback("SLSsteam::luaReload", function()
		log.info("[Pinapple] callback luaReload")
		loadConfig()
		if Pinapple.config.run_on_reload then
			Pinapple.run("luaReload")
		end
	end)

	SLS.registerCallback("SLSsteam::configLoaded", function()
		log.info("[Pinapple] callback configLoaded")
		if Pinapple.config.run_on_config_loaded then
			Pinapple.run("configLoaded")
		end
	end)

	SLS.registerCallback("Network::recvPkt", function()
		tick()
	end)
end

Pinapple.setup = true

notify("pinapple_v1.lua " .. Pinapple.VERSION .. " loaded ("
	.. (Pinapple.config.enabled and Pinapple.config.mode or "disabled")
	.. (CURL ~= nil and "" or ", curl missing") .. ")")