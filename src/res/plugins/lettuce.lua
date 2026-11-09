-- lettuce.lua (0.4)
--
-- 0.4: Lettuce.fallbackFetch / Lettuce.hasFallbackRoute, the one-shot proxy
-- step SpaceBunny 1.8.5 calls after every direct MRC provider failed (proxy
-- if configured, otherwise the worker; never a direct retry). MRC bodies
-- must look like a real code (8-20 digits, not all zeros), so a "404" or
-- "1015" in the body is no longer treated as usable.
--
-- Formerly bypass.lua.  Renamed to Lettuce in 0.3; it still reads a legacy
-- bypass.conf.lua and the mother config's bypass = { } section when the
-- lettuce ones are absent, so existing setups keep working.
--
-- Proxy / ISP-block transport layer for SLSsteam plugins.
--
-- Steam's Lua environment exposes only `curl.downloadString(url, headers,
-- timeout)`: no proxy argument, no SOCKS support. This plugin adds a proxy
-- transport underneath it and can transparently wrap `curl.downloadString`
-- so SpaceBunny (MRC) and Hubcap (keys) work when the ISP blocks the
-- upstream hosts.
--
-- Transports, in order of preference:
--   1. libcurl through FFI (CURLOPT_PROXY). No extra process, does TLS and
--      SOCKS5/HTTP CONNECT itself. Needs libcurl.so.4 to be loadable, which
--      every normal desktop distro has.
--   2. The `curl` command line via io.popen. Used when libcurl cannot be
--      loaded. One short-lived process per request.
--   3. Direct, using the original curl.downloadString.
--   4. The Cloudflare worker fallback. It is not a forward proxy; it rewrites
--      manifestdex/wudrm/Hubcap URLs to the worker's /manifest, /rescue and
--      /hubcap routes when the direct or proxy body is unusable.
--
-- Supported proxy schemes: socks5, socks5h, socks4, socks4a, http, https.
-- NOT supported, and rejected with an explanation: vless, vmess, trojan, ss,
-- hysteria, tuic. Those need a real proxy client that speaks the protocol and
-- terminates TLS/WebSocket; a Lua plugin cannot do that.
--
-- Config: the lettuce = { } section of ~/.config/SLSsteam/katamari.conf.lua,
-- or ~/.config/SLSsteam/lettuce.conf.lua for overrides. Minimal working file:
--
--   return {
--       proxy = "socks5h://user:pass@host:1080",
--   }
--
-- Or paste a subscription link; entries are parsed and the first working
-- socks5/http endpoint is used:
--
--   return {
--       subscription = "https://provider/sub/KEY",
--   }

Lettuce = Lettuce or {}

Lettuce.VERSION = "0.4"

KATAMARI = KATAMARI or { plugins = {} }
KATAMARI.plugins.lettuce = {
	version = Lettuce.VERSION,
	role = "transport",
	priority = 20,
}

Lettuce.DEBUG = false

-- SLSsteam re-runs every plugin on any plugins/ change.  A second pass would
-- register the luaReload callback again and stack reloads.  The first load
-- registers everything; the guard makes re-entry a no-op.
if Lettuce.setup then
	return
end

-- =========================================================================
-- Config
-- =========================================================================

local DEFAULT_CONFIG = {
	enabled = true,
	-- "auto" | "libcurl" | "curl" | "direct"
	transport = "auto",
	-- "socks5h://user:pass@host:port" or a list of them
	proxy = nil,
	-- subscription link returning base64 or plain proxy URIs
	subscription = nil,
	-- wrap curl.downloadString for the hosts below
	intercept = true,
	-- fall back to a direct request when the proxy fails
	direct_fallback = true,
	-- empty table means "every host"
	hosts = {
		"manifest.manifestdex.com",
		"gmrc.wudrm.com",
		"manifest.opensteamtool.com",
		"hubcapmanifest.com",
	},
	-- Cloudflare worker as a last-resort transport for known hosts.
	worker_fallback = true,
	worker_base = "https://rapid-thunder-fba1wirecutter.7ucking.workers.dev",
	worker_token = "",
	worker_token_header = "X-Katamari-Token",
	timeout = 10,
	connect_timeout = 6,
	probe_url = "https://manifest.manifestdex.com/900001",
	probe_timeout = 5,
	max_probe = 4,
	-- subscription re-read interval, and how long a failed proxy is skipped
	subscription_refresh_sec = 3600,
	cooldown_sec = 120,
}

local function trim(s)
	return (tostring(s):gsub("^%s+", ""):gsub("%s+$", ""))
end

local function safeLog(level, msg)
	pcall(function()
		if type(log) == "table" and type(log[level]) == "function" then
			log[level]("[Lettuce] " .. tostring(msg))
		else
			print("[Lettuce] " .. tostring(msg))
		end
	end)
end

Lettuce.safeLog = safeLog

-- Never write proxy credentials to the SLSsteam log or status output.
local function redactProxy(value)
	if type(value) ~= "string" then
		return value
	end

	return (value:gsub("://[^/@]+@", "://***@"))
end

Lettuce.redactProxy = redactProxy

local configPath = nil

local function configCandidates()
	local home = os.getenv("HOME") or ""
	local configHome = os.getenv("XDG_CONFIG_HOME")

	if configHome == nil or configHome == "" then
		configHome = home .. "/.config"
	end

	local candidates = {
		configHome .. "/SLSsteam/lettuce.conf.lua",
	}

	-- Also accept the conf next to the plugin, so copying both into
	-- plugins/ works without touching the SLSsteam config directory.
	local source = debug.getinfo(1, "S").source

	if source:sub(1, 1) == "@" then
		local dir = source:sub(2):match("^(.*)[/\\]")
		if dir ~= nil then
			candidates[#candidates + 1] = dir .. "/lettuce.conf.lua"
			candidates[#candidates + 1] = dir .. "/../lettuce.conf.lua"
		end
	end

	-- Legacy name from before the rename (0.2 and older).
	candidates[#candidates + 1] = configHome .. "/SLSsteam/bypass.conf.lua"

	return candidates
end

local function resolveConfigPath()
	local candidates = configCandidates()

	for _, candidate in ipairs(candidates) do
		local handle = io.open(candidate, "r")
		if handle ~= nil then
			handle:close()
			configPath = candidate
			return candidate
		end
	end

	configPath = candidates[1]
	return configPath
end

local function clampNumber(value, fallback, minimum, maximum)
	local number = tonumber(value)

	if number == nil then
		return fallback
	end

	if minimum ~= nil and number < minimum then
		number = minimum
	end

	if maximum ~= nil and number > maximum then
		number = maximum
	end

	return number
end

local function loadConfig()
	local path = resolveConfigPath()
	-- A missing plugin conf is normal: the mother config and the defaults
	-- still apply (0.2 returned the defaults here and skipped the mother).
	local handle = io.open(path, "r")
	local has_own_conf = handle ~= nil
	if handle ~= nil then
		handle:close()
	end

	local cfg = {}
	for k, v in pairs(DEFAULT_CONFIG) do
		cfg[k] = v
	end

	-- Mother config: shared proxy.* values, then lettuce.* overrides, then the
	-- standalone lettuce.conf.lua.  proxy.worker_url/token are for Bakeneko and
	-- are deliberately not copied here.
	local home = os.getenv("HOME") or ""
	local cfg_home = os.getenv("XDG_CONFIG_HOME")
	if cfg_home == nil or cfg_home == "" then
		cfg_home = home .. "/.config"
	end

	local mother_ok, mother = pcall(dofile, cfg_home .. "/SLSsteam/katamari.conf.lua")
	if mother_ok and type(mother) == "table" then
		local shared = mother.proxy
		if type(shared) == "table" then
			for _, key in ipairs({
				"enabled", "proxy", "subscription", "transport", "intercept",
				"direct_fallback", "hosts", "timeout", "connect_timeout",
				"probe_url", "probe_timeout", "max_probe",
				"subscription_refresh_sec", "worker_fallback",
				"worker_base", "worker_token", "worker_token_header",
			}) do
				if shared[key] ~= nil then
					cfg[key] = shared[key]
				end
			end
			if shared.proxy_cooldown_sec ~= nil then
				cfg.cooldown_sec = shared.proxy_cooldown_sec
			end
		end

		-- lettuce = { } wins; bypass = { } is the pre-rename section name.
		local section = mother.lettuce
		if type(section) ~= "table" then
			section = mother.bypass
		end
		if type(section) == "table" then
			for k, v in pairs(section) do
				cfg[k] = v
			end
		end
	end

	local ok, user = true, nil
	if has_own_conf then
		ok, user = pcall(dofile, path)
	end

	if not has_own_conf then
		-- nothing to merge
	elseif ok and type(user) == "table" then
		for k, v in pairs(user) do
			cfg[k] = v
		end
	elseif ok then
		safeLog("warn", "lettuce.conf.lua must return a table, using defaults")
	else
		safeLog("warn", "lettuce.conf.lua failed to load: " .. tostring(user))
	end

	if type(cfg.hosts) ~= "table" then
		cfg.hosts = DEFAULT_CONFIG.hosts
	else
		local hosts = {}
		for _, host in ipairs(cfg.hosts) do
			if type(host) == "string" and host ~= "" then
				hosts[#hosts + 1] = host
			end
		end
		cfg.hosts = hosts
	end

	for _, key in ipairs({ "enabled", "intercept", "direct_fallback" }) do
		if type(cfg[key]) ~= "boolean" then
			cfg[key] = DEFAULT_CONFIG[key]
		end
	end

	if cfg.transport ~= "auto" and cfg.transport ~= "libcurl"
			and cfg.transport ~= "curl" and cfg.transport ~= "direct" then
		safeLog("warn", "Invalid transport '" .. tostring(cfg.transport)
			.. "', using auto")
		cfg.transport = "auto"
	end

	cfg.timeout = clampNumber(cfg.timeout, DEFAULT_CONFIG.timeout, 1, 300)
	cfg.connect_timeout = clampNumber(cfg.connect_timeout,
		DEFAULT_CONFIG.connect_timeout, 1, 120)
	cfg.probe_timeout = clampNumber(cfg.probe_timeout,
		DEFAULT_CONFIG.probe_timeout, 1, 60)
	cfg.max_probe = clampNumber(cfg.max_probe, DEFAULT_CONFIG.max_probe, 1, 32)
	cfg.subscription_refresh_sec = clampNumber(cfg.subscription_refresh_sec,
		DEFAULT_CONFIG.subscription_refresh_sec, 60, 604800)
	cfg.cooldown_sec = clampNumber(cfg.cooldown_sec,
		DEFAULT_CONFIG.cooldown_sec, 5, 86400)

	if type(cfg.proxy) == "string" and cfg.proxy == "" then
		cfg.proxy = nil
	end

	if cfg.proxy ~= nil and type(cfg.proxy) ~= "string" then
		safeLog("warn", "proxy must be a string, ignoring")
		cfg.proxy = nil
	end

	if cfg.subscription ~= nil and type(cfg.subscription) ~= "string" then
		safeLog("warn", "subscription must be a string, ignoring")
		cfg.subscription = nil
	end

	Lettuce.config = cfg

	return cfg
end

-- =========================================================================
-- Small helpers
-- =========================================================================

local function hostOf(url)
	local host = tostring(url):match("^%a[%w+.-]*://([^/?#]+)")

	if host == nil then
		return nil
	end

	host = host:gsub("^.*@", "")     -- drop userinfo
	host = host:match("^%[([^%]]+)%]") or host:match("^([^:]+)") or host

	return host:lower()
end

local function isHttpUrl(url)
	return type(url) == "string"
		and (url:match("^https?://") ~= nil)
end

-- Reject anything that is not a plain proxy URL. This is defence in depth:
-- every transport below also quotes its arguments, but a proxy string that
-- can smuggle shell metacharacters should never reach that point.
local function isProxyUrl(url)
	if type(url) ~= "string" or url == "" then
		return false
	end

	if url:find("[%s'\"`$\\;|&<>]") then
		return false
	end

	if url:match("^%a[%w+.-]*://") == nil then
		return false
	end

	local scheme = url:match("^(%a[%w+.-]*)://"):lower()

	return scheme == "socks5" or scheme == "socks5h" or scheme == "socks4"
		or scheme == "socks4a" or scheme == "http" or scheme == "https"
end

local function hostAllowed(host)
	local cfg = Lettuce.config

	if host == nil then
		return false
	end

	if type(cfg.hosts) ~= "table" or #cfg.hosts == 0 then
		return true
	end

	for _, entry in ipairs(cfg.hosts) do
		local want = tostring(entry):lower()

		if host == want or host:sub(-(#want + 1)) == "." .. want then
			return true
		end
	end

	return false
end

local function base64Decode(s)
	local alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	local map = {}

	for i = 1, #alphabet do
		map[alphabet:sub(i, i)] = i - 1
	end

	local clean = tostring(s):gsub("[^%w%+/=]", "")

	if clean == "" then
		return nil
	end

	clean = clean:gsub("=+$", "")

	local out = {}
	local buffer, bits = 0, 0

	for i = 1, #clean do
		local value = map[clean:sub(i, i)]

		if value == nil then
			return nil
		end

		buffer = buffer * 64 + value
		bits = bits + 6

		if bits >= 8 then
			bits = bits - 8
			local byte = math.floor(buffer / (2 ^ bits)) % 256

			out[#out + 1] = string.char(byte)
			buffer = buffer % (2 ^ bits)
		end
	end

	return table.concat(out)
end

-- =========================================================================
-- Transports
-- =========================================================================

local libcurl = nil
local libcurlChecked = false
local libcurlCdefs = false

local CURLOPT = {
	URL = 10002,
	PROXY = 10004,
	TIMEOUT = 13,
	NOPROGRESS = 43,
	MAXREDIRS = 68,
	FOLLOWLOCATION = 52,
	CONNECTTIMEOUT = 78,
	NOSIGNAL = 99,
	WRITEDATA = 10001,
	ERRORBUFFER = 10010,
	HTTPHEADER = 10023,
	USERAGENT = 10018,
	WRITEFUNCTION = 20011,
	PROTOCOLS = 181,
	REDIR_PROTOCOLS = 182,
	INFO_RESPONSE_CODE = 0x200002,
}

local function loadLibcurl()
	if libcurlChecked then
		return libcurl
	end

	libcurlChecked = true

	local ok, ffi = pcall(require, "ffi")

	if not ok then
		return nil
	end

	if not libcurlCdefs then
		local defs = [[
			typedef void CURL;
			typedef int CURLcode;
			typedef size_t (*lettuce_write_cb)(char*, size_t, size_t, void*);
			struct curl_slist { char* data; struct curl_slist* next; };
			CURL* curl_easy_init(void);
			CURLcode curl_easy_setopt(CURL*, int, ...);
			CURLcode curl_easy_perform(CURL*);
			void curl_easy_cleanup(CURL*);
			const char* curl_easy_strerror(CURLcode);
			CURLcode curl_easy_getinfo(CURL*, int, ...);
			struct curl_slist* curl_slist_append(struct curl_slist*, const char*);
			void curl_slist_free_all(struct curl_slist*);
		]]

		if not pcall(ffi.cdef, defs) then
			safeLog("warn", "libcurl FFI declarations failed, using the curl CLI transport")
			return nil
		end

		libcurlCdefs = true
	end

	for _, name in ipairs({ "curl", "libcurl.so.4", "libcurl.so" }) do
		local loaded, lib = pcall(ffi.load, name)

		if loaded and lib ~= nil then
			libcurl = lib
			safeLog("debug", "libcurl transport available (" .. name .. ")")

			return lib
		end
	end

	safeLog("debug", "libcurl could not be loaded, using the curl CLI transport")

	return nil
end

-- Returns the libcurl library to use, or nil for the curl CLI transport.
local function useLibcurl()
	local mode = Lettuce.config and Lettuce.config.transport

	if mode == "curl" or mode == "direct" then
		return nil
	end

	return loadLibcurl()
end

local function libcurlRequest(lib, url, headers, timeout, proxy)
	local ffi = require("ffi")
	local handle = lib.curl_easy_init()

	if handle == nil then
		return nil, "curl_easy_init failed"
	end

	local chunks = {}
	local writeCallback = ffi.cast("lettuce_write_cb",
		function(ptr, size, nmemb, userdata)
			local n = tonumber(size) * tonumber(nmemb)

			if n > 0 then
				chunks[#chunks + 1] = ffi.string(ptr, n)
			end

			return n
		end)

	local errorBuffer = ffi.new("char[256]")
	local responseCode = ffi.new("long[1]")

	local function setopt(option, value)
		lib.curl_easy_setopt(handle, option, value)
	end

	setopt(CURLOPT.URL, url)
	setopt(CURLOPT.WRITEFUNCTION, writeCallback)
	setopt(CURLOPT.PROTOCOLS, ffi.cast("long", 3))            -- http, https
	setopt(CURLOPT.REDIR_PROTOCOLS, ffi.cast("long", 3))
	setopt(CURLOPT.FOLLOWLOCATION, ffi.cast("long", 1))
	setopt(CURLOPT.MAXREDIRS, ffi.cast("long", 3))
	setopt(CURLOPT.NOSIGNAL, ffi.cast("long", 1))
	setopt(CURLOPT.TIMEOUT, ffi.cast("long", math.max(1, math.floor(timeout))))
	setopt(CURLOPT.CONNECTTIMEOUT,
		ffi.cast("long", math.max(1, math.floor(Lettuce.config.connect_timeout))))
	setopt(CURLOPT.ERRORBUFFER, errorBuffer)
	setopt(CURLOPT.USERAGENT, "SLSsteam-Lettuce/" .. Lettuce.VERSION)

	local slist = nil

	if type(headers) == "table" and #headers > 0 then
		for _, header in ipairs(headers) do
			if type(header) == "string" then
				slist = lib.curl_slist_append(slist, header)
			end
		end

		if slist ~= nil then
			setopt(CURLOPT.HTTPHEADER, slist)
		end
	end

	if proxy ~= nil then
		setopt(CURLOPT.PROXY, proxy)
	end

	local rc = lib.curl_easy_perform(handle)

	lib.curl_easy_getinfo(handle, CURLOPT.INFO_RESPONSE_CODE, responseCode)

	if slist ~= nil then
		lib.curl_slist_free_all(slist)
	end

	lib.curl_easy_cleanup(handle)

	if rc ~= 0 then
		local message = ffi.string(errorBuffer)

		if message == "" then
			message = ffi.string(lib.curl_easy_strerror(rc))
		end

		return nil, "curl (" .. rc .. "): " .. message
	end

	return table.concat(chunks), nil, tonumber(responseCode[0])
end

local function shellQuote(s)
	return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

local function isShellSafeUrl(url)
	return type(url) == "string"
		and url:match("^https?://") ~= nil
		and url:find("['\"`$\\;|&<>%s]") == nil
end

local function cliRequest(url, headers, timeout, proxy)
	if type(io) ~= "table" or type(io.popen) ~= "function" then
		return nil, "io.popen is unavailable"
	end

	if not isShellSafeUrl(url) then
		return nil, "unsafe url for the curl CLI transport"
	end

	local parts = {
		"curl", "-sS",
		"--max-time", tostring(math.max(1, math.floor(timeout))),
		"--connect-timeout",
		tostring(math.max(1, math.floor(Lettuce.config.connect_timeout))),
	}

	if proxy ~= nil then
		parts[#parts + 1] = "--proxy"
		parts[#parts + 1] = shellQuote(proxy)
	end

	if type(headers) == "table" then
		for _, header in ipairs(headers) do
			-- A header value legitimately contains spaces, so only reject
			-- characters that could break out of the shell quoting.
			if type(header) == "string"
					and not header:find("['\"`$\\;|&<>]") then
				parts[#parts + 1] = "-H"
				parts[#parts + 1] = shellQuote(header)
			end
		end
	end

	parts[#parts + 1] = shellQuote(url)

	local pipe = io.popen(table.concat(parts, " ") .. " 2>/dev/null")

	if pipe == nil then
		return nil, "io.popen failed"
	end

	local body = pipe:read("*a") or ""
	pipe:close()

	if body == "" then
		return nil, "curl CLI returned nothing"
	end

	return body, nil, 200
end

-- The unmodified SLSsteam downloadString, used for direct requests and as the
-- final fallback. Kept in a global so a plugin reload does not wrap our own
-- wrapper.
local function originalDownloadString()
	if _G.__LETTUCE_ORIGINAL_DOWNLOAD__ == nil then
		-- bypass.lua (pre-rename) may already have wrapped curl in this Lua
		-- state; its saved original is the real one.
		if type(_G.__BYPASS_ORIGINAL_DOWNLOAD__) == "function" then
			_G.__LETTUCE_ORIGINAL_DOWNLOAD__ = _G.__BYPASS_ORIGINAL_DOWNLOAD__
		elseif type(curl) == "table" and type(curl.downloadString) == "function" then
			_G.__LETTUCE_ORIGINAL_DOWNLOAD__ = curl.downloadString
		end
	end

	return _G.__LETTUCE_ORIGINAL_DOWNLOAD__
end

-- Call the untouched downloadString with the arity it was given, so the
-- `(url, timeout)` form keeps working.
-- Pass a request to the untouched function and return its result verbatim,
-- exceptions included, for the non-intercepted path.
local function passthrough(url, headers, timeout)
	local original = originalDownloadString()

	if original == nil then
		return nil
	end

	if type(headers) == "table" then
		return original(url, headers, timeout or Lettuce.config.timeout)
	end

	return original(url, timeout or Lettuce.config.timeout)
end

local function callOriginal(url, headers, timeout)
	local original = originalDownloadString()

	if original == nil then
		return nil, "curl.downloadString is unavailable"
	end

	local ok, body

	if type(headers) == "table" then
		ok, body = pcall(original, url, headers,
			timeout or Lettuce.config.timeout)
	else
		ok, body = pcall(original, url, timeout or Lettuce.config.timeout)
	end

	if not ok then
		return nil, tostring(body)
	end

	if type(body) ~= "string" then
		return nil, "downloadString returned a non-string"
	end

	return body
end

local function directRequest(url, headers, timeout)
	return callOriginal(url, headers, timeout)
end

-- =========================================================================
-- Proxy resolution
-- =========================================================================

Lettuce.state = Lettuce.state or {
	proxy = nil,
	candidates = nil,
	candidateIndex = 1,
	downUntil = 0,
	subscriptionAt = 0,
	lastError = nil,
}

local function nowSec()
	if Lettuce.now ~= nil then
		return Lettuce.now()
	end

	return os.time()
end

-- Test one proxy by asking a real endpoint for a manifest request code. An
-- MRC is 1-20 digits, so this also proves the proxy reaches the target.
local function proxyWorks(proxy)
	local cfg = Lettuce.config
	local body, err

	local lib = useLibcurl()

	if lib ~= nil then
		body, err = libcurlRequest(lib, cfg.probe_url,
			{ "User-Agent: ManifestDeX/1.0" }, cfg.probe_timeout, proxy)
	else
		body, err = cliRequest(cfg.probe_url,
			{ "User-Agent: ManifestDeX/1.0" }, cfg.probe_timeout, proxy)
	end

	if body == nil then
		Lettuce.state.lastError = err

		return false
	end

	local code = trim(body)

	if not code:match("^%d+$") then
		Lettuce.state.lastError = "probe returned a non-MRC body"

		return false
	end

	return true
end

local function parseSubscription(body)
	local text = tostring(body)
	local candidates = {}
	local counts = {}

	if not text:find("://", 1, true) then
		local decoded = base64Decode(text)

		if decoded ~= nil and decoded:find("://", 1, true) then
			text = decoded
		end
	end

	for rawLine in text:gmatch("[^\r\n]+") do
		local line = trim(rawLine):gsub("#.*$", "")

		if line ~= "" then
			local scheme = line:match("^([%a][%w+.-]*)://")

			if scheme ~= nil then
				counts[scheme:lower()] = (counts[scheme:lower()] or 0) + 1
			end

			if isProxyUrl(line) then
				candidates[#candidates + 1] = line
			elseif line:match("^[%w%.%-]+:%d+:[^:]+:[^:]+$") then
				-- host:port:user:pass, the usual shape of a purchased list
				local host, port, user, pass =
					line:match("^([%w%.%-]+):(%d+):([^:]+):([^:]+)$")

				candidates[#candidates + 1] = string.format(
					"socks5h://%s:%s@%s:%s", user, pass, host, port)
			end
		end
	end

	return candidates, counts
end

local function reportUnsupported(counts)
	local unsupported = {}

	for scheme, n in pairs(counts) do
		local known = scheme == "socks5" or scheme == "socks5h"
			or scheme == "socks4" or scheme == "socks4a"
			or scheme == "http" or scheme == "https"

		if not known then
			unsupported[#unsupported + 1] = tostring(n) .. " " .. scheme
		end
	end

	if #unsupported > 0 then
		table.sort(unsupported)

		safeLog("warn", "Subscription has no socks5/http endpoint (only "
			.. table.concat(unsupported, ", ")
			.. "). Those protocols need a real proxy client; ask the provider "
			.. "for a socks5 or HTTP proxy URL.")
	end
end

local function selectFromSubscription()
	local cfg = Lettuce.config
	local body, err = directRequest(cfg.subscription, nil, cfg.timeout)

	if body == nil then
		safeLog("warn", "Could not download the subscription directly ("
			.. tostring(err) .. "); set `proxy` explicitly if the "
			.. "subscription host is blocked too")

		return nil
	end

	local candidates, counts = parseSubscription(body)

	Lettuce.state.subscriptionAt = nowSec()

	if #candidates == 0 then
		reportUnsupported(counts)
		safeLog("warn", "Subscription yielded no usable socks5/http proxy")

		return nil
	end

	local limit = math.min(#candidates, math.max(1, tonumber(cfg.max_probe) or 4))

	safeLog("info", "Subscription yielded " .. #candidates
		.. " candidate(s), probing up to " .. limit)

	for i = 1, limit do
		if proxyWorks(candidates[i]) then
			Lettuce.state.candidates = candidates
			Lettuce.state.candidateIndex = i
			safeLog("info", "Using subscription entry " .. i .. "/" .. #candidates)

			return candidates[i]
		end
	end

	safeLog("warn", "Every probed subscription entry failed: "
		.. tostring(Lettuce.state.lastError))

	return nil
end

-- Returns a usable proxy string, or nil when direct requests should be used.
Lettuce.resolveProxy = function()
	local cfg = Lettuce.config
	local state = Lettuce.state

	if not cfg.enabled then
		return nil
	end

	if nowSec() < state.downUntil then
		return nil
	end

	if state.proxy ~= nil and state.proxy ~= "" then
		return state.proxy
	end

	local configured = cfg.proxy

	if type(configured) == "table" then
		for _, candidate in ipairs(configured) do
			if proxyWorks(candidate) then
				state.proxy = candidate

				return candidate
			end
		end

		safeLog("warn", "None of the configured proxies answered")
		state.downUntil = nowSec() + (cfg.cooldown_sec or 120)

		return nil
	end

	if type(configured) == "string" and configured ~= "" then
		if not isProxyUrl(configured) then
			safeLog("error", "Rejected malformed proxy URL: " .. redactProxy(configured))

			return nil
		end

		state.proxy = configured

		return configured
	end

	if type(cfg.subscription) == "string" and cfg.subscription ~= "" then
		local fresh = state.subscriptionAt > 0
			and (nowSec() - state.subscriptionAt) < (cfg.subscription_refresh_sec or 3600)

		if state.candidates == nil or not fresh then
			local chosen = selectFromSubscription()

			if chosen ~= nil then
				state.proxy = chosen

				return chosen
			end
		else
			state.candidateIndex = math.max(1, state.candidateIndex)

			return state.candidates[state.candidateIndex]
		end
	end

	state.downUntil = nowSec() + (cfg.cooldown_sec or 120)

	return nil
end

-- =========================================================================
-- Public fetch
-- =========================================================================

Lettuce.request = function(url, headers, timeout, proxy)
	local cfg = Lettuce.config

	if not isHttpUrl(url) then
		return nil, "not an http(s) url"
	end

	if cfg.transport == "direct" then
		local body, err = directRequest(url, headers, timeout or cfg.timeout)

		return body, err, body ~= nil and 200 or nil
	end

	local lib = useLibcurl()

	if lib ~= nil then
		return libcurlRequest(lib, url, headers,
			timeout or cfg.timeout, proxy)
	end

	return cliRequest(url, headers, timeout or cfg.timeout, proxy)
end

-- A failed proxy is not just retried: the next subscription candidate is
-- promoted, and only when the whole list is exhausted does the cooldown
-- start. Without this, one dead entry would poison the cached choice.
local function markProxyFailed(err)
	local state = Lettuce.state
	local cfg = Lettuce.config

	state.lastError = err
	state.proxy = nil

	if state.candidates ~= nil and #state.candidates > 0 then
		state.candidateIndex = state.candidateIndex + 1

		if state.candidateIndex <= #state.candidates then
			state.downUntil = 0
			safeLog("info", "Rotating to subscription entry "
				.. state.candidateIndex .. "/" .. #state.candidates)

			return
		end

		state.candidateIndex = 1
		state.subscriptionAt = 0
	end

	state.downUntil = nowSec() + (cfg.cooldown_sec or 120)

	safeLog("warn", "Proxy failed (" .. tostring(redactProxy(err)) .. "); direct for "
		.. tostring(cfg.cooldown_sec or 120) .. "s")
end

local function workerTarget(url)
	local cfg = Lettuce.config
	local base = trim(cfg.worker_base or "")

	if base == "" then
		return nil
	end
	base = base:gsub("/+$", "")

	local path, query = tostring(url):match("^([^?]+)(.*)$")
	local host = hostOf(url)

	if path == nil or host == nil then
		return nil
	end

	if host == "manifest.manifestdex.com" then
		local id = path:match("/(%d+)$")
		return id and (base .. "/manifest/" .. id) or nil
	end

	if host == "gmrc.wudrm.com" then
		local id = path:match("/manifest/(%d+)")
		return id and (base .. "/rescue/" .. id) or nil
	end

	if host == "hubcapmanifest.com" then
		local rest = path:match("/api/v1(.*)$")
		return rest and (base .. "/hubcap" .. rest .. (query or "")) or nil
	end

	return nil
end

local function bodyIsUsable(url, body)
	if type(body) ~= "string" or body == "" then
		return false
	end

	local host = hostOf(url)

	if host == "manifest.manifestdex.com"
			or host == "gmrc.wudrm.com"
			or host == "manifest.opensteamtool.com" then
		-- A real manifest request code: 8-20 digits, not all zeros. Shorter
		-- numbers are status/error codes a provider put in the body.
		local digits = body:match("^%s*\"?(%d+)\"?%s*$")
		if digits == nil then
			return false
		end
		digits = digits:gsub("^0+", "")
		return #digits >= 8 and #digits <= 20
	end

	if host == "hubcapmanifest.com" then
		local head = body:sub(1, 200):lower()
		return not (head:find("<!doctype", 1, true)
			or head:find("<html", 1, true)
			or head:find("attention required", 1, true))
	end

	return true
end

local function workerRequest(url, headers, timeout)
	local cfg = Lettuce.config

	if not cfg.worker_fallback then
		return nil, "worker fallback disabled"
	end

	local target = workerTarget(url)

	if target == nil then
		return nil, "no worker mapping"
	end

	local workerHeaders = {}

	if type(headers) == "table" then
		for i, value in ipairs(headers) do
			workerHeaders[i] = value
		end
	end

	if type(cfg.worker_token) == "string" and cfg.worker_token ~= "" then
		workerHeaders[#workerHeaders + 1] =
			tostring(cfg.worker_token_header or "X-Katamari-Token")
			.. ": " .. cfg.worker_token
	end

	local body, err = directRequest(target, workerHeaders,
		timeout or cfg.timeout)

	if not bodyIsUsable(url, body) then
		safeLog("warn", "Worker fallback failed for " .. tostring(hostOf(url)))
		return nil, err or "worker returned an unusable body"
	end

	safeLog("info", "Worker fallback succeeded for " .. tostring(hostOf(url)))
	return body, nil, 200
end

local function acceptOrWorker(url, headers, timeout, body, err, status)
	if bodyIsUsable(url, body) then
		return body, err, status
	end

	if Lettuce.config.worker_fallback then
		local workerBody, workerErr, workerStatus =
			workerRequest(url, headers, timeout)

		if workerBody ~= nil then
			return workerBody, nil, workerStatus
		end

		err = workerErr or err
	end

	return body, err, status
end

-- Fetch through the proxy when one is available and the host is allowed.
-- On success returns body, nil, status. On failure returns nil, reason.
Lettuce.fetch = function(url, headers, timeout)
	local cfg = Lettuce.config

	if not cfg.enabled or not hostAllowed(hostOf(url)) then
		local body, err, status = directRequest(url, headers,
			timeout or cfg.timeout)

		return acceptOrWorker(url, headers, timeout, body, err, status)
	end

	local proxy = Lettuce.resolveProxy()

	if proxy == nil then
		local body, err, status = directRequest(url, headers,
			timeout or cfg.timeout)

		return acceptOrWorker(url, headers, timeout, body, err, status)
	end

	local body, err, status = Lettuce.request(url, headers, timeout, proxy)

	if bodyIsUsable(url, body) then
		return body, nil, status
	end

	markProxyFailed(err or "unusable proxy body")

	if cfg.direct_fallback then
		local directBody, directErr, directStatus = directRequest(url, headers,
			timeout or cfg.timeout)

		return acceptOrWorker(url, headers, timeout, directBody, directErr,
			directStatus)
	end

	local workerBody, workerErr, workerStatus =
		workerRequest(url, headers, timeout)

	if workerBody ~= nil then
		return workerBody, nil, workerStatus
	end

	return body, workerErr or err, status
end

-- =========================================================================
-- One-shot fallback (SpaceBunny's proxy step)
-- =========================================================================

local function proxyConfigured(cfg)
	local function set(v)
		if type(v) == "string" then
			return trim(v) ~= ""
		end
		return type(v) == "table" and #v > 0
	end

	return set(cfg.proxy) or set(cfg.subscription)
end

-- True when there is somewhere to send a fallback request: a configured
-- proxy or subscription, or the worker. Reads config only (no network).
Lettuce.hasFallbackRoute = function()
	local cfg = Lettuce.config

	if cfg == nil or not cfg.enabled then
		return false
	end

	if proxyConfigured(cfg) then
		return true
	end

	return cfg.worker_fallback == true and trim(cfg.worker_base or "") ~= ""
end

-- One attempt, for a caller that already tried the host directly: through
-- the proxy when one is configured and not cooling down, otherwise through
-- the worker. Never a direct request, never a second route. Returns
-- body, nil on success and nil, reason otherwise.
Lettuce.fallbackFetch = function(url, headers, timeout)
	local cfg = Lettuce.config

	if cfg == nil or not cfg.enabled then
		return nil, "lettuce disabled"
	end

	if proxyConfigured(cfg) then
		local proxy = Lettuce.resolveProxy()

		if proxy ~= nil then
			local body, err = Lettuce.request(url, headers, timeout, proxy)

			if bodyIsUsable(url, body) then
				return body, nil
			end

			markProxyFailed(err or "unusable proxy body")

			return nil, "proxy: " .. tostring(err or "unusable body")
		end
	end

	local body, err = workerRequest(url, headers, timeout)

	if body ~= nil then
		return body, nil
	end

	return nil, "worker: " .. tostring(err or "no answer")
end

-- =========================================================================
-- curl.downloadString interception
-- =========================================================================

local function currentDownloadString()
	return curl ~= nil and type(curl.downloadString) == "function"
end

Lettuce.install = function()
	local cfg = Lettuce.config

	if not cfg.intercept or not currentDownloadString() then
		return false
	end

	local original = originalDownloadString()

	if original == nil then
		return false
	end

	if curl.downloadString == Lettuce._wrapper then
		return true
	end

	local wrapper = function(url, a, b)
		local headers = nil
		local timeout = nil

		if type(a) == "table" then
			headers = a
			timeout = b
		else
			timeout = a
		end

		if not Lettuce.config.enabled or not Lettuce.config.intercept
				or not hostAllowed(hostOf(url)) then
			return passthrough(url, headers, timeout)
		end

		local body = Lettuce.fetch(url, headers, timeout)

		if body ~= nil then
			return body
		end

		return ""
	end

	Lettuce._wrapper = wrapper
	curl.downloadString = wrapper

	safeLog("info", "curl.downloadString is proxied for "
		.. (#cfg.hosts == 0 and "all hosts" or (#cfg.hosts .. " host(s)")))

	return true
end

-- =========================================================================
-- Status / reload
-- =========================================================================

Lettuce.status = function()
	local cfg = Lettuce.config
	local state = Lettuce.state

	return {
		version = Lettuce.VERSION,
		enabled = cfg.enabled,
		transport = (loadLibcurl() ~= nil) and "libcurl" or "curl-cli",
		intercept = (Lettuce._wrapper ~= nil and curl ~= nil
			and curl.downloadString == Lettuce._wrapper) or false,
		proxy = redactProxy(state.proxy or cfg.proxy),
		subscription = cfg.subscription,
		down = nowSec() < state.downUntil,
		lastError = redactProxy(state.lastError),
	}
end

Lettuce.reload = function()
	loadConfig()

	local state = Lettuce.state
	state.proxy = nil
	state.candidates = nil
	state.candidateIndex = 1
	state.downUntil = 0
	state.subscriptionAt = 0

	if Lettuce.config.intercept then
		Lettuce.install()
	elseif Lettuce._wrapper ~= nil and curl ~= nil
			and curl.downloadString == Lettuce._wrapper then
		local original = originalDownloadString()

		if original ~= nil then
			curl.downloadString = original
			Lettuce._wrapper = nil
		end
	end

	safeLog("info", "Reloaded lettuce configuration")

	return true
end

-- =========================================================================
-- Startup
-- =========================================================================

loadConfig()

if Lettuce.config.enabled then
	Lettuce.install()

	local summary = Lettuce.status()

	safeLog("info", "lettuce.lua " .. Lettuce.VERSION .. " loaded (transport "
		.. summary.transport .. ", "
		.. (summary.proxy and ("proxy " .. tostring(summary.proxy))
			or (Lettuce.config.subscription and "subscription" or "no proxy configured"))
		.. ")")
else
	safeLog("info", "lettuce.lua " .. Lettuce.VERSION .. " loaded but disabled")
end

pcall(function()
	if type(SLS) == "table" and type(SLS.registerCallback) == "function" then
		SLS.registerCallback("SLSsteam::luaReload", Lettuce.reload)
	end
end)

Lettuce.setup = true
