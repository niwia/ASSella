-- spacebunnny1.5beta.lua (1.5.0-spacebunny-beta)
--
-- SLSsteam download interceptor: rewrites package 0 depot lists, injects
-- configured AES decryption keys, and resolves manifest request codes.
--
-- Based on download-1.4.0-spacetest.lua. Fixes verified in review:
--   1. snprintf cdef return type: char* -> int (was silently wrong).
--   2. Stale Package pointer: re-baseline when Steam hands a new pointer.
--   3. curl timeout units: SLSsteam passes the value straight to
--      `curl --connect-timeout` (SECONDS, no --max-time). 1.4.0 passed
--      2000/700 thinking ms (= 33/11 min stalls). 1.5 uses seconds.
--   4. Recovery dead code: removed unconditional clearAuthorityFailures()
--      on success; probe-timeout counting now requires elapsed ~= budget.
--   5. Authoritative MRC entries now carry AUTHORITY_TTL (revalidated).
--   6. Cache timestamps use monotonic clock when available (NTP-safe).
--   7. #DecryptionKeys count fixed (pairs, not # on a map).
--   8. MRC caches pruned/capped; ULL overflow rejected via round-trip.
--   9. depots.grow preserved instead of overwritten with count.
--  10. mrc_config tuning validated + FULL_MS/PROBE_MS migrated to seconds.
--
-- Test build. Not hashed into plugins_manifest.json yet.

Downloader = Downloader or {
	setup = false,

	-- Hooks
	getPackageHook = nil, getPackageTramp = nil,
	getBinaryHook = nil, getBinaryTramp = nil,
	getMRCHook = nil, getMRCTramp = nil,

	-- GetPackage vars
	PackageDepotIds = {},
	Package = nil,
	RewriteDepots = false,

	-- GetMRC vars (MAX_MANIFEST_* kept for compat, unused)
	MAX_MANIFEST_STRING_SIZE = 32,
	MAX_MANIFEST_TRIES = 5,
}

Downloader.VERSION = "1.5.0-spacebunny-beta"

-- =========================================================================
-- MRC authority model (unchanged policy, fixed mechanics)
--
-- wudrm is the authority. manifestdex is rescue-only, fixed order.
-- See 1.4.0 header for the full rationale (Cloudflare UA lock, observed
-- disagreement on 4236832901879740133, etc.). 1.5 keeps that policy and
-- fixes the implementation bugs listed in the file header.
-- =========================================================================

Downloader.MRC_AUTHORITY = {
	name = "wudrm",
	url = "http://gmrc.wudrm.com/manifest/",
	headers = nil,
}

Downloader.MRC_RESCUE = {
	name = "manifestdex",
	url = "https://manifest.manifestdex.com/",
	-- Cloudflare allowlists this exact string. Anything else gets 403.
	headers = { "User-Agent: ManifestDeX/1.0" },
}

Downloader.MRC_TUNING = {
	-- Seconds. Passed straight to `curl --connect-timeout` (see src/curl.cpp).
	-- There is no --max-time, so this is the only bound. Keep it small:
	-- the hook holds the global LuaMutex while curl runs.
	FULL_SEC = 5,
	-- Budget for the authority once it has been failing, so rescue is
	-- reachable sooner. A probe SUCCESS is still authoritative.
	PROBE_SEC = 2,
	-- Consecutive probe timeouts before the authority is marked down.
	PROBE_TIMEOUTS_TO_MARK_DOWN = 2,
	-- How long the authority is skipped after being marked down (ms).
	DOWN_TTL_MS = 60000,
	-- Consecutive fast successes that clear a marked-down authority.
	RECOVER_AFTER = 2,
	-- A response faster than this counts as healthy (ms).
	HEALTHY_MS = 800,
	-- How long a provisional (rescue-sourced) value may be reused (sec).
	PROVISIONAL_TTL = 300,
	-- How long an authoritative value may be reused before re-asking (sec).
	-- 1.4.0 pinned authority values forever; a wrong-but-well-formed
	-- answer could never recover. 86400 = revalidate daily.
	AUTHORITY_TTL = 86400,
	-- Seconds to suppress a manifest after every provider failed for it.
	FAIL_TTL = 120,
	-- EWMA smoothing factor for authority latency (observability only).
	ALPHA = 0.3,
	-- Caps so long sessions cannot grow tables without bound.
	MRCCACHE_MAX = 5000,
	MRCFAILCACHE_MAX = 2000,
}

-- Migrate + validate external tuning. Old 1.4.0 configs used FULL_MS /
-- PROBE_MS (milliseconds); 1.5 uses seconds. Accept both, prefer _SEC.
local function sanitizeMrcTuning(t)
	if type(t) ~= "table" then
		return
	end

	-- Migration from 1.4.0 millisecond keys.
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
			print("[Downloader] Invalid MRC tuning " .. key .. ", keeping default.")
			t[key] = nil
		else
			t[key] = v
		end
	end

	num("FULL_SEC", 1, 30)
	num("PROBE_SEC", 0.5, 10)
	num("PROBE_TIMEOUTS_TO_MARK_DOWN", 1, 20)
	num("DOWN_TTL_MS", 1000, 600000)
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
			print("[Downloader] Invalid MRC tuning ALPHA, keeping default.")
			t.ALPHA = nil
		else
			t.ALPHA = a
		end
	end
end

-- Load ASSella-managed MRC configuration if available in SLSsteam directory
pcall(function()
	local function usableProvider(p)
		return type(p) == "table"
			and type(p.name) == "string" and p.name ~= "" and p.name ~= "none"
			and type(p.url) == "string" and p.url:match("^https?://")
			and (p.headers == nil or type(p.headers) == "table")
	end

	local home = os.getenv("HOME") or ""
	local cfg_path = home .. "/.config/SLSsteam/mrc_config.lua"
	local ok, mrc_cfg = pcall(dofile, cfg_path)
	if ok and type(mrc_cfg) == "table" then
		local valid = true
		if mrc_cfg.authority ~= nil then
			if usableProvider(mrc_cfg.authority) then
				Downloader.MRC_AUTHORITY = mrc_cfg.authority
			else
				print("[Downloader] Invalid MRC authority config, keeping defaults.")
				valid = false
			end
		end
		if mrc_cfg.rescue ~= nil then
			if usableProvider(mrc_cfg.rescue) then
				Downloader.MRC_RESCUE = mrc_cfg.rescue
			else
				print("[Downloader] Invalid MRC rescue config, keeping defaults.")
				valid = false
			end
		end
		if valid and mrc_cfg.tuning and type(mrc_cfg.tuning) == "table" then
			sanitizeMrcTuning(mrc_cfg.tuning)
			for k, v in pairs(mrc_cfg.tuning) do
				Downloader.MRC_TUNING[k] = v
			end
		end
	end
end)

Downloader.resetSettings = function()
	-- Config-derived state. Cleared on every Lua reload so a reload cannot
	-- inherit values from the previous config generation.
	Downloader.AdditionalDepots = {}
	Downloader.DecryptionKeys = {}

	-- The cached PackageInfo pointer is only valid for the lifetime of the
	-- config generation that captured it. Drop it so the next GetPackage(0)
	-- re-captures a live one instead of writing through a freed pointer.
	Downloader.Package = nil
	Downloader.RewriteDepots = false
end
Downloader.resetSettings()

-- Include guard, do not run multiple times
if Downloader.setup then
	return
end
Downloader.setup = true

local ffi = require("ffi")
local tier0 = ffi.load("tier0_s")

-- place_lua_hook is already declared by the other plugins. Redefining it
-- raises, which would abort this file at load time.
pcall(ffi.cdef, [[ void* place_lua_hook(const int, const void*); ]])

ffi.cdef[[
	int snprintf(char*, size_t size, const char*, ...);
	unsigned long long strtoull(const char*, char**, int);

	void* Plat_Alloc(size_t);
	void Plat_Free(void*);

	typedef struct
	{
		uint32_t* mem;
		uint32_t alloc;
		uint32_t grow;
		uint32_t size;
	} CUtlVector;

	typedef struct
	{
		uint32_t packageId;
		uint8_t __pad0x0[0x44];
		CUtlVector depots;
	} PackageInfo_t;

	typedef PackageInfo_t*(*GetPackage_t)(void*, uint32_t, uint32_t, uint32_t);
	typedef int(*GetBinary_t)(void*, uint32_t, const char*, int8_t*, uint32_t);
	typedef bool(*GetMRC_t)(void*, uint32_t, uint32_t, uint64_t, const char*, uint64_t*);
]]

-- =========================================================================
-- Wall-clock timing
--
-- Needed to tell a healthy authority from a slow one. os.clock() is CPU
-- time and useless for network latency; os.time() has one-second
-- resolution, far too coarse for an 800 ms threshold. clock_gettime with
-- CLOCK_MONOTONIC is libc, not a Steam internal.
--
-- Guarded: if the cdef fails on some platform we degrade to declared order
-- with full budgets. Degraded, not broken.
-- =========================================================================

local nowMs = nil

do
	local ok = pcall(ffi.cdef, [[
		typedef long time_t;
		typedef struct { time_t tv_sec; long tv_nsec; } timespec_t;
		int clock_gettime(int clk_id, timespec_t* tp);
	]])

	if ok then
		local ts = ffi.new("timespec_t")
		local CLOCK_MONOTONIC = 1

		nowMs = function()
			if ffi.C.clock_gettime(CLOCK_MONOTONIC, ts) ~= 0 then
				return nil
			end

			return tonumber(ts.tv_sec) * 1000 + tonumber(ts.tv_nsec) / 1e6
		end
	end
end

-- Monotonic seconds for cache TTLs. Falls back to os.time() when the
-- monotonic clock is unavailable, so TTLs still work (just NTP-sensitive).
local function nowSec()
	if nowMs ~= nil then
		local ms = nowMs()
		if ms ~= nil then
			return ms / 1000
		end
	end
	return os.time()
end

-- =========================================================================
-- Hook safety
--
-- SLSsteam hooks do not run in protected mode. An error that reaches the
-- hook boundary takes Steam down with it; pcall inside the hook body only
-- helps if the pcall actually encloses the throwing code.
--
-- runInHook wraps the *entire* hook: mutex construction, the trampoline
-- call, the hook body, the unlock, and the recovery log.
-- =========================================================================

-- safeLog: a log call that cannot itself throw. Used on every path where a
-- failure has already occurred, so reporting a problem never becomes a
-- second problem.
Downloader.safeLog = function(level, msg)
	pcall(function()
		if type(msg) == "string" then
			log[level](msg)
		else
			log[level](tostring(msg))
		end
	end)
end

-- runInHook(label, fallback, fn) -> fallback | fn's return value
Downloader.runInHook = function(label, fallback, fn)
	local mutex = nil
	local locked = false
	local result

	-- Outer pcall: nothing below this line may throw into the hook.
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
			Downloader.safeLog("error",
				"[" .. label .. "] mutex unlock failed: " .. tostring(uerr))
		end
	end

	if not ok then
		Downloader.safeLog("error",
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

-- Format an unsigned 64-bit value without going through Lua's double
-- conversion (which loses precision above 2^53) and without the NUL
-- padding that ffi.string(ptr, len) would append.
Downloader.u64str = function(value)
	local buf = ffi.new("char[24]")
	ffi.C.snprintf(buf, 24, "%llu", ffi.cast("uint64_t", value))
	return ffi.string(buf)
end

-- Same idea for the 32-bit ids, so log lines read "108602" not "108602ULL".
Downloader.u32str = function(value)
	local buf = ffi.new("char[16]")
	ffi.C.snprintf(buf, 16, "%u", ffi.cast("uint32_t", value))
	return ffi.string(buf)
end

-- Resolve a pattern scan to an absolute target, bailing out cleanly if the
-- signature is gone rather than feeding nil into getJmpTarget.
local function resolvePattern(label, pattern, module)
	local scan = memhlp.patternScan(pattern, module)

	if scan == nil then
		log.notifyError("Pattern scan failed for " .. label .. " - SLSsteam update?")
		return nil
	end

	local target = memhlp.getJmpTarget(scan)

	if target == nil then
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

-- Cheap sanity check on the cached PackageInfo before writing through it.
-- This cannot make a freed pointer safe to dereference, but it does reject
-- a NULL mem, an absurd depot count, and an alloc smaller than size.
Downloader.isUsablePackage = function(pkg)
	if pkg == nil then
		return false
	end

	if pkg.packageId ~= 0 then
		Downloader.safeLog("warn",
			"Cached package id is " .. Downloader.u32str(pkg.packageId) .. ", expected 0")
		return false
	end

	local depots = pkg.depots

	if depots == nil or depots.mem == nil then
		Downloader.safeLog("warn", "Cached package has a null depot vector, skipping injection")
		return false
	end

	if depots.size > 0x10000 then
		Downloader.safeLog("warn", "Cached package reports an implausible depot count, skipping injection")
		return false
	end

	if depots.alloc < depots.size then
		Downloader.safeLog("warn", "Cached package depot alloc < size, skipping injection")
		return false
	end

	return true
end

-- Do not assign other table, iirc Lua creates a ref if you do.
-- Order is preserved (originals first, then additions) and each id is
-- emitted once, so a depot listed in both cannot land in the vector twice.
Downloader.getAllDepots = function()
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

	append(Downloader.PackageDepotIds)
	append(Downloader.AdditionalDepots)

	return merged
end

Downloader.writeDepotIds = function()
	local pkg = Downloader.Package

	if not Downloader.isUsablePackage(pkg) then
		return
	end

	local depots = pkg.depots
	local mem = depots.mem
	local size = depots.size
	local grow = depots.grow

	Downloader.safeLog("debug", "writeDepotIds into Package "
		.. Downloader.u32str(pkg.packageId) .. " at " .. tostring(pkg))

	if #Downloader.PackageDepotIds < 1 and size > 0 then
		-- Back up original depot Ids. Only ever done once per package;
		-- afterwards PackageDepotIds is the restore point.
		for i = 0, size - 1 do
			Downloader.PackageDepotIds[#Downloader.PackageDepotIds + 1] = mem[i]
		end

		Downloader.safeLog("debug",
			"Backed up " .. #Downloader.PackageDepotIds .. " original depot ids")
	end

	local merged = Downloader.getAllDepots()

	if #merged < 1 then
		Downloader.safeLog("warn", "Refusing to write an empty depot vector, keeping original")
		return
	end

	local new = ffi.cast("uint32_t*", tier0.Plat_Alloc(4 * #merged))

	if new == nil then
		Downloader.safeLog("error",
			"Plat_Alloc failed for " .. #merged .. " depots, keeping original")
		return
	end

	for i = 0, #merged - 1 do
		new[i] = merged[i + 1]
	end

	-- Overwrite the vector in place, preserving the original grow hint.
	-- Steam keeps its own copy semantics, so the old allocation is
	-- released only after the fields are set.
	depots.mem = new
	depots.size = #merged
	depots.alloc = #merged
	depots.grow = grow

	tier0.Plat_Free(mem)
end

-- The trampoline result is what Steam must receive. Injection is
-- best-effort on top of it, so any failure returns the real package.
Downloader.hkGetPackage = ffi.cast("GetPackage_t", function(pPkgInfoCache, pkgId, a2, a3)
	local trampResult = nil

	return Downloader.runInHook("hkGetPackage", function() return trampResult end, function()
		local pPkg = Downloader.getPackageTramp(pPkgInfoCache, pkgId, a2, a3)
		trampResult = pPkg

		-- We only want to modify package 0
		if pkgId ~= 0 then
			return pPkg
		end

		-- Pkg not cached yet, abort
		if pPkg == nil then
			return pPkg
		end

		-- Nothing to inject
		if not Downloader.RewriteDepots then
			-- Still track pointer moves so a later inject hits the live object.
			if Downloader.Package ~= nil and Downloader.Package ~= pPkg then
				Downloader.safeLog("debug", "Package 0 pointer moved ("
					.. tostring(Downloader.Package) .. " -> " .. tostring(pPkg)
					.. "), re-baselining")
				Downloader.Package = nil
				Downloader.PackageDepotIds = {}
			end
			return pPkg
		end

		-- FIX 1.5: never write through a stale pointer. If Steam handed us
		-- a different PackageInfo than the one we cached, drop the old
		-- cache and re-backup from the live object.
		if Downloader.Package == nil or Downloader.Package ~= pPkg then
			if Downloader.Package ~= nil then
				Downloader.safeLog("debug", "Package 0 pointer changed ("
					.. tostring(Downloader.Package) .. " -> " .. tostring(pPkg)
					.. "), discarding stale backup of "
					.. #Downloader.PackageDepotIds .. " depots")
			end
			Downloader.Package = pPkg
			Downloader.PackageDepotIds = {}
		end

		Downloader.writeDepotIds()

		-- Only clear the flag once the write actually landed. If the
		-- package was unusable the flag stays set so the next
		-- GetPackage(0) retries.
		if Downloader.isUsablePackage(Downloader.Package) then
			local depots = Downloader.Package.depots
			Downloader.RewriteDepots = false
			Downloader.safeLog("debug", "Injected " .. depots.size .. " depots into package 0")
		end

		return pPkg
	end)
end)

-- =========================================================================
-- GetBinary
-- =========================================================================

Downloader.keyToBinary = function(depotId, key)
	if type(key) ~= "string" then
		Downloader.safeLog("error", "Invalid key type for " .. tostring(depotId))
		return nil
	end

	-- Tolerate surrounding whitespace and quotes, which YAML scalars can
	-- carry, then insist on exactly 64 hex characters.
	local trimmed = key:gsub("^%s+", ""):gsub("%s+$", "")
	trimmed = trimmed:gsub('^"', ""):gsub('"$', ""):gsub("^'", ""):gsub("'$", "")

	if #trimmed ~= 64 then
		Downloader.safeLog("error", "Invalid key length (" .. #trimmed
			.. ", expected 64) for " .. tostring(depotId))
		return nil
	end

	local bytes = {}

	for i = 1, 64, 2 do
		local sub = trimmed:sub(i, i + 1)

		-- tonumber(sub, 16) also accepts "-1", "+1" and " 1", which would
		-- silently become bogus key bytes. Require two hex digits.
		if not sub:match("^%x%x$") then
			Downloader.safeLog("error",
				"Invalid hex digit in " .. sub .. " in key for " .. tostring(depotId))
			return nil
		end

		bytes[#bytes + 1] = tonumber(sub, 16)
	end

	return bytes
end

Downloader.hkGetBinary = ffi.cast("GetBinary_t", function(pConfigStore, store, pChName, pOut, outSize)
	local trampSize = 0

	return Downloader.runInHook("hkGetBinary", function() return trampSize end, function()
		local size = Downloader.getBinaryTramp(pConfigStore, store, pChName, pOut, outSize)
		trampSize = size

		if pChName == nil or pChName == ffi.cast("void*", 0) then
			return size
		end

		if pOut == nil or pOut == ffi.cast("void*", 0) then
			Downloader.safeLog("error", "GetBinary received a null output buffer")
			return size
		end

		-- ffi.string walks a C string. A channel name that is not
		-- NUL-terminated within mapped memory will fault, and no pcall can
		-- catch that. We rely on Steam passing a proper C string here.
		local chNameStr = ffi.string(pChName)
		local nameMatch = chNameStr:match("%d+\\DecryptionKey")

		if nameMatch == nil then
			return size
		end

		local depotId = tonumber(nameMatch:match("%d+"))

		if depotId == nil then
			return size
		end

		local key = Downloader.DecryptionKeys[depotId]

		if key == nil then
			-- No key in SLSsteam config & no key in ConfigStore
			if size < 1 then
				Downloader.safeLog("warn", "Missing decryptionkey for " .. tostring(depotId))
			end

			return size
		end

		local bytes = Downloader.keyToBinary(depotId, key)

		if bytes == nil then
			return size
		end

		-- Refuse to hand back a partial key. A short key is a wrong key.
		if outSize < #bytes then
			Downloader.safeLog("error", "Output buffer for depot " .. tostring(depotId)
				.. " is " .. tostring(outSize) .. " bytes, need " .. #bytes
				.. " - skipping key injection")
			return size
		end

		Downloader.safeLog("debug", "Using config key for " .. tostring(depotId))

		for i = 0, #bytes - 1 do
			pOut[i] = bytes[i + 1]
		end

		return #bytes
	end)
end)

-- =========================================================================
-- MRC
-- =========================================================================

-- manifestId -> { value = <digits>, source = "authority"|"rescue", at = <monotonic sec> }
Downloader.MRCCache = Downloader.MRCCache or {}
-- manifestId -> monotonic sec until which every provider has failed for it
Downloader.MRCFailCache = Downloader.MRCFailCache or {}

-- Authority health. Exactly one authority; health only decides how long to
-- wait for it before reaching for the rescue path.
Downloader.MRCAuthorityHealth = Downloader.MRCAuthorityHealth or {
	ewma = nil,
	consecutiveFailures = 0,
	consecutiveProbeTimeouts = 0,
	consecutiveHealthy = 0,
	markedDownUntil = 0,
}

local function tableSize(t)
	local n = 0
	for _ in pairs(t) do
		n = n + 1
	end
	return n
end

-- Opportunistic pruning so long sessions cannot grow tables without bound.
-- Only runs when a table exceeds its cap; expired entries go first.
local function maybePruneCaches()
	local t = Downloader.MRC_TUNING
	local now = nowSec()

	if tableSize(Downloader.MRCFailCache) > (t.MRCFAILCACHE_MAX or 2000) then
		for k, until_ in pairs(Downloader.MRCFailCache) do
			if until_ <= now then
				Downloader.MRCFailCache[k] = nil
			end
		end
		-- Still over cap (all unexpired): drop oldest-ish arbitrarily.
		if tableSize(Downloader.MRCFailCache) > (t.MRCFAILCACHE_MAX or 2000) then
			local n = 0
			for k in pairs(Downloader.MRCFailCache) do
				Downloader.MRCFailCache[k] = nil
				n = n + 1
				if n > 100 then
					break
				end
			end
		end
	end

	if tableSize(Downloader.MRCCache) > (t.MRCCACHE_MAX or 5000) then
		-- Prefer evicting expired provisional entries first.
		for k, v in pairs(Downloader.MRCCache) do
			if type(v) == "table" and v.source == "rescue"
				and (now - (v.at or 0)) >= (t.PROVISIONAL_TTL or 300) then
				Downloader.MRCCache[k] = nil
			end
		end
		if tableSize(Downloader.MRCCache) > (t.MRCCACHE_MAX or 5000) then
			local n = 0
			for k in pairs(Downloader.MRCCache) do
				Downloader.MRCCache[k] = nil
				n = n + 1
				if n > 100 then
					break
				end
			end
		end
	end
end

local function authorityIsMarkedDown()
	local h = Downloader.MRCAuthorityHealth
	local now = (nowMs ~= nil and nowMs() or nil)
	if now == nil then
		-- No monotonic clock: fail open toward the authority (preferred).
		return false
	end
	return h.markedDownUntil ~= nil and h.markedDownUntil > now
end

Downloader.recordAuthorityResult = function(latencyMs, ok)
	local t = Downloader.MRC_TUNING
	local h = Downloader.MRCAuthorityHealth

	if not ok then
		h.consecutiveFailures = h.consecutiveFailures + 1
		h.consecutiveHealthy = 0

		if latencyMs ~= nil and h.ewma == nil then
			h.ewma = latencyMs
		end

		return
	end

	-- Success: a slow-but-correct answer is still authoritative, but it
	-- must NOT clear the failure state, or the probe never pays off.
	h.consecutiveFailures = 0

	if latencyMs ~= nil and latencyMs <= t.HEALTHY_MS then
		h.consecutiveHealthy = h.consecutiveHealthy + 1
		h.consecutiveProbeTimeouts = 0

		-- Fast answers clear the marked-down state without waiting for the
		-- full DOWN_TTL to lapse.
		if h.consecutiveHealthy >= t.RECOVER_AFTER then
			h.markedDownUntil = 0
			h.consecutiveHealthy = 0
			Downloader.safeLog("debug", "MRC authority recovered ("
				.. h.consecutiveHealthy .. " fast successes)")
		end
	else
		h.consecutiveHealthy = 0
	end

	if latencyMs ~= nil then
		if h.ewma == nil then
			h.ewma = latencyMs
		else
			h.ewma = t.ALPHA * latencyMs + (1 - t.ALPHA) * h.ewma
		end
	end
end

Downloader.recordAuthorityProbeTimeout = function()
	local t = Downloader.MRC_TUNING
	local h = Downloader.MRCAuthorityHealth

	h.consecutiveProbeTimeouts = h.consecutiveProbeTimeouts + 1

	if h.consecutiveProbeTimeouts >= t.PROBE_TIMEOUTS_TO_MARK_DOWN then
		local now = (nowMs ~= nil and nowMs() or nil) or 0
		h.markedDownUntil = now + t.DOWN_TTL_MS
		Downloader.safeLog("warn", "MRC authority " .. Downloader.MRC_AUTHORITY.name
			.. " marked down for " .. t.DOWN_TTL_MS .. "ms after "
			.. h.consecutiveProbeTimeouts .. " consecutive probe timeouts")
	end
end

Downloader.clearAuthorityFailures = function()
	local h = Downloader.MRCAuthorityHealth
	h.consecutiveFailures = 0
	h.consecutiveProbeTimeouts = 0
	h.markedDownUntil = 0
end

-- Accept only an all-digit, non-zero response, tolerating the JSON quoting
-- the rescue provider uses. Rejects "0", HTML challenge/error pages, and
-- anything with trailing junk. Rejects values above 2^64-1 via round-trip.
Downloader.validateMRC = function(raw)
	if raw == nil then
		return nil
	end

	local s = tostring(raw):gsub("^%s+", ""):gsub("%s+$", "")

	-- Rescue provider returns a JSON string: "17105692160592179882"
	-- Authority returns bare digits. Strip one layer of quoting if present.
	if #s >= 2 and s:sub(1, 1) == '"' and s:sub(-1) == '"' then
		s = s:sub(2, -2)
	end

	if not s:match("^%d+$") then
		return nil
	end

	-- A zero MRC means "no code"; treating it as a hit would cache a
	-- useless value and hide the real failure.
	if s:match("^0+$") then
		return nil
	end

	-- 20 digits is the widest uint64.
	if #s > 20 then
		return nil
	end

	-- Reject values that strtoull would clamp to ULL_MAX (e.g. 20x9).
	-- Round-trip through the real conversion and compare numerically.
	local num = ffi.C.strtoull(s, nil, 10)
	local back = Downloader.u64str(num)
	local norm = s:gsub("^0+", "")
	if norm == "" then
		norm = "0"
	end
	if back ~= norm then
		return nil
	end

	return s
end

-- One attempt against one provider. timeoutSec is SECONDS (curl
-- --connect-timeout). Returns validated code string, or nil plus a reason.
Downloader.fetchMRCFrom = function(provider, manifestIdStr, timeoutSec)
	local started = (nowMs ~= nil and nowMs() or nil)

	local ok, raw

	if provider.headers ~= nil and #provider.headers > 0 then
		ok, raw = pcall(curl.downloadString, provider.url .. manifestIdStr, provider.headers, timeoutSec)
	else
		ok, raw = pcall(curl.downloadString, provider.url .. manifestIdStr, timeoutSec)
	end

	local elapsed = nil

	if started ~= nil and nowMs ~= nil then
		local now = nowMs()
		if now ~= nil then
			elapsed = now - started
		end
	end

	if not ok then
		Downloader.safeLog("debug", "MRC provider " .. provider.name
			.. " raised an error: " .. tostring(raw))

		return nil, "error", elapsed
	end

	local codeStr = Downloader.validateMRC(raw)

	if codeStr == nil then
		Downloader.safeLog("debug", "MRC provider " .. provider.name
			.. " returned an unusable body for " .. manifestIdStr)

		return nil, "bad-body", elapsed
	end

	Downloader.safeLog("debug", "MRC " .. provider.name .. " answered " .. manifestIdStr
		.. " in " .. tostring(math.floor(elapsed or 0)) .. "ms")

	return codeStr, "ok", elapsed
end

Downloader.getManifestRequestCode = function(manifestId)
	local t = Downloader.MRC_TUNING
	local manifestIdStr = Downloader.u64str(manifestId)
	local now = nowSec()

	maybePruneCaches()

	-- 1. Cache. Authority entries live for AUTHORITY_TTL (then re-asked);
	-- rescue (provisional) entries live for PROVISIONAL_TTL.
	local cached = Downloader.MRCCache[manifestIdStr]

	if cached ~= nil and type(cached) == "table" and cached.value ~= nil then
		if cached.source == "authority" then
			local age = now - (cached.at or 0)
			if age < (t.AUTHORITY_TTL or 86400) then
				return ffi.C.strtoull(cached.value, nil, 10)
			end
			Downloader.safeLog("debug", "Authoritative MRC for " .. manifestIdStr
				.. " expired after " .. math.floor(age) .. "s, re-asking")
		else
			local age = now - (cached.at or 0)
			if age < t.PROVISIONAL_TTL then
				Downloader.safeLog("debug", "Using PROVISIONAL MRC for " .. manifestIdStr
					.. " from rescue (age " .. math.floor(age) .. "s of " .. t.PROVISIONAL_TTL .. "s)")
				return ffi.C.strtoull(cached.value, nil, 10)
			end
			Downloader.safeLog("debug", "Provisional MRC for " .. manifestIdStr
				.. " expired, re-asking the authority")
		end
	end

	-- 2. Negative cache.
	local failUntil = Downloader.MRCFailCache[manifestIdStr]

	if failUntil ~= nil and failUntil > now then
		Downloader.safeLog("debug",
			"MRC for " .. manifestIdStr .. " is in failure backoff, not fetching")
		return nil
	elseif failUntil ~= nil then
		Downloader.MRCFailCache[manifestIdStr] = nil
	end

	-- 3. The authority, unless temporarily marked down. Fixed order: the
	-- rescue path is never consulted before the authority has had its turn.
	local authority = Downloader.MRC_AUTHORITY

	if not authorityIsMarkedDown() then
		local budgetSec = t.FULL_SEC
		local probing = false

		if Downloader.MRCAuthorityHealth.consecutiveFailures > 0 then
			-- The authority has been struggling. Probe it briefly so the
			-- rescue path is reachable sooner. A probe that SUCCEEDS is
			-- still authoritative - slow is not wrong.
			budgetSec = t.PROBE_SEC
			probing = true
		end

		local codeStr, reason, elapsed = Downloader.fetchMRCFrom(authority, manifestIdStr, budgetSec)

		if codeStr ~= nil then
			-- FIX 1.5: no unconditional clearAuthorityFailures() here.
			-- recordAuthorityResult() alone decides recovery, so a slow
			-- success does not wipe the failure state and RECOVER_AFTER
			-- is reachable again.
			Downloader.recordAuthorityResult(elapsed, true)
			Downloader.MRCCache[manifestIdStr] = {
				value = codeStr,
				source = "authority",
				at = nowSec(),
			}
			Downloader.MRCFailCache[manifestIdStr] = nil

			if probing then
				Downloader.safeLog("debug", "Authority recovered within its probe budget")
			end

			return ffi.C.strtoull(codeStr, nil, 10)
		end

		Downloader.recordAuthorityResult(elapsed, false)

		if probing then
			-- FIX 1.5: only a stall counts as a probe timeout. A fast
			-- rejection (bad-body in 50ms) is a failure, not a timeout,
			-- and must not push the authority into marked-down.
			local budgetMs = budgetSec * 1000
			if elapsed ~= nil and elapsed >= (budgetMs - 150) then
				Downloader.recordAuthorityProbeTimeout()
			end
		end

		Downloader.safeLog("debug", "MRC authority failed (" .. tostring(reason)
			.. ") for " .. manifestIdStr)
	else
		Downloader.safeLog("debug", "MRC authority marked down, going straight to rescue")
	end

	-- 4. Rescue path. Anything it returns is provisional.
	local rescue = Downloader.MRC_RESCUE
	local rescueCode, rescueReason = Downloader.fetchMRCFrom(rescue, manifestIdStr, t.FULL_SEC)

	if rescueCode ~= nil then
		Downloader.MRCCache[manifestIdStr] = {
			value = rescueCode,
			source = "rescue",
			at = nowSec(),
		}
		Downloader.MRCFailCache[manifestIdStr] = nil

		Downloader.safeLog("warn", "MRC for " .. manifestIdStr .. " came from the rescue"
			.. " provider (" .. rescue.name .. ") - provisional for "
			.. t.PROVISIONAL_TTL .. "s until " .. authority.name .. " can confirm it")

		return ffi.C.strtoull(rescueCode, nil, 10)
	end

	Downloader.safeLog("debug", "MRC rescue failed (" .. tostring(rescueReason)
		.. ") for " .. manifestIdStr)

	-- We don't use tonumber because of precision loss
	Downloader.MRCFailCache[manifestIdStr] = nowSec() + t.FAIL_TTL
	Downloader.safeLog("error", "All MRC providers failed for manifest "
		.. manifestIdStr .. ", suppressing for " .. t.FAIL_TTL .. "s")

	return nil
end

Downloader.hkGetMRC = ffi.cast("GetMRC_t", function(a1, appId, depotId, manifestId, pChBranch, pOutMRC)
	local trampResult = false

	return Downloader.runInHook("hkGetMRC", function() return trampResult end, function()
		local success = Downloader.getMRCTramp(a1, appId, depotId, manifestId, pChBranch, pOutMRC)
		trampResult = success

		-- Don't rely on success. Check whether MRC has been written.
		if pOutMRC == nil or pOutMRC[0] ~= 0 then
			return success
		end

		local code = Downloader.getManifestRequestCode(manifestId)

		if code == nil then
			Downloader.safeLog("error",
				"Failed to get MRC for " .. Downloader.u32str(depotId))
			return success
		end

		Downloader.safeLog("debug", "Using MRC " .. Downloader.u64str(code)
			.. " for " .. Downloader.u32str(depotId))

		pOutMRC[0] = code
		return true
	end)
end)

-- =========================================================================
-- Hooking
-- =========================================================================

Downloader.getPackageHook = LuaHook("GetPackage", getPackageInfoPtr)
Downloader.getPackageTramp = ffi.cast("GetPackage_t",
	ffi.C.place_lua_hook(Downloader.getPackageHook.index, Downloader.hkGetPackage))

Downloader.getBinaryHook = LuaHook("GetBinary", configStoreGetBinary.ptr)
Downloader.getBinaryTramp = ffi.cast("GetBinary_t",
	ffi.C.place_lua_hook(Downloader.getBinaryHook.index, Downloader.hkGetBinary))

Downloader.getMRCHook = LuaHook("GetManifestRequestCode", getMRCPtr)
Downloader.getMRCTramp = ffi.cast("GetMRC_t",
	ffi.C.place_lua_hook(Downloader.getMRCHook.index, Downloader.hkGetMRC))

-- =========================================================================
-- Config
-- =========================================================================

local function countKeys(t)
	local n = 0
	for _ in pairs(t) do
		n = n + 1
	end
	return n
end

Downloader.configLoaded = function()
	Downloader.resetSettings()

	-- DepotIds
	Downloader.AdditionalDepots = SLS.config:getIntList("AdditionalDepots")
	Downloader.RewriteDepots = true -- Lazy inject, Package may still be nil

	Downloader.safeLog("debug", "Queued " .. #Downloader.AdditionalDepots
		.. " additional depots for injection")

	-- Decryptionkeys
	local keyNode = SLS.config:getNode("DecryptionKeys")

	if not keyNode.isDefined then
		log.notifyWarn("Missing DecryptionKeys in config!")
		return
	end

	local keys = keyNode:asPairList()

	for _, v in pairs(keys) do
		Downloader.DecryptionKeys[v[1]:asInt()] = v[2]:asString()
	end

	-- FIX 1.5: DecryptionKeys is a depotId-keyed map, so # is always ~0.
	-- Count with pairs.
	Downloader.safeLog("debug", "Loaded " .. countKeys(Downloader.DecryptionKeys) .. " decryption keys")
end

Downloader.luaReload = function()
	-- Write the original depot list back before dropping our cached package
	-- pointer, so a reload leaves Steam's package in the state it would
	-- have been in had we never injected.
	Downloader.writeDepotIds()
	Downloader.resetSettings()
end

-- Callbacks
SLS.registerCallback("SLSsteam::configLoaded", Downloader.configLoaded)
SLS.registerCallback("SLSsteam::luaReload", Downloader.luaReload)

log.notify("spacebunnny1.5beta.lua " .. Downloader.VERSION .. " loaded!")
