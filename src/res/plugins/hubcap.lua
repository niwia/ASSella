-- hubcap.lua (1.0.0)
--
-- Standalone Hubcap auto-fetch plugin for SLSsteam.
-- Resolves missing depot decryption keys by fetching Lua manifests
-- from the Hubcap API and writing keys back into config.yaml.
--
-- Designed to be loaded independently of SpaceBunny. SpaceBunny 1.8.1+
-- detects this plugin and delegates key resolution to it via the
-- HubcapResolver global table.
--
-- Config: ~/.config/SLSsteam/spacebunny.conf.lua (shared with SpaceBunny)
--   hubcap = {
--     enabled = true,
--     api_key = "...",
--     base_url = "https://hubcapmanifest.com/api/v1",
--   }

HubcapResolver = HubcapResolver or {
	setup = false,
}

if HubcapResolver.setup then
	return
end

-- =========================================================================
-- Helpers
-- =========================================================================

local function sbLog(level, msg)
	print("[Hubcap] " .. msg)
	pcall(function() log[level]("[Hubcap] " .. msg) end)
end

HubcapResolver.safeLog = function(level, msg)
	pcall(function()
		if type(msg) == "string" then
			log[level](msg)
		else
			log[level](tostring(msg))
		end
	end)
end

local function sbCount(t)
	local n = 0
	for _ in pairs(t) do
		n = n + 1
	end
	return n
end

local function sbIsHex64(s)
	return type(s) == "string" and #s == 64 and s:match("^%x+$") ~= nil
end

local function sbSplitLines(text)
	local out = {}
	local pos = 1

	while true do
		local nl = text:find("\n", pos, true)

		if nl == nil then
			out[#out + 1] = text:sub(pos)
			break
		end

		out[#out + 1] = text:sub(pos, nl - 1)
		pos = nl + 1
	end

	return out
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

local function sbHubcapAuthError(body)
	local detail = body:match('"detail"%s*:%s*"([^"]*)"')

	if detail == nil then
		return nil
	end

	if detail:find("API key required", 1, true)
			or detail:find("Invalid API key", 1, true)
			or detail:find("not found or expired", 1, true) then
		return detail
	end

	return nil
end

-- =========================================================================
-- State
-- =========================================================================

HubcapResolver.HUBCAP = {
	enabled = false,
	base_url = "https://hubcapmanifest.com/api/v1",
	api_key = nil,
	timeout_sec = 5,
	write_config = true,
	notify = true,
	preflight = "contents",
	app_cache_ttl = 3600,
	pending_cache_ttl = 300,
	depot_negative_ttl = 300,
	fail_cache_ttl = 300,
	depot_index = false,
	depot_index_ttl = 300,
	depot_index_max_bytes = 12000000,
	max_keys_per_manifest = 100,
	write_depot_comments = false,
	app_hints = {},
}

HubcapResolver.VERSION = "1.0.0"
HubcapResolver.HUBCAP_AUTH_BACKOFF = 600

-- app id -> { keys = {depot -> key}, noKey = {depot -> true}, name = str }
HubcapResolver.AppCache = {}

-- app id -> { at = epoch }
HubcapResolver.FailCache = {}

-- depot id -> { retryAt = epoch }
HubcapResolver.DepotNegativeCache = {}

-- Hubcap credential health
HubcapResolver.Auth = {
	status = "unknown",
	retryAt = 0,
	reason = nil,
}

-- depot id -> app id, learned from fetched Luas and seeded from config
HubcapResolver.DepotToApp = {}

-- The /depot-keys availability hint
HubcapResolver.DepotIndex = {
	at = 0,
	pending = nil,
	loading = false,
}

-- app ids the config knows about (AdditionalApps)
HubcapResolver.KnownApps = {}

-- DecryptionKeys mirror: populated from SLS config on configLoaded,
-- and written back to when new keys are adopted.
HubcapResolver.DecryptionKeys = {}

-- Where the SLSsteam config lives
HubcapResolver.CONFIG_PATH = nil

-- Config path override for spacebunny.conf.lua
HubcapResolver.CONF_PATH = nil

-- =========================================================================
-- Seed DepotToApp from config.yaml comments
-- =========================================================================

HubcapResolver.seedDepotToAppFromConfig = function(pathOverride)
	local home = os.getenv("HOME") or ""
	local path = pathOverride or HubcapResolver.CONFIG_PATH
		or (home .. "/.config/SLSsteam/config.yaml")

	local f = io.open(path, "rb")
	if f == nil then
		return
	end

	local content = f:read("*a")
	f:close()

	local lines = sbSplitLines(content)

	local appsHeader = nil
	for i = 1, #lines do
		if lines[i]:match("^AdditionalApps:%s*$") ~= nil then
			appsHeader = i
			break
		end
	end

	if appsHeader ~= nil then
		for i = appsHeader + 1, #lines do
			local line = lines[i]
			if line == "" then
			elseif line:match("^[ \t]") ~= nil then
				local a = tonumber(line:match("^%s*-%s*(%d+)"))
				if a then
					HubcapResolver.KnownApps[a] = true
				end
			else
				break
			end
		end
	end

	local header = nil
	for i = 1, #lines do
		if lines[i]:match("^AdditionalDepots:%s*$") ~= nil then
			header = i
			break
		end
	end

	if header == nil then
		return
	end

	for i = header + 1, #lines do
		local line = lines[i]
		if line == "" then
		elseif line:match("^[ \t]") ~= nil then
			local depot, rest = line:match("^%s*-%s*(%d+)%s*#(.*)$")
			if depot and rest then
				local app = rest:match("%((%d+)%)%s*$")
				if app then
					local d = tonumber(depot)
					local a = tonumber(app)
					if d and a and a ~= d then
						HubcapResolver.DepotToApp[d] = a
					end
				end
			end
		else
			break
		end
	end
end

-- =========================================================================
-- Config file (~/.config/SLSsteam/spacebunny.conf.lua)
-- =========================================================================

HubcapResolver.loadConfig = function()
	local path = HubcapResolver.CONF_PATH

	if path == nil or path == "" then
		path = os.getenv("SPACE_BUNNY_CONFIG")
	end

	if path == nil or path == "" then
		local home = os.getenv("HOME") or ""
		path = home .. "/.config/SLSsteam/spacebunny.conf.lua"
	end

	local ok, cfg = pcall(dofile, path)

	if not ok then
		sbLog("info", "No spacebunny.conf.lua (" .. path .. "), Hubcap auto-fetch stays off")
		return false
	end

	if type(cfg) ~= "table" or type(cfg.hubcap) ~= "table" then
		sbLog("warn", "spacebunny.conf.lua has no 'hubcap' table, keeping defaults")
		return false
	end

	local h = cfg.hubcap
	local H = HubcapResolver.HUBCAP

	if type(h.enabled) == "boolean" then
		H.enabled = h.enabled
	end

	if type(h.base_url) == "string" and h.base_url:match("^https?://") then
		H.base_url = h.base_url:gsub("/+$", "")
	elseif h.base_url ~= nil then
		sbLog("warn", "Invalid base_url, keeping " .. H.base_url)
	end

	if type(h.api_key) == "string" and h.api_key ~= "" then
		H.api_key = h.api_key
	end

	if type(h.api_key_env) == "string" and h.api_key_env ~= "" then
		local env = os.getenv(h.api_key_env)
		if env ~= nil and env ~= "" then
			H.api_key = env
		end
	end

	if type(h.timeout_sec) == "number" and h.timeout_sec >= 1 and h.timeout_sec <= 30 then
		H.timeout_sec = h.timeout_sec
	end

	if type(h.write_config) == "boolean" then
		H.write_config = h.write_config
	end

	if type(h.notify) == "boolean" then
		H.notify = h.notify
	end

	if h.preflight == "none" or h.preflight == "contents" then
		H.preflight = h.preflight
	end

	if type(h.app_cache_ttl) == "number" and h.app_cache_ttl >= 60 then
		H.app_cache_ttl = h.app_cache_ttl
	end

	if type(h.pending_cache_ttl) == "number" and h.pending_cache_ttl >= 60 then
		H.pending_cache_ttl = h.pending_cache_ttl
	end

	if type(h.fail_cache_ttl) == "number" and h.fail_cache_ttl >= 30 then
		H.fail_cache_ttl = h.fail_cache_ttl
	end

	if type(h.depot_index) == "boolean" then
		H.depot_index = h.depot_index
	end

	if type(h.depot_index_ttl) == "number" and h.depot_index_ttl >= 60 then
		H.depot_index_ttl = h.depot_index_ttl
	end

	if type(h.max_keys_per_manifest) == "number" and h.max_keys_per_manifest >= 1
			and h.max_keys_per_manifest <= 500 then
		H.max_keys_per_manifest = h.max_keys_per_manifest
	end

	if type(h.write_depot_comments) == "boolean" then
		H.write_depot_comments = h.write_depot_comments
	end

	local hints = h.app_hints
	if type(hints) ~= "table" and type(cfg.app_hints) == "table" then
		hints = cfg.app_hints
	end

	if type(hints) == "table" then
		for depot, app in pairs(hints) do
			local d = tonumber(depot)
			local a = tonumber(app)
			if d ~= nil and a ~= nil then
				H.app_hints[d] = a
			end
		end
	end

	if H.enabled and (H.api_key == nil or H.api_key == "") then
		sbLog("warn", "Hubcap auto-fetch is enabled but no api_key is set - it stays off")
		H.enabled = false
	end

	sbLog("info", "Hubcap auto-fetch " .. (H.enabled and "on" or "off")
		.. " (" .. H.base_url .. ", timeout " .. tostring(H.timeout_sec) .. "s)")

	return true
end

-- =========================================================================
-- HTTP
-- =========================================================================

HubcapResolver.request = function(path)
	local H = HubcapResolver.HUBCAP
	local url = H.base_url .. path
	local headers = nil

	local auth = HubcapResolver.Auth

	if auth.status == "blocked" and os.time() < auth.retryAt then
		return nil, "hubcap credentials blocked until " .. tostring(auth.retryAt)
	end

	if H.api_key ~= nil and H.api_key ~= "" then
		headers = { "Authorization: Bearer " .. H.api_key }
	end

	local ok, body = false, nil

	if headers then
		ok, body = pcall(curl.downloadString, url, headers, H.timeout_sec)
	else
		ok, body = pcall(curl.downloadString, url, H.timeout_sec)
	end

	if not ok then
		return nil, "request failed: " .. tostring(body)
	end

	if type(body) ~= "string" or #body == 0 then
		return nil, "empty response"
	end

	local authErr = sbHubcapAuthError(body)

	if authErr ~= nil then
		HubcapResolver.Auth.status = "blocked"
		HubcapResolver.Auth.retryAt = os.time() + HubcapResolver.HUBCAP_AUTH_BACKOFF
		HubcapResolver.Auth.reason = authErr
		sbLog("error", "Hubcap rejected the API key (" .. authErr
			.. "), backing off " .. HubcapResolver.HUBCAP_AUTH_BACKOFF .. "s")
		return nil, "auth: " .. authErr
	end

	if sbLooksLikeHtml(body) then
		return nil, "not a Lua manifest (HTML response)"
	end

	if auth.status == "blocked" then
		auth.status = "ok"
		auth.reason = nil
	end

	return body, nil
end

HubcapResolver.hasBundle = function(appId)
	local body, err = HubcapResolver.request("/manifest/" .. tostring(appId) .. "/contents")

	if body == nil then
		return nil, err
	end

	local count = body:match('"manifest_count"%s*:%s*(%d+)')

	if count == nil then
		return nil, "malformed contents response: no manifest_count"
	end

	if tonumber(count) == 0 then
		return false, "manifest_count 0"
	end

	return true, nil
end

-- =========================================================================
-- Lua manifest parsing
-- =========================================================================

local function sbIsHeaderNoise(comment)
	return comment:match("Lua and Manifest Created by") ~= nil
		or comment:match("^Created:") ~= nil
		or comment:match("^Website:") ~= nil
		or comment:match("^Build:") ~= nil
		or comment:match("^Build Date:") ~= nil
		or comment:match("^Total Depots:") ~= nil
		or comment:match("^Total DLCs:") ~= nil
		or comment:match("^Blacklisted") ~= nil
end

HubcapResolver.parseAppLua = function(body, appId)
	local keys = {}
	local noKey = {}
	local name = nil

	if type(body) ~= "string" then
		return keys, noKey, name
	end

	for line in body:gmatch("[^\r\n]+") do
		local trimmed = line:gsub("^%s+", ""):gsub("%s+$", "")

		if trimmed:sub(1, 2) ~= "--" then
			local depot, key, comment = trimmed:match(
				'addappid%s*%(%s*(%d+)%s*,%s*%d+%s*,%s*["\']([0-9a-fA-F]+)["\']%s*%)%s*%-%-%s*(.*)$')

			if depot == nil then
				depot, key, comment = trimmed:match(
					'addappid%s*%(%s*(%d+)%s*,%s*%d+%s*,%s*["\']([0-9a-fA-F]+)["\']%s*%)')
			end

			if depot == nil then
				depot, comment = trimmed:match(
					'addappid%s*%(%s*(%d+)%s*%)%s*%-%-%s*(.*)$')
			end

			if depot == nil then
				depot = trimmed:match('addappid%s*%(%s*(%d+)%s*%)')
			end

			local d = tonumber(depot)

			if d ~= nil then
				if key ~= nil and #key == 64 then
					keys[d] = key:lower()

					if name == nil and comment ~= nil and comment ~= "" then
						if appId ~= nil and d == tonumber(appId) then
							name = comment
						elseif appId == nil and not comment:match("^Depot %d+$") then
							name = comment
						end
					end
				else
					noKey[d] = true
				end
			end
		end
	end

	if name == nil then
		local first = true
		for line in body:gmatch("[^\r\n]+") do
			local trimmed = line:gsub("^%s+", ""):gsub("%s+$", "")
			local comment = trimmed:match("^%-%-%s*(.*)$")

			if comment ~= nil and comment ~= "" and not sbIsHeaderNoise(comment) then
				name = comment
				break
			end

			first = false
		end
	end

	return keys, noKey, name
end

-- =========================================================================
-- Resolution
-- =========================================================================

HubcapResolver.fetchAppLua = function(appId)
	local H = HubcapResolver.HUBCAP
	local now = os.time()
	local cached = HubcapResolver.AppCache[appId]

	if cached ~= nil and (now - cached.fetchedAt) < (cached.ttl or H.app_cache_ttl) then
		return cached
	end

	local failed = HubcapResolver.FailCache[appId]

	if failed ~= nil and (now - failed.at) < H.fail_cache_ttl then
		return nil
	end

	if HubcapResolver.Auth.status == "blocked"
			and os.time() < HubcapResolver.Auth.retryAt then
		return nil
	end

	if H.preflight == "contents" then
		local has, reason = HubcapResolver.hasBundle(appId)

		if has == false then
			sbLog("info", "Hubcap has no manifest for app " .. tostring(appId)
				.. " (" .. tostring(reason) .. "), not spending a /lua call")
			local empty = {
				keys = {}, noKey = {}, name = nil,
				fetchedAt = now, ttl = H.pending_cache_ttl,
			}
			HubcapResolver.AppCache[appId] = empty
			return empty
		end

		if has == nil and HubcapResolver.Auth.status == "blocked" then
			sbLog("warn", "Hubcap credentials are blocked, not fetching app "
				.. tostring(appId))
			return nil
		end
	end

	local body, err = HubcapResolver.request("/lua/" .. tostring(appId))

	if body == nil then
		sbLog("warn", "Hubcap LUA for app " .. tostring(appId) .. " failed: " .. tostring(err))
		HubcapResolver.FailCache[appId] = { at = now }
		return nil
	end

	local keys, noKey, name = HubcapResolver.parseAppLua(body, appId)

	local ttl = H.app_cache_ttl

	if sbCount(noKey) > 0 and H.pending_cache_ttl < ttl then
		ttl = H.pending_cache_ttl
	end

	local entry = { keys = keys, noKey = noKey, name = name, fetchedAt = now, ttl = ttl }
	HubcapResolver.AppCache[appId] = entry
	HubcapResolver.FailCache[appId] = nil

	for d in pairs(keys) do
		HubcapResolver.DepotToApp[d] = appId
	end

	for d in pairs(noKey) do
		HubcapResolver.DepotToApp[d] = appId
	end

	sbLog("info", "Hubcap LUA for app " .. tostring(appId) .. ": "
		.. sbCount(keys) .. " keyed depots, " .. sbCount(noKey) .. " without a key")

	return entry
end

-- Parse a JSON array of numeric strings by key
local function sbParseIdArray(body, key)
	local inner = body:match('"' .. key .. '"%s*:%s*%[(.-)%]')

	if inner == nil then
		return nil
	end

	local set = {}

	for id in inner:gmatch('"(%-?%d+)"') do
		set[tonumber(id)] = true
	end

	return set
end

HubcapResolver.loadDepotIndex = function()
	local H = HubcapResolver.HUBCAP
	local idx = HubcapResolver.DepotIndex

	if not H.depot_index then
		return false
	end

	local now = os.time()

	if idx.pending ~= nil and (now - idx.at) < H.depot_index_ttl then
		return true
	end

	if idx.loading then
		return idx.pending ~= nil
	end

	if HubcapResolver.Auth.status == "blocked"
			and now < HubcapResolver.Auth.retryAt then
		return false
	end

	idx.loading = true
	local body, err = HubcapResolver.request("/depot-keys")
	idx.loading = false

	if body == nil then
		sbLog("debug", "depot-keys index unavailable: " .. tostring(err))
		return false
	end

	if #body > H.depot_index_max_bytes then
		H.depot_index = false
		sbLog("warn", "depot-keys index is " .. tostring(#body)
			.. " bytes, over the cap; turning the index off")
		return false
	end

	local pending = sbParseIdArray(body, "pending_depot_ids")

	if pending == nil then
		sbLog("debug", "depot-keys index had no pending_depot_ids")
		return false
	end

	idx.pending = pending
	idx.at = now
	sbLog("info", "depot-keys index loaded: "
		.. tostring(sbCount(pending)) .. " pending depots")
	return true
end

HubcapResolver.depotIndexSaysPending = function(depotId)
	if not HubcapResolver.HUBCAP.depot_index then
		return nil
	end

	if not HubcapResolver.loadDepotIndex() then
		return nil
	end

	local idx = HubcapResolver.DepotIndex
	return idx.pending ~= nil and idx.pending[depotId] == true
end

local function sbNoteDepotMissing(depotId)
	HubcapResolver.DepotNegativeCache[depotId] = {
		retryAt = os.time() + (HubcapResolver.HUBCAP.depot_negative_ttl or 300),
	}
end

-- =========================================================================
-- Writing keys into config.yaml
-- =========================================================================

local hffi = require("ffi")
local sbFfiReady = pcall(hffi.cdef, [[
	int open(const char*, int, int);
	long write(int, const void*, unsigned long);
	int ftruncate(int, long);
	int fsync(int);
	int close(int);
]])

local function sbWriteInPlace(path, text)
	local wrote = false

	if sbFfiReady then
		local ok, err = pcall(function()
			local fd = hffi.C.open(path, 2, 384)

			if fd < 0 then
				error("open failed")
			end

			local buf = hffi.new("char[?]", #text + 1, text)
			local written = hffi.C.write(fd, buf, #text)

			if written ~= #text then
				hffi.C.close(fd)
				error("short write")
			end

			hffi.C.ftruncate(fd, #text)
			hffi.C.fsync(fd)
			hffi.C.close(fd)
		end)

		if ok then
			return true, nil
		end

		err = tostring(err)
		sbLog("warn", "In-place write via ffi failed (" .. err .. "), trying io")
	end

	local f = io.open(path, "r+")

	if f == nil then
		return false, "cannot open " .. path
	end

	local ok, err = pcall(function()
		f:seek("set", 0)
		f:write(text)
		f:flush()
	end)

	f:close()

	if not ok then
		return false, tostring(err)
	end

	wrote = true
	return wrote, nil
end

local function sbWriteEntriesToConfig(path, entries)
	local home = os.getenv("HOME") or ""
	path = path or HubcapResolver.CONFIG_PATH
		or (home .. "/.config/SLSsteam/config.yaml")

	local f = io.open(path, "rb")

	if f == nil then
		return 0, 0, "cannot open " .. path
	end

	local content = f:read("*a")
	f:close()

	local lines = sbSplitLines(content)

	local header = nil

	for i = 1, #lines do
		if lines[i]:match("^DecryptionKeys:%s*$") ~= nil then
			header = i
			break
		end
	end

	if header == nil then
		return 0, 0, "no DecryptionKeys block in " .. path
	end

	local last = header

	for i = header + 1, #lines do
		local line = lines[i]

		if line == "" or line:match("^[ \t]") ~= nil then
			last = i
		else
			break
		end
	end

	local existing = {}

	for i = header + 1, last do
		local d = lines[i]:match("^%s*(%d+)%s*:")

		if d ~= nil then
			existing[tonumber(d)] = true
		end
	end

	local newEntries = {}
	local already = 0

	for _, e in ipairs(entries) do
		if existing[e.depot] then
			already = already + 1
		else
			local line = "  " .. tostring(e.depot) .. ": " .. string.lower(e.key)

			if e.comment ~= nil and e.comment ~= "" then
				line = line .. " # " .. e.comment
			end

			newEntries[#newEntries + 1] = line
			existing[e.depot] = true
		end
	end

	if #newEntries == 0 then
		return 0, already, nil
	end

	local out = {}

	for i = 1, last do
		out[#out + 1] = lines[i]
	end

	for _, line in ipairs(newEntries) do
		out[#out + 1] = line
	end

	for i = last + 1, #lines do
		out[#out + 1] = lines[i]
	end

	local updated = table.concat(out, "\n")

	if #updated < #content then
		return 0, already, "refusing to write a shorter config"
	end

	local backupPath = path .. ".spacebunny.bak"
	local bf = io.open(backupPath, "wb")

	if bf ~= nil then
		bf:write(content)
		bf:close()
	end

	local ok, err = sbWriteInPlace(path, updated)

	if not ok then
		return 0, already, "write failed: " .. tostring(err)
	end

	local vf = io.open(path, "rb")

	if vf == nil then
		return 0, already, "cannot re-read " .. path
	end

	local verify = vf:read("*a")
	vf:close()

	local present = {}

	for _, line in ipairs(sbSplitLines(verify)) do
		local d = line:match("^%s*(%d+)%s*:")

		if d ~= nil then
			present[tonumber(d)] = true
		end
	end

	for _, e in ipairs(entries) do
		if not present[e.depot] then
			return 0, already, "the key for depot " .. tostring(e.depot)
				.. " is not in the file after writing"
		end
	end

	return #newEntries, already, nil
end

HubcapResolver.writeKeyToConfig = function(depotId, key, comment, pathOverride)
	if not sbIsHex64(key) then
		return false, "refusing to write a key that is not 64 hex characters"
	end

	local written, already, err = sbWriteEntriesToConfig(pathOverride, {
		{ depot = depotId, key = key, comment = comment },
	})

	if err ~= nil then
		return false, err
	end

	if written == 0 then
		if already > 0 then
			return false, "depot " .. tostring(depotId) .. " is already in the config"
		end

		return false, "nothing to write"
	end

	sbLog("info", "Wrote depot " .. tostring(depotId) .. " into config")
	return true, nil
end

HubcapResolver.writeKeysToConfig = function(entries, pathOverride)
	return sbWriteEntriesToConfig(pathOverride, entries)
end

HubcapResolver.writeDepotComments = function(entries, pathOverride)
	local home = os.getenv("HOME") or ""
	local path = pathOverride or HubcapResolver.CONFIG_PATH
		or (home .. "/.config/SLSsteam/config.yaml")

	local f = io.open(path, "rb")
	if f == nil then
		return false, "cannot open " .. path
	end

	local content = f:read("*a")
	f:close()

	local lines = sbSplitLines(content)

	local header = nil

	for i = 1, #lines do
		if lines[i]:match("^AdditionalDepots:%s*$") ~= nil then
			header = i
			break
		end
	end

	if header == nil then
		return false, "no AdditionalDepots block in " .. path
	end

	local want = {}

	for _, e in ipairs(entries) do
		if e.app ~= nil and e.app ~= e.depot then
			want[e.depot] = e.app
		end
	end

	local changed = 0

	for i = header + 1, #lines do
		local line = lines[i]

		if line == "" then
		elseif line:match("^[ \t]") == nil then
			break
		else
			local depot, rest = line:match("^%s*-%s*(%d+)%s*#(.*)$")

			if depot ~= nil and rest ~= nil then
				local app = want[tonumber(depot)]

				if app ~= nil then
					local text = rest:gsub("%s*%(%d+%)%s*$", "")
					local newLine = "  - " .. depot .. " #" .. text
						.. " (" .. tostring(app) .. ")"

					if newLine ~= line then
						lines[i] = newLine
						changed = changed + 1
					end
				end
			end
		end
	end

	if changed == 0 then
		return true, nil
	end

	local updated = table.concat(lines, "\n")

	if #updated < #content then
		return false, "refusing to write a shorter config"
	end

	local bf = io.open(path .. ".spacebunny.bak", "wb")
	if bf ~= nil then
		bf:write(content)
		bf:close()
	end

	local ok, err = sbWriteInPlace(path, updated)
	if not ok then
		return false, "write failed: " .. tostring(err)
	end

	sbLog("info", "Updated " .. changed .. " depot comment(s) in " .. path)
	return true, nil
end

HubcapResolver.adoptManifestKeys = function(entry, appId, pathOverride)
	if type(entry) ~= "table" or type(entry.keys) ~= "table" then
		return 0
	end

	local ids = {}

	for depotId, key in pairs(entry.keys) do
		if sbIsHex64(key) and HubcapResolver.DecryptionKeys[depotId] == nil then
			ids[#ids + 1] = depotId
		end
	end

	table.sort(ids)

	local cap = HubcapResolver.HUBCAP.max_keys_per_manifest or 100

	if #ids > cap then
		sbLog("warn", "The manifest for app " .. tostring(appId) .. " carries "
			.. #ids .. " new keys, adopting the first " .. tostring(cap))

		for i = #ids, cap + 1, -1 do
			ids[i] = nil
		end
	end

	local added = {}
	local appKeys = 0
	local depotKeys = 0

	for _, depotId in ipairs(ids) do
		local key = entry.keys[depotId]

		HubcapResolver.DecryptionKeys[depotId] = key
		HubcapResolver.DepotToApp[depotId] = appId
		added[#added + 1] = { depot = depotId, key = key, comment = entry.name }

		if depotId == appId then
			appKeys = appKeys + 1
		else
			depotKeys = depotKeys + 1
		end
	end

	local n = #added

	if n == 0 or not HubcapResolver.HUBCAP.write_config then
		return n
	end

	table.sort(added, function(a, b) return a.depot < b.depot end)

	local written, already, err = sbWriteEntriesToConfig(pathOverride, added)

	if err ~= nil then
		sbLog("warn", "Could not write " .. n .. " fetched key(s): " .. tostring(err))
	else
		sbLog("info", "Adopted " .. written .. " key(s) for app " .. tostring(appId)
			.. " (" .. depotKeys .. " depot, " .. appKeys .. " app)"
			.. (already > 0 and (" (" .. already .. " already in the config)") or ""))

		if written > 0 and HubcapResolver.HUBCAP.notify then
			local gameName = entry.name

			if gameName == nil or gameName == "" then
				gameName = "app " .. tostring(appId)
			end

			pcall(log.notify, "[Hubcap] Added " .. written .. " key(s) for "
				.. gameName .. " to the config")
		end
	end

	if HubcapResolver.HUBCAP.write_depot_comments and appId ~= nil then
		local commentEntries = {}

		for _, e in ipairs(added) do
			commentEntries[#commentEntries + 1] = { depot = e.depot, app = appId }
		end

		local ok, cerr = HubcapResolver.writeDepotComments(commentEntries, pathOverride)

		if not ok then
			sbLog("warn", "Could not update the depot comments: " .. tostring(cerr))
		end
	end

	return n
end

-- =========================================================================
-- Main entry point: resolve a depot key
-- =========================================================================

HubcapResolver.resolveDepotKey = function(depotId, appId)
	local H = HubcapResolver.HUBCAP

	if not H.enabled then
		return nil, "Hubcap auto-fetch is off"
	end

	if type(depotId) ~= "number" then
		return nil, "no depot id"
	end

	local neg = HubcapResolver.DepotNegativeCache[depotId]

	if neg ~= nil then
		if os.time() < (neg.retryAt or 0) then
			return nil, "depot " .. tostring(depotId) .. " has no key yet (retry in "
				.. tostring((neg.retryAt or 0) - os.time()) .. "s)"
		end

		HubcapResolver.DepotNegativeCache[depotId] = nil
	end

	-- Already known?
	local configured = HubcapResolver.DecryptionKeys[depotId]

	if configured ~= nil and sbIsHex64(configured) then
		HubcapResolver.DepotNegativeCache[depotId] = nil
		return configured, "config"
	end

	-- In an app we already fetched?
	for app, entry in pairs(HubcapResolver.AppCache) do
		if entry.keys[depotId] ~= nil then
			HubcapResolver.DepotToApp[depotId] = app
			return entry.keys[depotId], "cache"
		end
	end

	-- Which game does the depot belong to?
	local app = tonumber(appId)

	if app == depotId then
		app = nil
	end

	if app == nil then
		app = HubcapResolver.DepotToApp[depotId]
	end

	if app == depotId then
		app = nil
	end

	if app == nil then
		app = H.app_hints[depotId]
	end

	if app == nil then
		local best = nil

		for known in pairs(HubcapResolver.KnownApps) do
			if known < depotId and (depotId - known) < 10 then
				if best == nil or known > best then
					best = known
				end
			end
		end

		if best ~= nil then
			app = best
			sbLog("debug", "Guessed parent app " .. tostring(app)
				.. " for depot " .. tostring(depotId) .. " from the depot number")
		end
	end

	if app == nil then
		return nil, "no app id known for depot " .. tostring(depotId)
	end

	if HubcapResolver.depotIndexSaysPending(depotId) == true then
		sbNoteDepotMissing(depotId)
		return nil, "depot " .. tostring(depotId) .. " is pending at Hubcap"
	end

	local entry = HubcapResolver.fetchAppLua(app)

	if entry == nil then
		return nil, "Hubcap fetch failed for app " .. tostring(app)
	end

	HubcapResolver.DepotToApp[depotId] = app

	HubcapResolver.adoptManifestKeys(entry, app)

	if entry.keys[depotId] ~= nil then
		HubcapResolver.DepotNegativeCache[depotId] = nil
		return entry.keys[depotId], "hubcap"
	end

	if entry.noKey[depotId] then
		sbNoteDepotMissing(depotId)
		return nil, "Hubcap lists depot " .. tostring(depotId)
			.. " for app " .. tostring(app) .. " without a key"
	end

	return nil, "depot " .. tostring(depotId) .. " is not in Hubcap's manifest for app " .. tostring(app)
end

-- =========================================================================
-- Record depot->app mapping (called by SpaceBunny from GetMRC hook)
-- =========================================================================

HubcapResolver.learnDepotApp = function(depotId, appId)
	if depotId ~= nil and appId ~= nil and depotId ~= 0 and appId ~= 0 then
		HubcapResolver.DepotToApp[depotId] = appId
	end
end

-- =========================================================================
-- Sync decryption keys from SLS config into our mirror
-- =========================================================================

HubcapResolver.syncKeys = function(keysTable)
	if type(keysTable) ~= "table" then
		return
	end
	HubcapResolver.DecryptionKeys = keysTable
end

-- =========================================================================
-- Init
-- =========================================================================

HubcapResolver.loadConfig()
HubcapResolver.seedDepotToAppFromConfig()

HubcapResolver.configLoaded = function()
	pcall(function()
		HubcapResolver.seedDepotToAppFromConfig()
	end)
end

SLS.registerCallback("SLSsteam::configLoaded", HubcapResolver.configLoaded)

HubcapResolver.setup = true

sbLog("info", "hubcap.lua " .. HubcapResolver.VERSION .. " loaded!")
