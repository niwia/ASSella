-- styx.lua (2.1)
--
-- Styx is the app ownership ticket splicer for SLSsteam.  It gives SteamStub a
-- valid spacewar (480) ownership ticket wearing the requested app's id, so
-- unowned or token-only apps can pass their DRM check.
--
-- The working recipe:
--   1. Watch the network for k_EMsgClientAccountInfo (768).  That is the
--      "logged on, safe to touch IClientUser" signal.  206 is not a real EMsg.
--   2. Ask IClientUser for spacewar's ownership ticket with
--      BUpdateAppOwnershipTicket(480, false).  staleOnly must be false.
--   3. Hook GetAppOwnershipTicketExtendedData.  When the caller has no ticket
--      for its app, fetch 480's ticket, insert the requested app id at the
--      signature offset, shift the returned offsets by four, and return the
--      original 480 ticket size.
--   4. Leave the first 50 bytes of the ticket untouched.  SteamStub verifies
--      the RSA signature over that signed header; the insertion only moves the
--      offsets the caller is told to trust.
--
-- ======================= 2.1 changes from 0.4 ==========================
--  44. Renamed to Styx.  Canonical files are styx.lua and styx.conf.lua; the
--      legacy filenames are retired.  Config is optional: the
--      built-in defaults are the known-good insert mode.
--  45. Confirmed on SteamStub (app 3404260): insert + original ticket size is
--      the shape that passes.  Overwrite invalidates the signed 50-byte header
--      and is rejected; returning size + 4 is also rejected.  Both remain
--      selectable fallbacks for other stubs.
--  46. Versioning follows SpaceBunny: Styx.VERSION, a numbered changelog in
--      this header, and the version in the load notification.
--  47. The conf is re-read on every ticket request, so splice_mode and
--      return_expanded_size can be changed without restarting Steam.
--  48. Everything from 0.4 is kept: accountInfo 768 login gate, BUpdate(480,
--      false) through the live IClientUser vtable, readiness probing, fail-open
--      bounds checks, per-app rules, secondary fallback, and the test suite.
--
-- Verified working: app 3404260 after SteamStub.
--
-- Flow:
--   load -> initialized/accountInfo -> resolve vtables -> BUpdate(480, false)
--        -> place GetAppOwnershipTicketExtendedData detour
--        -> probe until 480's ticket lands -> splice

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
}

Styx.VERSION = "2.1"

KATAMARI = KATAMARI or { plugins = {} }
KATAMARI.plugins.styx = {
	version = Styx.VERSION,
	role = "ticket-splicer",
	priority = 30,
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

-- Public SLS message numbers (src/sdk/protobufs/enums_clientserver.pb.h)
local EMsg = {
	ClientAccountInfo = 768,
}

-- ---------------------------------------------------------------------------
-- Configuration (defaults <- conf file <- global table)
-- ---------------------------------------------------------------------------
local defaults = {
	enabled              = true,
	fallback_app         = 480,      -- spacewar. the only app every account owns.
	secondary_fallback   = 291550,   -- last resort, not warmed over the network
	-- Confirmed against SteamStub (app 3404260): insert + old size works,
	-- overwrite is rejected (the stub reads the returned offsets and verifies
	-- the untouched 50-byte signed header).
	splice_mode          = "insert",
	return_expanded_size = false,
	max_ticket_size      = 65536,
	debug                = false,

	-- Minimum seconds between BUpdate requests for the fallback app, so a game
	-- hammering GetTicket cannot turn into a request flood.
	refresh_retry_seconds = 5,

	-- How long to keep probing for the fallback ticket after a request, and how
	-- many requests to make before giving up and relying on lazy hook retries.
	probe_window_seconds = 60,
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

-- 1. Mother config.  2. Per-plugin conf overrides it.
mergeConfig(config, motherStyxConfig())

do
	local dir = pluginDirectory()
	mergeConfig(config, loadConfigFile(dir .. "/styx.conf.lua"))
	mergeConfig(config, loadConfigFile(dir .. "/../styx.conf.lua"))
end

mergeConfig(config, rawget(_G, "StyxConfig"))

S.config = config
S.failedApps = S.failedApps or {}

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

local function validAppId(appId)
	return type(appId) == "number"
		and appId > 0
		and appId <= 4294967295
		and appId == math.floor(appId)
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
		return true, rule.fallback_app or S.config.fallback_app
	end

	return false, nil
end

-- ---------------------------------------------------------------------------
-- Ticket layout / splicing
-- ---------------------------------------------------------------------------
local function ticketLayoutIsSafe(size, ticketSize, sigOffset)
	local maxSize = tonumber(S.config.max_ticket_size)
	if not maxSize or maxSize < 4 then
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

-- The working splice shape: insert the requested app id at the spacewar
-- ticket's signature offset, shift the returned offsets by four, and return the
-- *spacewar* ticket size (not size + 4).  SteamStub reads the returned offsets
-- and verifies the untouched signed header, so the insertion has to leave the
-- first 50 bytes alone.  A blunter overwrite variant exists for stubs that
-- ignore signatures; it is not the default.  The conf is re-read on every
-- ticket request so the mode can be changed without restarting Steam.
local function runtimeOptions(appId)
	local options = {
		mode = "insert",
		expanded = false,
	}

	local dir = pluginDirectory()
	local configured = {}
	mergeConfig(configured, S.config)
	mergeConfig(configured, motherStyxConfig())
	mergeConfig(configured, loadConfigFile(dir .. "/styx.conf.lua"))
	mergeConfig(configured, loadConfigFile(dir .. "/../styx.conf.lua"))

	if type(configured.splice_mode) == "string" then
		options.mode = configured.splice_mode
	end
	if configured.return_expanded_size ~= nil then
		options.expanded = configured.return_expanded_size ~= false
	end

	local override = rawget(_G, "StyxConfig")
	if type(override) == "table" then
		if type(override.splice_mode) == "string" then
			options.mode = override.splice_mode
		end
		if override.return_expanded_size ~= nil then
			options.expanded = override.return_expanded_size ~= false
		end
	end

	debug("splice options for " .. tostring(appId) .. ": mode=" .. options.mode
		.. " expanded=" .. tostring(options.expanded))
	return options
end

local function overwriteAppId(pTicket, ticketSize, size, appOffset, appId)
	if appOffset < 0 or appOffset + 4 > size then
		return false, "app id offset is outside the ticket"
	end
	if appOffset + 4 > ticketSize then
		return false, "app id offset is outside the caller buffer"
	end
	putLittleEndianU32(ffi.cast("uint8_t*", pTicket), appOffset, appId)
	return true
end

local function logTicketLayout(appId, pTicket, fallbackSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize, options)
	if S.loggedOffsets[appId] then
		return
	end
	S.loggedOffsets[appId] = true

	local head = ffi.cast("uint32_t*", pTicket)
	log.notify(string.format(
		"[Styx] ticket for %d: size=%d len=%d ver=%d app=%d "
			.. "appOff=%d steamOff=%d sigOff=%d sigSize=%d mode=%s expanded=%s",
		appId, fallbackSize, head[0], head[1], head[4],
		pOffAppId[0], pOffSteamId[0], pOffSig[0], pOffSigSize[0],
		options.mode, tostring(options.expanded)
	))
end

local function spliceByMode(pTicket, ticketSize, fallbackSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize, appId, options)
	local sigOffset = pOffSig[0]

	if options.mode == "overwrite" then
		local ok, reason = overwriteAppId(pTicket, ticketSize, fallbackSize, pOffAppId[0], appId)
		if not ok then
			return false, reason
		end
		return true, fallbackSize
	end

	if options.mode == "both" then
		local ok, reason = overwriteAppId(pTicket, ticketSize, fallbackSize, pOffAppId[0], appId)
		if not ok then
			return false, reason
		end
		local inserted, result = spliceTicket(pTicket, ticketSize, fallbackSize, sigOffset, appId)
		if not inserted then
			return false, result
		end
		pOffAppId[0] = sigOffset
		pOffSig[0] = sigOffset + 4
		if options.expanded then
			return true, result
		end
		return true, fallbackSize
	end

	-- default: insert at the signature offset (the shape SteamStub accepts)
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
local function callOriginal(pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
	local fn = S.getTicketTramp or S.getTicketFn
	if not fn then
		return 0
	end
	return fn(pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
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

	-- Prefer the live vtable slot.  SLS already replaced this slot with its own
	-- hook, so calling through it gives us SLS' staleOnly handling for free.
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

	-- Fallback: the address the decompiler found for the interface itself.
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

	local now = os.time()
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
	local result = updateFn(S.clientUser, appId, 0) -- staleOnly = false
	log.notify("[Styx] BUpdateAppOwnershipTicket(" .. tostring(appId)
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

	local fn = S.getTicketTramp or S.getTicketFn
	local appId = S.config.fallback_app
	local size = fn(
		S.clientUser, appId, S.probeBuf, S.probeSize,
		S.probeOffApp, S.probeOffSteam, S.probeOffSig, S.probeOffSigSize
	)

	if size and size > 0 then
		S.fallbackReady = true
		log.notify("[Styx] Fallback ticket " .. tostring(appId)
			.. " is loaded (" .. tostring(size) .. " bytes) — splicing is live")
		return true
	end
	return false
end

-- ---------------------------------------------------------------------------
-- Core ticket handler
-- ---------------------------------------------------------------------------
local function handleTicket(pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
	debug("handleTicket: appId=" .. tostring(appId) .. " ticketSize=" .. tostring(ticketSize))
	local originalSize = callOriginal(
		pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
	)
	debug("handleTicket: originalSize=" .. tostring(originalSize))

	if originalSize > 0 or not S.config.enabled then
		return originalSize
	end

	local eligible, fallbackApp = ruleFor(appId)
	if not eligible then
		return originalSize
	end
	if not validAppId(fallbackApp) or fallbackApp == appId then
		return originalSize
	end

	local fallbackSize = callOriginal(
		pUser, fallbackApp, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
	)

	if fallbackSize <= 0 then
		local secondary = S.config.secondary_fallback
		if validAppId(secondary) and secondary ~= fallbackApp and secondary ~= appId then
			debug("primary fallback " .. tostring(fallbackApp) .. " empty, trying " .. tostring(secondary))
			fallbackSize = callOriginal(
				pUser, secondary, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
			)
			if fallbackSize > 0 then
				fallbackApp = secondary
			end
		end

		if fallbackSize <= 0 then
			-- Not warm yet.  Kick a request so the next attempt can splice.
			warnOnce(appId, "asked before the fallback ticket was warm; returning 0")
			recallFallback("hook miss for " .. tostring(appId))
			return 0
		end
	end

	S.fallbackReady = true

	local options = runtimeOptions(appId)
	logTicketLayout(appId, pTicket, fallbackSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize, options)

	local ok, result = spliceByMode(
		pTicket, ticketSize, fallbackSize,
		pOffAppId, pOffSteamId, pOffSig, pOffSigSize,
		appId, options
	)
	if not ok then
		warnOnce(appId, result)
		return fallbackSize
	end

	log.notify("[Styx] Spliced app " .. tostring(appId)
		.. " from " .. tostring(fallbackApp) .. " using " .. options.mode
		.. " (" .. tostring(fallbackSize) .. " -> " .. tostring(result) .. " bytes)")
	debug("spliced app " .. tostring(appId) .. " from " .. tostring(fallbackApp)
		.. " (" .. tostring(fallbackSize) .. " -> " .. tostring(result) .. " bytes)")

	return result
end

S.seenApps = S.seenApps or {}

S.hkGetTicket = ffi.cast("GetTicket_t", function(
	pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
)
	if not S.seenApps[appId] then
		S.seenApps[appId] = true
		log.notify("[Styx] HK GetTicket: appId=" .. tostring(appId)
			.. " ticketSize=" .. tostring(ticketSize))
	end
	debug("hkGetTicket appId=" .. tostring(appId) .. " ticketSize=" .. tostring(ticketSize))

	local mutex = LuaMutex()
	local result = 0
	local ok, err = pcall(function()
		if S.inFallback then
			result = callOriginal(
				pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize
			)
			return
		end

		S.inFallback = true
		result = handleTicket(pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
		S.inFallback = false
	end)
	S.inFallback = false

	if not ok then
		warnOnce(appId, "hook error: " .. tostring(err))
		result = 0
	end

	mutex:unlock()
	return result or 0
end)

-- ---------------------------------------------------------------------------
-- Resolution + hook placement
-- ---------------------------------------------------------------------------
local function resolveTargets()
	if S.resolved then
		return true
	end

	-- Resolve the interface map once.  This is already decompiled by SLS for its
	-- own hooks, so it is cheap and warning-free.
	local userMap = VFTableInfo_t("14IClientUserMap", "GetAppOwnershipTicketExtendedData")
	if not userMap:init() then
		log.notifyError("[Styx] Failed to resolve IClientUserMap::GetAppOwnershipTicketExtendedData")
		return false
	end

	-- Concrete CUser override at the same index: the object's vtable is
	-- CUser's, not the interface's.
	local impl = VFTableInfo_t("5CUser", "GetAppOwnershipTicketExtendedData", userMap.index, 0)
	if not impl:init() then
		log.notifyError("[Styx] Failed to resolve CUser::GetAppOwnershipTicketExtendedData")
		return false
	end

	-- Keep both forms:
	--   ptr = the raw void* LuaBridge handed us (only this may go into LuaHook)
	--   fn  = an FFI callable for direct/probe calls
	-- Passing the ffi cast into LuaHook fails with "lua object can't be cast
	-- to desired type" because it is cdata, not a lightuserdata pointer.
	S.getTicketPtr = impl.ptr
	S.getTicketFn = ffi.cast("GetTicket_t", impl.ptr)
	S.getTicketIndex = userMap.index

	-- BUpdate lives in the interface map, NOT in "5CUser".  Looking it up in
	-- the concrete map is the mistake that made earlier versions silently fail.
	S.updateInfo = VFTableInfo_t("14IClientUserMap", "BUpdateAppOwnershipTicket")
	if not S.updateInfo:init() then
		log.notifyError("[Styx] Failed to resolve IClientUserMap::BUpdateAppOwnershipTicket")
		return false
	end

	log.notify("[Styx] Resolved GetTicket index " .. tostring(S.getTicketIndex)
		.. ", BUpdate index " .. tostring(S.updateInfo.index))

	local maxSize = tonumber(S.config.max_ticket_size) or 65536
	if maxSize < 4 then
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
	-- LuaBridge userdata wraps the pointer; unpack it, never cast the box.
	S.clientUser = ffi.C.unpack_user_data(clientUser)
	S.updateFn = nil

	return S.clientUser ~= nil
end

local function placeHook()
	if S.getTicketHook then
		return true
	end

	log.notify("[Styx] Placing GetAppOwnershipTicketExtendedData hook at index "
		.. tostring(S.getTicketIndex))
	S.getTicketHook = LuaHook("Styx GetTicket", S.getTicketPtr)
	local trampoline = ffi.C.place_lua_hook(S.getTicketHook.index, S.hkGetTicket)
	if trampoline == nil then
		log.notifyError("[Styx] Failed to place GetAppOwnershipTicketExtendedData hook")
		S.getTicketHook = nil
		return false
	end

	S.getTicketTramp = ffi.cast("GetTicket_t", trampoline)
	log.notify("[Styx] Hook placed, trampoline=" .. tostring(trampoline))
	return true
end

local function start()
	if S.active then
		return
	end
	if not S.config.enabled then
		return
	end

	if not resolveClientUser() then
		return
	end
	if not resolveTargets() then
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
		.. ", arming the ticket hook")

	-- Order matters: request first, then arm.  The hook can be live before the
	-- ticket lands; it simply returns 0 once and retries on the next call.
	recallFallback("login")
	probeFallback()
	if not placeHook() then
		return
	end

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
	local body = pkt and pkt.body
	if not body then
		return
	end

	local msgType = body.type

	local accountInfoType = S.config.account_info_msg_type or EMsg.ClientAccountInfo
	local isAccountInfo = msgType == accountInfoType

	if not S.active then
		-- The dev's trigger.  We only arm here; the actual BUpdate below is
		-- re-issued on accountInfo if the "initialized" callback armed us early.
		if isAccountInfo then
			if not S.loginDetected then
				S.loginDetected = true
				log.info("[Styx] Login detected via accountInfo packet ("
					.. tostring(accountInfoType) .. ")")
			end
			ensureStarted()
		elseif S.initSeen then
			-- initialized fired but activation did not stick (engine or vtable
			-- not ready yet).  Retry on traffic, with a hard attempt cap.
			ensureStarted()
		end
		return
	end

	if S.fallbackReady then
		return
	end

	-- SLS runs this callback before the packet's trampoline, so probing on the
	-- ticket response itself is too early.  Probing on the following packets
	-- works because callbacks for packet N run after packet N-1 has already been
	-- processed by the client.  Bound it to a minute after the last request.
	if os.time() <= S.probeUntil then
		probeFallback()
	elseif not S.fallbackReady and not S.warnedNoTicket then
		S.warnedNoTicket = true
		log.warn("[Styx] Spacewar (" .. tostring(S.config.fallback_app)
			.. ") ticket still absent after the warm-up window; BUpdate may be failing")
	end
	if S.fallbackReady then
		return
	end

	-- If "initialized" armed us before logon, that first BUpdate was too early.
	-- accountInfo is the authoritative "logged on now" signal, so force exactly
	-- one retry there.  Later retries come from the GetTicket hook instead of
	-- every recvPkt callback, keeping network sends off the packet hot path.
	if isAccountInfo then
		S.lastRequestAt = nil
		recallFallback("accountInfo")
	end
end

local function onInitialized()
	S.initSeen = true
	-- Hooks are placed by the time this fires, but logon may still be in flight.
	-- Arming now is harmless: if the first BUpdate is too early, accountInfo
	-- forces a fresh one a moment later.
	resolveClientUser()
	ensureStarted()
end

SLS.registerCallback("Network::recvPkt", onNetworkPacket)
SLS.registerCallback("SLSsteam::initialized", onInitialized)

S.setup = true

log.notify("styx.lua " .. Styx.VERSION .. " loaded — will warm spacewar ("
	.. tostring(S.config.fallback_app) .. ") at init/accountInfo")
