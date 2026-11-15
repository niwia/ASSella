-- styx.lua (2.5)
--
-- SLSsteam ownership ticket splicer: injects AppOwnershipTicket for
-- AdditionalApps games so they pass SteamStub DRM checks.
--
Styx = Styx or {
	setup          = false,
	active         = false,
	loginDetected  = false,
	fallbackReady  = false,
	probeUntil     = 0,
	requestCount   = 0,
	loggedOffsets  = {},
	inFallback     = false,
	starting       = false,
	initSeen       = false,
	resolveTries   = 0,
	getTicketHook  = nil,
	getTicketTramp = nil,
	getTicketFn    = nil,
	getTicketPtr   = nil,
	hookPlacementUnknown = false,
}

Styx.VERSION = "2.5"

KATAMARI = KATAMARI or { plugins = {} }
KATAMARI.plugins.styx = {
	version = Styx.VERSION,
	role = "ticket-splicer",
	priority = 40,
}

if Styx.setup then
	return
end

local S = Styx
local ffi = require("ffi")

pcall(ffi.cdef, [[
	void* place_lua_hook(const int, const void*);
	void* unpack_user_data(const void*);
	void* memmove(void*, const void*, size_t);

	typedef uint32_t(*GetTicket_t)(
		void*, uint32_t, uint8_t*, uint32_t, uint32_t*, uint32_t*, uint32_t*, uint32_t*
	);
	typedef uint32_t(*UpdateTicket_t)(void*, uint32_t, int);
]])

pcall(ffi.cdef, [[
	struct StyxTimespec { long tv_sec; long tv_nsec; };
	int clock_gettime(int, struct StyxTimespec*);
]])

local EMsg = {
	ClientAccountInfo = 768,
}

-- ---------------------------------------------------------------------------
-- Configuration (defaults <- conf file <- global table)
-- ---------------------------------------------------------------------------
local defaults = {
	enabled              = true,
	fallback_app         = 480,
	secondary_fallback   = 291550,
	splice_mode          = "insert",
	return_expanded_size = false,
	max_ticket_size      = 65536,
	debug                = false,
	notify               = false,

	refresh_retry_seconds = 5,

	probe_window_seconds = 60,
	probe_interval_seconds = 2, -- 1–60 seconds between cold packet probes
	max_warmup_requests  = 120,

	account_info_msg_type = EMsg.ClientAccountInfo,
	apps = nil,
}

local function pluginDirectory()
	local source = debug.getinfo(1, "S").source
	if source:sub(1, 1) == "@" then
		source = source:sub(2)
	end
	return source:match("^(.*)[/\\]") or "."
end

local function mergeConfig(target, source)
	if type(source) ~= "table" then
		return
	end
	for key, value in pairs(source) do
		target[key] = value
	end
end

local function loadConfigFile(path)
	local handle = io.open(path, "r")
	if not handle then
		return nil
	end
	handle:close()

	local ok, result = pcall(dofile, path)
	if not ok then
		log.warn("[Styx] Failed to load config " .. path .. ": " .. tostring(result))
		return nil
	end
	if type(result) == "table" then
		return result
	end
	return nil
end

local function motherStyxConfig()
	local home = os.getenv("HOME") or ""
	local configHome = os.getenv("XDG_CONFIG_HOME")
	if configHome == nil or configHome == "" then
		if home == "" then
			return nil
		end
		configHome = home .. "/.config"
	end

	local mother = loadConfigFile(configHome .. "/SLSsteam/katamari.conf.lua")
	if type(mother) == "table" and type(mother.styx) == "table" then
		return mother.styx
	end
	return nil
end

local config = {}
mergeConfig(config, defaults)

mergeConfig(config, motherStyxConfig())

do
	local dir = pluginDirectory()
	mergeConfig(config, loadConfigFile(dir .. "/styx.conf.lua"))
	mergeConfig(config, loadConfigFile(dir .. "/../styx.conf.lua"))
end

mergeConfig(config, rawget(_G, "StyxConfig"))

S.config = config
S.failedApps = S.failedApps or {}

-- Configuration is read exactly once, here at startup. Files are never polled
-- per request and there is no configLoaded refresh: edits to styx.conf.lua or
-- katamari.conf.lua take effect only after a Steam restart.

local function notify(message)
	if S.config.notify then
		log.notify(message)
	else
		log.info(message)
	end
end

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------
local function debug(message)
	if S.config.debug then
		log.debug("[Styx] " .. message)
	end
end

local function warnOnce(appId, message)
	local key = tostring(appId) .. ":" .. message
	if not S.failedApps[key] then
		S.failedApps[key] = true
		log.warn("[Styx] " .. message .. " for app " .. tostring(appId))
	end
end

local function warnConfigOnce(setting, message)
	local key = "config:" .. setting
	if not S.failedApps[key] then
		S.failedApps[key] = true
		log.warn("[Styx] " .. message)
	end
end

-- One preallocated timespec, used only on 64-bit Linux. Everywhere else (or if
-- clock_gettime is unavailable) monotonicNow() falls back to os.time().
local monotonicClock
local monotonicTimespec
if ffi.os == "Linux" then
	local okAbi, is64Bit = pcall(ffi.abi, "64bit")
	if okAbi and is64Bit then
		local okClock, clock, timespec = pcall(function()
			return ffi.C.clock_gettime, ffi.new("struct StyxTimespec[1]")
		end)
		if okClock then
			monotonicClock = clock
			monotonicTimespec = timespec
		end
	end
end

local function monotonicNow()
	if monotonicClock and monotonicTimespec then
		local ok, result = pcall(monotonicClock, 1, monotonicTimespec)
		if ok and result == 0 then
			local ts = monotonicTimespec[0]
			return tonumber(ts.tv_sec) + tonumber(ts.tv_nsec) / 1000000000
		end
	end
	return os.time()
end

local function validAppId(appId)
	return type(appId) == "number"
		and appId > 0
		and appId <= 4294967295
		and appId == math.floor(appId)
end

local function validSpliceMode(mode)
	return mode == "insert" or mode == "overwrite" or mode == "both"
end

local function validateStartupConfig()
	local maxSize = tonumber(S.config.max_ticket_size)
	if not maxSize or maxSize ~= math.floor(maxSize) or maxSize < 4 or maxSize > 0x100000 then
		warnConfigOnce("max_ticket_size", "invalid max_ticket_size "
			.. tostring(S.config.max_ticket_size) .. ", using 65536")
		S.config.max_ticket_size = 65536
	end

	if not validAppId(S.config.fallback_app) then
		warnConfigOnce("fallback_app", "invalid fallback_app, using 480")
		S.config.fallback_app = 480
	end

	if S.config.secondary_fallback ~= nil and not validAppId(S.config.secondary_fallback) then
		warnConfigOnce("secondary_fallback", "invalid secondary_fallback, disabling it")
		S.config.secondary_fallback = nil
	end

	if not validSpliceMode(S.config.splice_mode) then
		warnConfigOnce("splice_mode", "invalid splice_mode "
			.. tostring(S.config.splice_mode) .. ", using insert")
		S.config.splice_mode = "insert"
	end

	if S.config.apps ~= nil and type(S.config.apps) ~= "table" then
		warnConfigOnce("apps", "invalid apps config (expected table or nil), disabling")
		S.config.apps = {}
	end

	local retry = tonumber(S.config.refresh_retry_seconds)
	if not retry or retry ~= math.floor(retry) or retry < 1 or retry > 3600 then
		warnConfigOnce("refresh_retry_seconds", "invalid refresh_retry_seconds, using 5")
		S.config.refresh_retry_seconds = 5
	end

	local probeWindow = tonumber(S.config.probe_window_seconds)
	if not probeWindow or probeWindow ~= math.floor(probeWindow)
		or probeWindow < 1 or probeWindow > 3600 then
		warnConfigOnce("probe_window_seconds", "invalid probe_window_seconds, using 60")
		S.config.probe_window_seconds = 60
	end

	local probeInterval = tonumber(S.config.probe_interval_seconds)
	if not probeInterval or probeInterval ~= math.floor(probeInterval)
		or probeInterval < 1 or probeInterval > 60 then
		warnConfigOnce("probe_interval_seconds", "invalid probe_interval_seconds "
			.. tostring(S.config.probe_interval_seconds) .. ", using 2")
		S.config.probe_interval_seconds = 2
	end

	local maxWarmup = tonumber(S.config.max_warmup_requests)
	if not maxWarmup or maxWarmup ~= math.floor(maxWarmup)
		or maxWarmup < 1 or maxWarmup > 10000 then
		warnConfigOnce("max_warmup_requests", "invalid max_warmup_requests, using 120")
		S.config.max_warmup_requests = 120
	end

	if type(S.config.enabled) ~= "boolean" then
		warnConfigOnce("enabled", "invalid enabled setting, using true")
		S.config.enabled = true
	end

	local accountInfoType = tonumber(S.config.account_info_msg_type)
	if not accountInfoType or accountInfoType ~= math.floor(accountInfoType)
		or accountInfoType < 1 or accountInfoType > 0x7fffffff then
		warnConfigOnce("account_info_msg_type", "invalid account_info_msg_type "
			.. tostring(S.config.account_info_msg_type) .. ", using 768")
		S.config.account_info_msg_type = EMsg.ClientAccountInfo
	else
		S.config.account_info_msg_type = accountInfoType
	end
end

validateStartupConfig()

-- ---------------------------------------------------------------------------
-- Target resolution and native hook placement
--
-- These run at load time on the main (loading) thread, before any callback is
-- registered. They only inspect steamclient.so, so they do not need
-- S.clientUser or login state. Placing the detour here keeps LuaHook /
-- place_lua_hook off Steam's IPC worker thread (SLSsteam::initialized /
-- Network::recvPkt), which can SIGSEGV on Linux when the libc TLS layout of
-- the worker differs.
-- ---------------------------------------------------------------------------
local function resolveTargets()
	if S.resolved then
		return true
	end

	local userMap = VFTableInfo_t("14IClientUserMap", "GetAppOwnershipTicketExtendedData")
	if not userMap:init() then
		log.error("[Styx] Failed to resolve IClientUserMap::GetAppOwnershipTicketExtendedData")
		return false
	end

	local impl = VFTableInfo_t("5CUser", "GetAppOwnershipTicketExtendedData", userMap.index, 0)
	if not impl:init() then
		log.error("[Styx] Failed to resolve CUser::GetAppOwnershipTicketExtendedData")
		return false
	end
	if impl.ptr == nil then
		log.error("[Styx] Resolved CUser::GetAppOwnershipTicketExtendedData has a null address")
		return false
	end

	S.getTicketPtr = impl.ptr
	S.getTicketFn = ffi.cast("GetTicket_t", impl.ptr)
	S.getTicketIndex = userMap.index

	S.updateInfo = VFTableInfo_t("14IClientUserMap", "BUpdateAppOwnershipTicket")
	if not S.updateInfo:init() then
		log.error("[Styx] Failed to resolve IClientUserMap::BUpdateAppOwnershipTicket")
		return false
	end

	notify("[Styx] Resolved GetTicket index " .. tostring(S.getTicketIndex)
		.. ", BUpdate index " .. tostring(S.updateInfo.index))

	local maxSize = tonumber(S.config.max_ticket_size)
	if not maxSize or maxSize ~= math.floor(maxSize) or maxSize < 4 or maxSize > 0x100000 then
		maxSize = 65536
	end
	S.probeSize = maxSize
	S.probeBuf = ffi.new("uint8_t[?]", maxSize)
	S.probeOffApp = ffi.new("uint32_t[1]")
	S.probeOffSteam = ffi.new("uint32_t[1]")
	S.probeOffSig = ffi.new("uint32_t[1]")
	S.probeOffSigSize = ffi.new("uint32_t[1]")

	S.resolved = true
	return true
end

local function placeHook()
	if S.hookPlacementUnknown then
		-- A previous attempt threw while native state was ambiguous. Retrying
		-- could double-install a detour, so refuse until Steam restarts.
		return false
	end
	if S.getTicketHook then
		return true
	end

	notify("[Styx] Placing GetAppOwnershipTicketExtendedData hook at index "
		.. tostring(S.getTicketIndex))
	notify("[Styx] Constructing LuaHook target=" .. tostring(S.getTicketPtr))
	S.getTicketHook = LuaHook("Styx GetTicket", S.getTicketPtr)
	-- place_lua_hook enables the detour before returning its trampoline. The
	-- callback's nil guard fails closed during that window, but serialization
	-- before LuaJIT entry requires native SLSsteam support.
	notify("[Styx] Calling place_lua_hook index=" .. tostring(S.getTicketHook.index)
		.. " callback=" .. tostring(S.hkGetTicket))
	local trampoline = ffi.C.place_lua_hook(S.getTicketHook.index, S.hkGetTicket)
	if trampoline == nil then
		log.error("[Styx] Failed to place GetAppOwnershipTicketExtendedData hook")
		S.getTicketHook = nil
		return false
	end

	S.getTicketTramp = ffi.cast("GetTicket_t", trampoline)
	notify("[Styx] Hook placed, trampoline=" .. tostring(trampoline))
	return true
end

local function ruleFor(appId)
	local apps = S.config.apps
	if apps == nil then
		return true, S.config.fallback_app
	end

	if type(apps) == "table" and next(apps) == nil then
		local key = "config:empty-apps"
		if not S.failedApps[key] then
			S.failedApps[key] = true
			log.warn("[Styx] apps is an empty table, which disables every app; "
				.. "set apps = nil to splice all apps")
		end
	end

	local rule = apps[appId]
	if rule == nil then
		return false, nil
	elseif rule == false then
		return false, nil
	elseif rule == true then
		return true, S.config.fallback_app
	elseif type(rule) == "number" then
		return true, rule
	elseif type(rule) == "table" then
		if rule.enabled == false then
			return false, nil
		end
		if rule.fallback_app ~= nil then
			return true, rule.fallback_app
		end
		return true, S.config.fallback_app
	end

	return false, nil
end

-- ---------------------------------------------------------------------------
-- Ticket layout / splicing
-- ---------------------------------------------------------------------------
local function ticketLayoutIsSafe(size, ticketSize, sigOffset)
	local maxSize = tonumber(S.config.max_ticket_size)
	if not maxSize or maxSize ~= math.floor(maxSize) or maxSize < 4 or maxSize > 0x100000 then
		return false, "invalid max_ticket_size"
	end

	if size <= 0 then
		return false, "fallback returned an empty ticket"
	end
	if ticketSize <= 0 or ticketSize > maxSize then
		return false, "ticket buffer size is outside the safety limit"
	end
	if size > ticketSize then
		return false, "ticket size exceeds the caller buffer"
	end
	if sigOffset < 0 or sigOffset > size then
		return false, "signature offset is outside the ticket"
	end
	if size + 4 > ticketSize then
		return false, "ticket buffer has no room for the app id"
	end

	return true
end

local function putLittleEndianU32(buffer, offset, value)
	local bit = require("bit")
	for i = 0, 3 do
		buffer[offset + i] = bit.band(bit.rshift(value, i * 8), 0xff)
	end
end

local function spliceTicket(pTicket, ticketSize, size, sigOffset, appId)
	if pTicket == nil then
		return false, "ticket buffer pointer is null"
	end
	local safe, reason = ticketLayoutIsSafe(size, ticketSize, sigOffset)
	if not safe then
		return false, reason
	end
	if not validAppId(appId) then
		return false, "fallback app id is invalid"
	end

	local newSize = size + 4
	local temporary = ffi.new("uint8_t[?]", newSize)
	local source = ffi.cast("const uint8_t*", pTicket)
	local destination = ffi.cast("uint8_t*", temporary)

	ffi.copy(destination, source, size)
	ffi.C.memmove(destination + sigOffset + 4, destination + sigOffset, size - sigOffset)
	putLittleEndianU32(destination, sigOffset, appId)
	ffi.copy(pTicket, destination, newSize)

	return true, newSize
end

local function runtimeOptions(appId)
	local options = {
		mode = "insert",
		expanded = false,
	}

	local configured = {}
	mergeConfig(configured, S.config)

	if configured.splice_mode ~= nil then
		if validSpliceMode(configured.splice_mode) then
			options.mode = configured.splice_mode
		else
			warnConfigOnce("splice_mode", "invalid runtime splice_mode "
				.. tostring(configured.splice_mode) .. ", using insert")
		end
	end
	if configured.return_expanded_size ~= nil then
		if type(configured.return_expanded_size) == "boolean" then
			options.expanded = configured.return_expanded_size
		else
			warnConfigOnce("return_expanded_size",
				"invalid runtime return_expanded_size, using false")
		end
	end

	local override = rawget(_G, "StyxConfig")
	if type(override) == "table" then
		if override.splice_mode ~= nil then
			if validSpliceMode(override.splice_mode) then
				options.mode = override.splice_mode
			else
				warnConfigOnce("splice_mode", "invalid runtime splice_mode "
					.. tostring(override.splice_mode) .. ", using insert")
			end
		end
		if override.return_expanded_size ~= nil then
			if type(override.return_expanded_size) == "boolean" then
				options.expanded = override.return_expanded_size
			else
				warnConfigOnce("return_expanded_size",
					"invalid runtime return_expanded_size, using false")
			end
		end
	end

	debug("splice options for " .. tostring(appId) .. ": mode=" .. options.mode
		.. " expanded=" .. tostring(options.expanded))
	return options
end

local function overwriteAppId(pTicket, ticketSize, size, appOffset, appId)
	local maxSize = tonumber(S.config.max_ticket_size)
	if pTicket == nil then
		return false, "ticket buffer pointer is null"
	end
	if not maxSize or maxSize ~= math.floor(maxSize)
		or maxSize < 4 or maxSize > 0x100000
		or ticketSize <= 0 or ticketSize > maxSize
		or size <= 0 or size > ticketSize then
		return false, "ticket size is outside the caller buffer or safety limit"
	end
	if not validAppId(appId) then
		return false, "app id is invalid"
	end
	if appOffset < 0 or appOffset + 4 > size then
		return false, "app id offset is outside the ticket"
	end
	if appOffset + 4 > ticketSize then
		return false, "app id offset is outside the caller buffer"
	end
	putLittleEndianU32(ffi.cast("uint8_t*", pTicket), appOffset, appId)
	return true
end

local function logTicketLayout(appId, pTicket, fallbackSize, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize, options)
	if not S.config.debug then
		return
	end
	local safeBytes = math.min(fallbackSize, ticketSize or fallbackSize)
	local canFullyRead = pTicket ~= nil and safeBytes >= 20
	local previous = S.loggedOffsets[appId]
	if previous == "full" or (previous and not canFullyRead) then
		return
	end
	S.loggedOffsets[appId] = canFullyRead and "full" or "partial"

	local length, version, ticketApp = "n/a", "n/a", "n/a"
	if pTicket ~= nil and safeBytes >= 8 then
		local head = ffi.cast("const uint8_t*", pTicket)
		local function readU32(offset)
			return head[offset]
				+ head[offset + 1] * 0x100
				+ head[offset + 2] * 0x10000
				+ head[offset + 3] * 0x1000000
		end
		length, version = readU32(0), readU32(4)
		if safeBytes >= 20 then
			ticketApp = readU32(16)
		end
	end
	local function offsetValue(pointer)
		if pointer == nil then
			return "nil"
		end
		return tostring(pointer[0])
	end
	notify(string.format(
		"[Styx] ticket for %d: size=%d len=%s ver=%s app=%s "
			.. "appOff=%s steamOff=%s sigOff=%s sigSize=%s mode=%s expanded=%s",
		appId, fallbackSize, length, version, ticketApp,
		offsetValue(pOffAppId), offsetValue(pOffSteamId),
		offsetValue(pOffSig), offsetValue(pOffSigSize),
		options.mode, tostring(options.expanded)
	))
end

local function spliceByMode(pTicket, ticketSize, fallbackSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize, appId, options)
	if pTicket == nil then
		return false, "ticket buffer pointer is null"
	end
	if pOffAppId == nil or pOffSig == nil then
		return false, "required ticket offset pointer is null"
	end
	if not validAppId(appId) then
		return false, "app id is invalid"
	end
	local sigOffset = pOffSig[0]

	if options.mode == "overwrite" then
		local ok, reason = overwriteAppId(pTicket, ticketSize, fallbackSize, pOffAppId[0], appId)
		if not ok then
			return false, reason
		end
		return true, fallbackSize
	end

	if options.mode == "both" then
		local safe, reason = ticketLayoutIsSafe(fallbackSize, ticketSize, sigOffset)
		if not safe then
			return false, reason
		end
		local appOffset = pOffAppId[0]
		if appOffset < 0 or appOffset + 4 > fallbackSize or appOffset + 4 > ticketSize then
			return false, "app id offset is outside the ticket or caller buffer"
		end
		if not validAppId(appId) then
			return false, "fallback app id is invalid"
		end
		-- Build the combined mutation in a temporary buffer, so validation or
		-- allocation failures cannot leave a partially overwritten ticket.
		local newSize = fallbackSize + 4
		local temporary = ffi.new("uint8_t[?]", newSize)
		local source = ffi.cast("const uint8_t*", pTicket)
		local destination = ffi.cast("uint8_t*", temporary)
		ffi.copy(destination, source, fallbackSize)
		local ok, overwriteReason = overwriteAppId(destination, newSize, fallbackSize, appOffset, appId)
		if not ok then
			return false, overwriteReason
		end
		ffi.C.memmove(destination + sigOffset + 4, destination + sigOffset, fallbackSize - sigOffset)
		putLittleEndianU32(destination, sigOffset, appId)
		ffi.copy(pTicket, destination, newSize)
		pOffAppId[0] = sigOffset
		pOffSig[0] = sigOffset + 4
		if options.expanded then
			return true, newSize
		end
		return true, fallbackSize
	end

	local ok, result = spliceTicket(pTicket, ticketSize, fallbackSize, sigOffset, appId)
	if not ok then
		return false, result
	end
	pOffAppId[0] = sigOffset
	pOffSig[0] = sigOffset + 4
	if options.expanded then
		return true, result
	end
	return true, fallbackSize
end

-- ---------------------------------------------------------------------------
-- Calling the real GetAppOwnershipTicketExtendedData
-- ---------------------------------------------------------------------------
local function callOriginal(trampoline, pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
	-- Never fall back to S.getTicketFn here: once detour placement starts that
	-- address may route back into hkGetTicket.
	if not trampoline then
		return 0
	end
	return trampoline(pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
end

-- ---------------------------------------------------------------------------
-- Fallback warm-up (the step earlier experiments never managed to run)
-- ---------------------------------------------------------------------------
local function resolveUpdateFunction()
	if S.updateFn then
		return S.updateFn
	end
	if not S.updateInfo then
		return nil
	end

	if S.clientUser then
		local vtable = ffi.cast("void**", S.clientUser)[0]
		if vtable ~= nil then
			local slot = ffi.cast("void**", vtable)[S.updateInfo.index]
			if slot ~= nil then
				S.updateFn = ffi.cast("UpdateTicket_t", slot)
				debug("BUpdateAppOwnershipTicket resolved from live vtable at index "
					.. tostring(S.updateInfo.index))
				return S.updateFn
			end
		end
	end

	if S.updateInfo.ptr ~= nil then
		S.updateFn = ffi.cast("UpdateTicket_t", S.updateInfo.ptr)
		debug("BUpdateAppOwnershipTicket resolved from IClientUserMap")
		return S.updateFn
	end

	return nil
end

local function recallFallback(reason)
	local updateFn = resolveUpdateFunction()
	if not updateFn or not S.clientUser then
		return false
	end

	local now = monotonicNow()
	local retry = tonumber(S.config.refresh_retry_seconds) or 5
	if S.lastRequestAt and (now - S.lastRequestAt) < retry then
		debug("Skipping BUpdate for " .. tostring(S.config.fallback_app) .. " (rate limited)")
		return false
	end

	S.lastRequestAt = now
	S.probeUntil = now + (tonumber(S.config.probe_window_seconds) or 60)
	S.requestCount = (S.requestCount or 0) + 1
	if S.requestCount > (tonumber(S.config.max_warmup_requests) or 120) then
		local key = "config:warmup-cap"
		if not S.failedApps[key] then
			S.failedApps[key] = true
			log.warn("[Styx] Giving up on fallback ticket warm-up after "
				.. tostring(S.config.max_warmup_requests or 120) .. " requests")
		end
		return false
	end

	local appId = S.config.fallback_app
	local result = updateFn(S.clientUser, appId, 0)
	notify("[Styx] BUpdateAppOwnershipTicket(" .. tostring(appId)
		.. ", false) -> " .. tostring(result) .. " (" .. tostring(reason) .. ")")
	return true
end

local function probeFallback()
	if S.fallbackReady then
		return true
	end
	if not S.getTicketFn or not S.clientUser or not S.probeBuf then
		return false
	end

	local fn = S.getTicketTramp
	if not fn then
		-- Before the hook exists, the raw target is safe for warm-up. During
		-- placement, it may already point back through hkGetTicket.
		if S.getTicketHook then
			return false
		end
		fn = S.getTicketFn
	end
	if not fn then
		return false
	end
	local appId = S.config.fallback_app
	local size = fn(
		S.clientUser, appId, S.probeBuf, S.probeSize,
		S.probeOffApp, S.probeOffSteam, S.probeOffSig, S.probeOffSigSize
	)

	if size and size > 0 then
		S.fallbackReady = true
		notify("[Styx] Fallback ticket " .. tostring(appId)
			.. " is loaded (" .. tostring(size) .. " bytes) — splicing is live")
		return true
	end
	return false
end

-- ---------------------------------------------------------------------------
-- Core ticket handler
-- ---------------------------------------------------------------------------
local function handleTicket(trampoline, pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
	debug("handleTicket: appId=" .. tostring(appId) .. " ticketSize=" .. tostring(ticketSize))
	if pTicket == nil and ticketSize > 0 then
		warnOnce(appId, "ticket buffer pointer is null")
		return 0
	end
	local originalSize = callOriginal(trampoline,
		pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
	)
	debug("handleTicket: originalSize=" .. tostring(originalSize))

	if originalSize > 0 or not S.active or not S.config.enabled then
		return originalSize
	end

	local eligible, fallbackApp = ruleFor(appId)
	if not eligible then
		return originalSize
	end
	if not validAppId(fallbackApp) or fallbackApp == appId then
		return originalSize
	end

	local fallbackSize = callOriginal(trampoline,
		pUser, fallbackApp, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
	)

	if fallbackSize <= 0 then
		local secondary = S.config.secondary_fallback
		if validAppId(secondary) and secondary ~= fallbackApp and secondary ~= appId then
			debug("primary fallback " .. tostring(fallbackApp) .. " empty, trying " .. tostring(secondary))
			fallbackSize = callOriginal(trampoline,
				pUser, secondary, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
			)
			if fallbackSize > 0 then
				fallbackApp = secondary
			end
		end

		if fallbackSize <= 0 then
			warnOnce(appId, "asked before the fallback ticket was warm; returning 0")
			recallFallback("hook miss for " .. tostring(appId))
			return 0
		end
	end

	S.fallbackReady = true
	local maxSize = tonumber(S.config.max_ticket_size)
	if not maxSize or fallbackSize > ticketSize or fallbackSize > maxSize
		or ticketSize <= 0 or ticketSize > maxSize then
		warnOnce(appId, "fallback ticket size exceeds the caller buffer or safety limit")
		return 0
	end

	local options = runtimeOptions(appId)
	logTicketLayout(appId, pTicket, fallbackSize, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize, options)

	local ok, result = spliceByMode(
		pTicket, ticketSize, fallbackSize,
		pOffAppId, pOffSteamId, pOffSig, pOffSigSize,
		appId, options
	)
	if not ok then
		warnOnce(appId, result)
		return fallbackSize
	end

	notify("[Styx] Spliced app " .. tostring(appId)
		.. " from " .. tostring(fallbackApp) .. " using " .. options.mode
		.. " (" .. tostring(fallbackSize) .. " -> " .. tostring(result) .. " bytes)")
	debug("spliced app " .. tostring(appId) .. " from " .. tostring(fallbackApp)
		.. " (" .. tostring(fallbackSize) .. " -> " .. tostring(result) .. " bytes)")

	return result
end

S.seenApps = S.seenApps or {}

-- Keep the existing callback cdata anchored if a same-state reload happens
-- after the native detour was installed. Replacing it could let the old
-- callback be collected while the detour still targets it.
local function createGetTicketCallback()
	return ffi.cast("GetTicket_t", function(
	pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
)
	local mutex = LuaMutex()
	local trampoline = S.getTicketTramp
	local previousInFallback = S.inFallback
	local result = 0
	local ok, err = pcall(function()
		if not trampoline then
			warnOnce(appId, "GetTicket trampoline unavailable")
			return
		end

		if not S.seenApps[appId] then
			S.seenApps[appId] = true
			notify("[Styx] HK GetTicket: appId=" .. tostring(appId)
				.. " ticketSize=" .. tostring(ticketSize))
		end
		debug("hkGetTicket appId=" .. tostring(appId) .. " ticketSize=" .. tostring(ticketSize))

		if previousInFallback then
			result = callOriginal(trampoline,
				pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
			)
			return
		end

		S.inFallback = true
		result = handleTicket(trampoline, pUser, appId, pTicket, ticketSize,
			pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
	end)
	S.inFallback = previousInFallback

	if not ok then
		pcall(function()
			warnOnce(appId, "hook error: " .. tostring(err))
		end)
		result = 0
	end

	-- Explicitly release before returning; SLSsteam's LuaMutex destructor also
	-- unlocks its std::recursive_mutex when this userdata is collected. See
	-- styx.md for the wrapper lifecycle caveat. This lock is acquired only
	-- after entering the FFI callback; native code must serialize VM entry.
	mutex:unlock()
	return result or 0
end)
end

if not (S.getTicketHook and S.hkGetTicket) then
	S.hkGetTicket = createGetTicketCallback()
end

-- Place the GetTicket hook now, on the load-time (main) thread, so the native
-- detour is never installed from Steam's IPC worker thread. resolveTargets()
-- and placeHook() only inspect steamclient.so and do not need S.clientUser or
-- login state. S.getTicketHook and S.hkGetTicket stay anchored across reloads,
-- and placeHook() is idempotent once the hook exists. Failures are logged and
-- swallowed so a bad resolve never crashes the loader.
if S.config.enabled then
	local okResolve, resolved = pcall(resolveTargets)
	if not okResolve then
		log.error("[Styx] resolveTargets failed at load time: " .. tostring(resolved))
	elseif resolved then
		local okPlace, placeErr = pcall(placeHook)
		if not okPlace then
			-- placeHook may have installed a partial detour before throwing.
			-- Keep S.getTicketHook and S.hkGetTicket anchored, never retry, and
			-- require a restart so the ambiguous native state is cleared.
			S.hookPlacementUnknown = true
			log.error("[Styx] placeHook threw at load time; native hook state is "
				.. "unknown and a Steam restart is required: " .. tostring(placeErr))
		end
	end
end

-- ---------------------------------------------------------------------------
-- Client-user resolution (runtime, after login)
-- ---------------------------------------------------------------------------
local function resolveClientUser()
	if S.clientUser then
		return true
	end

	local engine = SLS and SLS.steamEngine
	if not engine then
		return false
	end

	local user = engine:getUser(0)
	if not user then
		return false
	end

	local clientUser = user:getClientUser()
	if not clientUser then
		return false
	end

	S.userUd = user
	S.clientUserUd = clientUser
	S.clientUser = ffi.C.unpack_user_data(clientUser)
	S.updateFn = nil

	return S.clientUser ~= nil
end

local function start()
	if S.active then
		return
	end
	if not S.config.enabled then
		return
	end

	if S.hookPlacementUnknown then
		if not S.warnedPlacementUnknown then
			S.warnedPlacementUnknown = true
			log.warn("[Styx] GetTicket hook state is unknown after a load-time failure; "
				.. "refusing activation (restart Steam)")
		end
		return
	end

	-- The hook must already be installed at load time. Never call placeHook()
	-- or resolveTargets() here: start() runs from the Steam IPC worker thread,
	-- and entering LuaJIT/place_lua_hook from there can SIGSEGV across libc TLS.
	if not S.getTicketHook or not S.getTicketTramp then
		if not S.warnedHookMissing then
			S.warnedHookMissing = true
			log.warn("[Styx] GetTicket hook was not placed at load time; skipping activation")
		end
		return
	end

	if not resolveClientUser() then
		return
	end

	local subscribed = false
	if S.userUd then
		local ok, result = pcall(function()
			return S.userUd:isSubscribed(S.config.fallback_app)
		end)
		subscribed = ok and result == true
	end
	log.info("[Styx] Activating — warming spacewar ("
		.. tostring(S.config.fallback_app) .. "), subscribed=" .. tostring(subscribed)
		.. ", ticket hook already armed at load time")

	recallFallback("login")
	local now = monotonicNow()
	S.lastProbeAt = now
	probeFallback()

	S.active = true
end

local function ensureStarted()
	if S.active or S.starting then
		return
	end
	if S.resolveTries >= 10 then
		return
	end

	S.starting = true
	S.resolveTries = S.resolveTries + 1
	local ok, err = pcall(start)
	S.starting = false
	if not ok then
		log.warn("[Styx] Activation error: " .. tostring(err))
	end
end

-- ---------------------------------------------------------------------------
-- Callbacks
-- ---------------------------------------------------------------------------
local function onNetworkPacket(pkt)
	if S.active and S.fallbackReady then
		return
	end

	local body = pkt and pkt.body
	if not body then
		return
	end

	local msgType = body.type

	local accountInfoType = S.config.account_info_msg_type or EMsg.ClientAccountInfo
	local isAccountInfo = msgType == accountInfoType

	if not S.active then
		if isAccountInfo then
			if not S.loginDetected then
				S.loginDetected = true
				log.info("[Styx] Login detected via accountInfo packet ("
					.. tostring(accountInfoType) .. ")")
			end
			ensureStarted()
		elseif S.initSeen then
			ensureStarted()
		end
		return
	end

	if S.fallbackReady then
		return
	end

	local now = monotonicNow()
	if now <= S.probeUntil then
		local interval = tonumber(S.config.probe_interval_seconds) or 2
		if not S.lastProbeAt or now - S.lastProbeAt >= interval then
			S.lastProbeAt = now
			probeFallback()
		end
	elseif not S.fallbackReady and not S.warnedNoTicket then
		S.warnedNoTicket = true
		log.warn("[Styx] Spacewar (" .. tostring(S.config.fallback_app)
			.. ") ticket still absent after the warm-up window; BUpdate may be failing")
	end
	if S.fallbackReady then
		return
	end

	if isAccountInfo then
		recallFallback("accountInfo")
	end
end

local function onInitialized()
	S.initSeen = true
	resolveClientUser()
	ensureStarted()
end

SLS.registerCallback("Network::recvPkt", onNetworkPacket)
SLS.registerCallback("SLSsteam::initialized", onInitialized)

S.setup = true


notify("styx.lua " .. Styx.VERSION .. " loaded — will warm spacewar ("
	.. tostring(S.config.fallback_app) .. ") at init/accountInfo")
