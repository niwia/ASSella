-- SpaceBunny 2.0.5
-- Made by bakabakabaka for Assella 3.1  — Mario Test 3.6 core

local function standDown(message)
	if type(log) == "table" and type(log.error) == "function" then
		pcall(log.error, message)
	else
		print(message)
	end
end

-- SLSsteam loads direct .lua children of this directory. Runtime globals catch
-- conflicts only when the other plugin ran first; exact basenames also prevent
-- load-order-dependent double hooks. Keep this list narrow and do not match
-- backups such as .bak files. `spacebunny.lua` itself is intentionally omitted.
local KNOWN_INTERCEPTOR_FILES = {
	["download.lua"] = true,
	["spacebunny1.5.lua"] = true,
}

local function findKnownInterceptorFiles()
	local home = os.getenv("HOME") or ""
	local xdg = os.getenv("XDG_CONFIG_HOME")
	-- Mirror CConfig::getDir(): only an unset XDG_CONFIG_HOME falls back.
	if xdg == nil then xdg = home .. "/.config" end
	local pluginDir = xdg .. "/SLSsteam/plugins"
	local found = {}
	for filename in pairs(KNOWN_INTERCEPTOR_FILES) do
		local file = io.open(pluginDir .. "/" .. filename, "rb")
		if file ~= nil then
			file:close()
			found[#found + 1] = filename
		end
	end
	table.sort(found)
	return found
end

-- A plugin-file reload cannot remove native hooks. If either this plugin or
-- the prior SpaceBunny build is already active, require a full Steam restart.
-- (SpaceBunny is only an alias for DOWNLOAD_X64 further down.)
if type(SpaceBunny) == "table" and SpaceBunny ~= DOWNLOAD_X64 and SpaceBunny.setup then
	standDown("[SpaceBunny 2.0.5] SpaceBunny hooks are already active; restart Steam to replace them")
	return
end
if type(DOWNLOAD_X64) == "table" and (DOWNLOAD_X64.setup or DOWNLOAD_X64.installFailed) then
	standDown("[SpaceBunny 2.0.5] another SpaceBunny download interceptor is active; restart Steam before switching")
	return
end

-- download.lua is another interceptor. Only one may own these interfaces.
if type(Downloader) == "table" and Downloader.setup then
	standDown("[SpaceBunny 2.0.5] download.lua is active; SpaceBunny is standing down")
	return
end

local interceptorFiles = findKnownInterceptorFiles()
if #interceptorFiles > 0 then
	standDown("[SpaceBunny 2.0.5] known download interceptor file(s) in plugins directory: "
		.. table.concat(interceptorFiles, ", ") .. "; standing down")
	return
end

DOWNLOAD_X64 = {
	-- True only when every hook with a resolved signature was placed. Missing
	-- signatures and failures before native placement remain retryable.
	setup = false,
	installFailed = false,
	hookPlacementAttempted = false,
	version = "2.0.5",

	hooks = {},

	AdditionalDepots = {},
	DecryptionKeys = {},
	KeyBytes = {},
	KeyInjectCount = 0,

	MRCCache = {},
	MRCFailCache = {},
	MRCProviderBreakers = {},
	MRCCacheMax = 5000,
	MRCFailCacheMax = 2000,
	MRCInjectCount = 0,
	MRCQueries = 0,
	MRCNativeLogged = 0,

	-- LoadedProviders never changes; Providers is the live order readConfig
	-- rebuilds from it, so a repeated reload cannot wrap another provider's
	-- settings or endpoint into the wrong slot.
	LoadedProviders = {
		{ name = "wudrm", url = "https://gmrc.wudrm.com/manifest/", timeout = 4 },
		{ name = "manifestdex", url = "https://manifest.manifestdex.com/", headers = { "User-Agent: ManifestDeX/1.0" }, timeout = 5 },
	},

	RewriteDepotIds = true,
	OriginalDepotIds = nil,
	OriginalDepotsCaptured = false,
	-- depotIds size this module last wrote, used to re-arm injection when the
	-- client replaces the vector with its own.
	ExpectedDepotSize = nil,

	KeyInjection = true,
}

-- Keep the SpaceBunny identity visible to companion plugins while retaining
-- the Mario 3.6 engine table/API internally.
SpaceBunny = DOWNLOAD_X64
SpaceBunny.VERSION = DOWNLOAD_X64.version

local ffi = require("ffi")

-- Use a monotonic clock for provider cooldowns and the overall request budget.
-- Fall back to wall time only if the Linux clock API is unavailable.
pcall(ffi.cdef, [[
	typedef struct { long tv_sec; long tv_nsec; } SBClockTimespec;
	int clock_gettime(int clock_id, SBClockTimespec* tp);
]])

local function monotonicMs()
	local ok, value = pcall(function()
		local ts = ffi.new("SBClockTimespec")
		if ffi.C.clock_gettime(1, ts) ~= 0 then return nil end
		return tonumber(ts.tv_sec) * 1000 + tonumber(ts.tv_nsec) / 1000000
	end)
	if ok and value ~= nil then return value end
	return os.time() * 1000
end

local MRC_TOTAL_BUDGET_SEC = 8
local MRC_MIN_ATTEMPT_SEC = 1
local MRC_FAILURE_POLICY = {
	transient = { limit = 3, cooldown = 30 },
	junk = { limit = 2, cooldown = 300 },
}

-- PackageInfo_t ABI variants seen in the wild:
--   0x58 — current SpaceBunny build (the client this was verified against)
--   0x40 — the layout download.lua reads on other client builds
-- DepotAddress selects the offset for this session. It is read once during
-- load, because changing it under live hooks cannot be applied safely; an
-- unknown value fails closed instead of guessing.
SpaceBunny.DepotAddressMode = "0x58"
SpaceBunny.DepotAddresses = { ["0x58"] = 0x58, ["0x40"] = 0x40 }

local function readDepotAddressMode()
	local ok, value = pcall(function() return SLS.config:getString("DepotAddress", "0x58") end)
	if not ok or type(value) ~= "string" then value = "0x58" end
	local mode = value:lower():gsub("%s", "")
	return SpaceBunny.DepotAddresses[mode] and mode or nil, value
end

-- The vector is read through byte offsets rather than DLX_PackageInfo fields
-- so one build can follow either ABI.
local function depotVector(pkg)
	local raw = ffi.cast("uint8_t*", pkg)
	local off = SpaceBunny.DepotAddresses[SpaceBunny.DepotAddressMode]
	local vec = ffi.cast("DLX_UtlVector*", raw + off)
	return vec
end

local U64_MAX = ffi.cast("uint64_t", 0) - ffi.cast("uint64_t", 1)

local function isNull(p)
	if p == nil then return true end
	local ok, v = pcall(function() return tonumber(ffi.cast("uintptr_t", p)) end)
	return ok and v == 0
end

local function isGoodPtr(p)
	if isNull(p) then return false end
	local ok, v = pcall(function() return ffi.cast("uint64_t", p) end)
	return ok and v ~= U64_MAX
end

local function ptrstr(p)
	if p == nil then return "nil" end
	local ok, v = pcall(function() return tonumber(ffi.cast("uintptr_t", p)) end)
	if ok and v then return string.format("0x%x", v) end
	return tostring(p)
end

local function L(msg) pcall(log.info, "[DLX] " .. tostring(msg)) end
local function W(msg) pcall(log.warn, "[DLX] " .. tostring(msg)) end
local function E(msg) pcall(log.error, "[DLX] " .. tostring(msg)) end
local function D(msg) pcall(log.debug, "[DLX] " .. tostring(msg)) end

local statusFile = nil
local function note(msg)
	L(msg)
	pcall(function()
		if statusFile == nil then
			statusFile = (os.getenv("HOME") or "") .. "/.download_x64.log"
		end
		local f = io.open(statusFile, "a")
		if f then
			f:write(os.date("%Y-%m-%d %H:%M:%S ") .. tostring(msg) .. "\n")
			f:close()
		end
	end)
end

-- tonumber() turns a uint64 into a double and rounds past 2^53, which
-- corrupts manifest ids in logs, and printf's "%llu" is read as unsigned long
-- by the host libc. Build the decimal text from the integer instead: `%` and
-- `/` on a uint64 stay exact.
local function u64str(v)
	local n = ffi.cast("uint64_t", v)
	local digits = {}
	repeat
		digits[#digits + 1] = tonumber(n % 10)
		n = n / 10
	until n == 0
	for i = 1, math.floor(#digits / 2) do
		local j = #digits - i + 1
		digits[i], digits[j] = digits[j], digits[i]
	end
	return table.concat(digits)
end

local function tableSize(t)
	local n = 0
	for _ in pairs(t) do n = n + 1 end
	return n
end

-- Keep the in-memory caches bounded. Drop expired entries first, then
-- arbitrary entries until the cache fits. `isEntry` is true when values are
-- { code = ..., expires = ... } tables instead of bare deadlines.
local function trimCache(cache, max, now, isEntry)
	local n = tableSize(cache)
	if n <= max then return end

	for k, v in pairs(cache) do
		local expires = v
		if isEntry then expires = v.expires or 0 end
		if expires <= now then cache[k] = nil end
	end

	n = tableSize(cache)
	if n <= max then return end

	for k in pairs(cache) do
		cache[k] = nil
		n = n - 1
		if n <= max then return end
	end
end

local function validateMRC(raw, manifestId)
	if raw == nil then return nil end
	local s = tostring(raw):gsub("^%s+", ""):gsub("%s+$", "")
	local startsQuoted = s:sub(1, 1) == '"'
	local endsQuoted = s:sub(-1) == '"'
	if startsQuoted ~= endsQuoted then return nil end
	if startsQuoted then s = s:sub(2, -2) end
	if not s:match("^%d+$") or s:match("^0+$") or #s < 8 or #s > 20 then return nil end

	local normalized = s:gsub("^0+", "")
	if manifestId ~= nil and normalized == tostring(manifestId):gsub("^0+", "") then
		return nil
	end

	-- Reject uint64 overflow instead of allowing strtoull to wrap a malformed
	-- provider response into a different, apparently valid MRC.
	local parsed = ffi.C.strtoull(s, ffi.cast("char**", 0), 10)
	if u64str(parsed) ~= normalized then return nil end
	return normalized
end

local function parseKey(key)
	if type(key) ~= "string" then return nil end
	local s = key:gsub("%s", ""):gsub('"', ""):gsub("'", "")
	if #s ~= 64 or not s:match("^%x+$") then return nil end

	local bytes = ffi.new("uint8_t[32]")
	for i = 0, 31 do
		bytes[i] = tonumber(s:sub(i * 2 + 1, i * 2 + 2), 16)
	end
	return bytes
end

local function loadTier0()
	local home = os.getenv("HOME") or ""
	local candidates = {
		home .. "/.local/share/Steam/steamrt64/libtier0_s.so",
		home .. "/.steam/steam/steamrt64/libtier0_s.so",
		home .. "/.local/share/Steam/ubuntu12_64/libtier0_s.so",
		home .. "/.var/app/com.valvesoftware.Steam/data/Steam/steamrt64/libtier0_s.so",
	}

	for _, path in ipairs(candidates) do
		local f = io.open(path, "r")
		if f ~= nil then
			f:close()
			local ok, lib = pcall(ffi.load, path)
			if ok and lib ~= nil then return lib end
		end
	end

	local ok, lib = pcall(ffi.load, "libtier0_s.so")
	if ok and lib ~= nil then return lib end
	return nil
end

local function main()
	note("=== SpaceBunny " .. DOWNLOAD_X64.version .. " loading ===")

	-- Select the PackageInfo_t ABI before anything follows a package pointer.
	-- This must happen at load: changing it under live hooks is unsafe.
	local mode, rawMode = readDepotAddressMode()
	if mode == nil then
		E("unknown DepotAddress " .. tostring(rawMode) .. " (expected 0x58 or 0x40), aborting")
		return false
	end
	SpaceBunny.DepotAddressMode = mode

	if not ffi.abi("64bit") then
		E("not a 64-bit process, aborting")
		return false
	end

	if memhlp == nil or memhlp.getModule == nil then
		E("memhlp API missing, aborting")
		return false
	end

	if LuaHook == nil or LuaMutex == nil then
		E("LuaHook/LuaMutex missing, aborting")
		return false
	end

	pcall(ffi.cdef, [[ void* place_lua_hook(const int, const void*); ]])
	pcall(ffi.cdef, [[ unsigned long long strtoull(const char*, char**, int); ]])
	pcall(ffi.cdef, [[ void* Plat_Alloc(size_t); ]])
	pcall(ffi.cdef, [[ void* Plat_Realloc(void*, size_t); ]])
	pcall(ffi.cdef, [[ void Plat_Free(void*); ]])
	pcall(ffi.cdef, [[
		typedef struct { uint32_t* mem; uint32_t alloc; uint32_t grow; uint32_t size; } DLX_UtlVector;
	]])
	pcall(ffi.cdef, [[
		typedef struct { uint32_t packageId; uint8_t __pad0x0[0x3C]; DLX_UtlVector appIds; DLX_UtlVector depotIds; } DLX_PackageInfo;
	]])
	pcall(ffi.cdef, [[ typedef DLX_PackageInfo* (*DLX_GetPackage_t)(void*, uint32_t, long); ]])
	pcall(ffi.cdef, [[ typedef int (*DLX_GetBinary_t)(void*, uint32_t, const char*, int8_t*, uint32_t); ]])
	pcall(ffi.cdef, [[ typedef bool (*DLX_GetMRC_t)(void*, uint32_t, uint32_t, uint64_t, const char*, uint64_t*); ]])

	local tier0 = loadTier0()
	if tier0 == nil then
		W("libtier0_s.so not found; injection disabled")
	end

	local mod = memhlp.getModule("steamclient.so") or memhlp.getModule("steamclient64.so")
	if mod == nil then
		E("steamclient module not found")
		return false
	end

	local function resolve(label, pattern)
		local ok, scan = pcall(memhlp.patternScan, pattern, mod)
		if not ok or not isGoodPtr(scan) then
			E(label .. " pattern not found")
			return nil
		end
		local ok2, target = pcall(memhlp.getJmpTarget, scan)
		if not ok2 or not isGoodPtr(target) then
			E(label .. " getJmpTarget failed")
			return nil
		end
		note(label .. " pattern at " .. ptrstr(scan) .. " -> " .. ptrstr(target))
		return target
	end

	-- Dev-confirmed patterns
	local pkgPtr = resolve("GetPackage", "E8 ? ? ? ? 83 78 18 03 0F 84")
	local mrcPtr = resolve("GetMRC", "E8 ? ? ? ? 83 F8 ? 0F 85 ? ? ? ? 31 F6")
	local pkgSignatureFound = pkgPtr ~= nil
	if pkgPtr ~= nil and tier0 == nil then
		W("GetPackage signature found, but depot merging is disabled without libtier0_s.so")
		pkgPtr = nil
	end

	local binPtr = nil
	if VFTableInfo_t ~= nil then
		pcall(function()
			local mapInfo = VFTableInfo_t("21IClientConfigStoreMap", "GetBinary")
			if not mapInfo:init() then
				W("IClientConfigStoreMap::GetBinary not found")
				return
			end
			local storeInfo = VFTableInfo_t("12CConfigStore", "GetBinary", mapInfo.index)
			if storeInfo:init() and isGoodPtr(storeInfo.ptr) then
				binPtr = storeInfo.ptr
				note("CConfigStore::GetBinary at " .. ptrstr(storeInfo.ptr) .. " (index " .. tostring(mapInfo.index) .. ")")
			else
				W("CConfigStore::GetBinary not found")
			end
		end)
	else
		W("VFTableInfo_t API missing")
	end

	local function providerOrder()
		local names = {}
		for _, provider in ipairs(DOWNLOAD_X64.Providers or DOWNLOAD_X64.LoadedProviders) do
			names[#names + 1] = provider.name
		end
		return #names > 0 and table.concat(names, ">") or "none"
	end

	local function providerTimeout(value, fallback, name)
		local timeout = tonumber(value)
		if timeout == nil or timeout ~= timeout or timeout ~= math.floor(timeout)
			or timeout < 1 or timeout > 15 then
			if value ~= nil then W("invalid MRC timeout for " .. name .. "; using default") end
			return fallback
		end
		return timeout
	end

	local function providerHeaders(value, fallback)
		local source = value
		if source == nil then source = fallback end
		if type(source) ~= "table" then return nil end
		local headers = {}
		for _, header in ipairs(source) do
			if type(header) == "string" and not header:find("[%c]")
				and header:match("^[%w%-]+%s*:") then
				headers[#headers + 1] = header
			end
		end
		return #headers > 0 and headers or nil
	end

	local function readProviders()
		local cfg = nil
		if type(KATAMARI) == "table" and type(KATAMARI.mother) == "table"
			and type(KATAMARI.mother.spacebunny) == "table" then
			cfg = KATAMARI.mother.spacebunny.mrc
		end
		if type(cfg) ~= "table" then
			local home = os.getenv("HOME") or ""
			local xdg = os.getenv("XDG_CONFIG_HOME")
			if xdg == nil or xdg == "" then xdg = home .. "/.config" end
			local ok, value = pcall(dofile, xdg .. "/SLSsteam/katamari.conf.lua")
			if ok and type(value) == "table" and type(value.spacebunny) == "table" then
				cfg = value.spacebunny.mrc
			end
		end
		if type(cfg) ~= "table" or type(cfg.providers) ~= "table" then
			return DOWNLOAD_X64.LoadedProviders
		end
		local byName = {}
		for _, entry in ipairs(cfg.providers) do
			if type(entry) == "table" and type(entry.name) == "string"
				and type(entry.url) == "string"
				and entry.url:match("^https://[^/%?#]+")
				and not entry.url:find("[%c%s?#]") then
				byName[entry.name:lower()] = entry
			end
		end
		local providers = {}
		local defaults = DOWNLOAD_X64.LoadedProviders or DOWNLOAD_X64.Providers
		for i, name in ipairs({ "wudrm", "manifestdex" }) do
			local configured = byName[name]
			local fallback = defaults[i]
			local entry = configured or fallback
			local headers = providerHeaders(entry.headers, fallback.headers)
			providers[#providers + 1] = {
				name = name, url = entry.url,
				headers = headers,
				timeout = providerTimeout(entry.timeout, fallback.timeout, name),
			}
		end
		return providers
	end

	local function readConfig(tag)
		local depots = {}
		local keys = {}

		-- SLSsteam reports an absent key as any of nil, "" or an empty list
		-- and lists it as a load problem; accept all three as "none".
		-- A key missing from config.yaml makes SLSsteam record a load error and
		-- report a non-table here. Treat that as "no depots", but say so once
		-- instead of silently swallowing a config key that is meant to exist.
		local okList, list = pcall(function() return SLS.config:getIntList("AdditionalDepots") end)
		if not okList then
			W("getIntList(AdditionalDepots) failed: " .. tostring(list))
		elseif type(list) ~= "table" then
			W("AdditionalDepots is not a list (got " .. type(list)
				.. "); SLSsteam probably could not parse the key")
		elseif #list > 0 then
			local seen = {}
			for _, v in ipairs(list) do
				local id = tonumber(v)
				if id ~= nil and id > 0 and id <= 0xFFFFFFFF and id == math.floor(id) and not seen[id] then
					depots[#depots + 1] = id
					seen[id] = true
				elseif id ~= nil then
					W("ignoring invalid AdditionalDepots entry " .. tostring(v))
				end
			end
		end


		local okNode, node = pcall(function() return SLS.config:getNode("DecryptionKeys") end)
		if not okNode then
			W("getNode(DecryptionKeys) failed: " .. tostring(node))
		elseif node ~= nil and node.isDefined then
			local okPairs, pairs_ = pcall(function() return node:asPairList() end)
			if okPairs and pairs_ ~= nil then
				for _, kv in ipairs(pairs_) do
					local okEntry, id, key = pcall(function() return tonumber(kv[1]:asInt()), kv[2]:asString() end)
					if okEntry and id ~= nil and id > 0 and id <= 0xFFFFFFFF and type(key) == "string" and parseKey(key) ~= nil then
						keys[id] = key
					else
						W("ignoring malformed DecryptionKeys entry")
					end
				end
			end
		end

		DOWNLOAD_X64.AdditionalDepots = depots
		DOWNLOAD_X64.DecryptionKeys = keys
		DOWNLOAD_X64.KeyBytes = {}
		DOWNLOAD_X64.Providers = readProviders()
		local providerCount = #DOWNLOAD_X64.Providers
		DOWNLOAD_X64.MRCQueries = 0
		DOWNLOAD_X64.MRCNativeLogged = 0

		DOWNLOAD_X64.RewriteDepotIds = true


		note(tag .. ": depots=" .. #depots
			.. " DecryptionKeys=" .. tostring(tableSize(keys))
			.. " MRCProviders=" .. tostring(providerCount) .. "(" .. providerOrder() .. ")"
			.. " tier0=" .. tostring(tier0 ~= nil))
	end

	local function captureOriginalDepots(pkg, vec)
		DOWNLOAD_X64.OriginalDepotIds = {}

		for i = 0, vec.size - 1 do
			local id = tonumber(vec.mem[i])
			if id ~= nil and id > 0 then
				DOWNLOAD_X64.OriginalDepotIds[#DOWNLOAD_X64.OriginalDepotIds + 1] = id
			end
		end

		DOWNLOAD_X64.OriginalDepotsCaptured = true
		note("captured " .. #DOWNLOAD_X64.OriginalDepotIds .. " original depotIds from package 0")
	end

	local function writeDepotIds(pkg)
		if tier0 == nil or pkg == nil then return false end
		if tonumber(pkg.packageId) ~= 0 then return false end

		local vec = depotVector(pkg)
		local size = tonumber(vec.size) or 0
		local alloc = tonumber(vec.alloc) or 0
		local grow = tonumber(vec.grow) or 0
		local memOk = vec.mem ~= nil and not isNull(vec.mem)

		-- The package id is at +0x00 in both ABI variants, so a mismatch here
		-- means the vector offset is wrong for this client, not that another
		-- package was rewritten. Refuse to follow it.
		if grow > 0x40 then
			W("depotIds at 0x" .. string.format("%x", SpaceBunny.DepotAddresses[SpaceBunny.DepotAddressMode])
				.. " looks implausible (grow=" .. tostring(grow) .. "); refusing to write")
			return false
		end
		if size > 0x10000 then
			W("implausible depotId count " .. tostring(size) .. ", refusing to write")
			return false
		end
		if size > 0 and not memOk then
			W("depotIds vector has size " .. tostring(size) .. " but no memory, refusing to write")
			return false
		end
		if size > 0 and alloc < size then
			W("depotIds vector alloc < size, refusing to write")
			return false
		end

		-- getPackageTramp reuses the client's result object, so neither a
		-- fresh PackageInfo nor a fresh vector proves a genuine client
		-- change: the client can rebuild its original depot list at any time
		-- (refresh, new ownership). The original list is captured once per
		-- process; a later capture would keep this module's own injections
		-- alive after the user removed them from config.
		if not DOWNLOAD_X64.OriginalDepotsCaptured then
			captureOriginalDepots(pkg, vec)
		end

		local merged = {}
		local seen = {}

		local function add(id)
			id = tonumber(id)
			if id ~= nil and id > 0 and not seen[id] then
				seen[id] = true
				merged[#merged + 1] = id
			end
		end

		for _, id in ipairs(DOWNLOAD_X64.OriginalDepotIds) do add(id) end
		for _, id in ipairs(DOWNLOAD_X64.AdditionalDepots) do add(id) end

		if #merged == 0 or #merged > 0x10000 then
			W("refusing to write " .. tostring(#merged) .. " depotIds")
			return false
		end

		local same = size == #merged and memOk
		if same then
			for i = 0, #merged - 1 do
				if tonumber(vec.mem[i]) ~= merged[i + 1] then
					same = false
					break
				end
			end
		end
		if same then
			DOWNLOAD_X64.RewriteDepotIds = false
			return true
		end

		local newMem = ffi.cast("uint32_t*", tier0.Plat_Alloc(4 * #merged))
		if not isGoodPtr(newMem) or isNull(newMem) then
			W("Plat_Alloc failed for " .. #merged .. " depotIds, keeping original")
			return false
		end

		for i = 0, #merged - 1 do
			newMem[i] = merged[i + 1]
		end

		local oldMem = vec.mem
		vec.mem = newMem
		vec.alloc = #merged
		vec.size = #merged
		-- Keep the native grow rate across the swap, as spacebunny.lua does:
		-- a replaced mem with a stale grow makes the next client insert
		-- reallocate from the wrong capacity.
		vec.grow = grow

		DOWNLOAD_X64.RewriteDepotIds = false
		DOWNLOAD_X64.ExpectedDepotSize = #merged

		note("injected " .. #merged .. " depotIds into package 0 (original "
			.. #DOWNLOAD_X64.OriginalDepotIds .. ", added "
			.. #DOWNLOAD_X64.AdditionalDepots .. ", grow " .. tostring(grow)
			.. ", old mem " .. ptrstr(oldMem)
			.. " -> " .. ptrstr(newMem) .. ")")
		return true
	end

	local function providerKey(provider)
		return tostring(provider.name) .. "|" .. tostring(provider.url)
	end

	local function providerSkipRemaining(provider, nowMs)
		local key = providerKey(provider)
		local state = DOWNLOAD_X64.MRCProviderBreakers[key]
		if state == nil or state.skipUntil == nil then return 0 end
		if state.skipUntil > nowMs then
			return math.ceil((state.skipUntil - nowMs) / 1000)
		end
		DOWNLOAD_X64.MRCProviderBreakers[key] = nil
		return 0
	end

	local function recordProviderFailure(provider, kind)
		local policy = MRC_FAILURE_POLICY[kind] or MRC_FAILURE_POLICY.transient
		local key = providerKey(provider)
		local state = DOWNLOAD_X64.MRCProviderBreakers[key]
		if state == nil then state = { count = 0, kind = kind } end
		if state.kind ~= kind then
			state.count = 0
			state.kind = kind
		end
		state.count = state.count + 1
		if state.count >= policy.limit then
			state.count = 0
			state.skipUntil = monotonicMs() + policy.cooldown * 1000
			W("MRC provider " .. provider.name .. " circuit broken after "
				.. policy.limit .. " " .. kind .. " failures; skipping for "
				.. policy.cooldown .. "s")
		end
		DOWNLOAD_X64.MRCProviderBreakers[key] = state
	end

	local function recordProviderSuccess(provider)
		DOWNLOAD_X64.MRCProviderBreakers[providerKey(provider)] = nil
	end

	local function classifyProviderFailure(raw, elapsedMs, timeoutSec, callOk)
		if not callOk then return "transient", "curl-error" end
		if type(raw) ~= "string" or raw == "" then
			if elapsedMs >= timeoutSec * 900 then return "transient", "timeout" end
			return "transient", "empty-response"
		end
		local head = raw:sub(1, 256):lower()
		if head:find("<!doctype") or head:find("<html") or head:find("<head") then
			return "junk", "html-response"
		end
		local status = raw:match("^%s*error code:%s*(%d%d%d)%s*$")
			or raw:match("^%s*(%d%d%d)%s*$")
		if status ~= nil then
			local code = tonumber(status)
			if code >= 500 then return "transient", "http-5xx" end
			if code >= 400 then return "junk", "http-4xx" end
		end
		return "junk", "invalid-response"
	end

	local function fetchMRC(manifestId, context)
		local key = u64str(manifestId)
		local now = os.time()

		local cached = DOWNLOAD_X64.MRCCache[key]
		if cached ~= nil and cached.expires > now then
			if type(context) == "string" then
				D("MRC " .. key .. " from cache (via " .. tostring(cached.via) .. ") for " .. context)
			end
			return cached.code
		end

		local failed = DOWNLOAD_X64.MRCFailCache[key]
		if failed ~= nil and failed > now then
			return nil
		end

		local deadline = monotonicMs() + MRC_TOTAL_BUDGET_SEC * 1000
		local attempted = false
		local report = {}
		local providers = DOWNLOAD_X64.Providers

		for i, provider in ipairs(providers) do
			local nowMs = monotonicMs()
			local skipped = providerSkipRemaining(provider, nowMs)
			if skipped > 0 then
				report[#report + 1] = provider.name .. "=circuit-open(" .. skipped .. "s)"
				D("MRC provider " .. provider.name .. " is circuit-broken, skipping (" .. skipped .. "s left)")
			else
				local activeAfter = 0
				for j = i + 1, #providers do
					if providerSkipRemaining(providers[j], nowMs) == 0 then
						activeAfter = activeAfter + 1
					end
				end
				local remaining = (deadline - nowMs) / 1000
				local timeout = math.floor(math.min(provider.timeout,
					remaining - activeAfter * MRC_MIN_ATTEMPT_SEC))
				if timeout < MRC_MIN_ATTEMPT_SEC then
					report[#report + 1] = provider.name .. "=no-budget"
				else
					attempted = true
					-- Append a manifest ID to either a bare host or a full endpoint path.
					local url = provider.url:gsub("/+$", "") .. "/" .. key
					local started = monotonicMs()
					local ok, raw = pcall(function()
						if type(curl) ~= "table" or type(curl.downloadString) ~= "function" then
							error("curl.downloadString is unavailable")
						end
						if provider.headers ~= nil then
							return curl.downloadString(url, provider.headers, timeout)
						end
						return curl.downloadString(url, timeout)
					end)
					local elapsed = math.max(0, monotonicMs() - started)
					local codeStr = ok and validateMRC(raw, key) or nil
					if type(context) == "string" then
						D("MRC " .. key .. " for " .. context .. ": "
							.. (codeStr ~= nil and "provider " .. provider.name .. " answered"
								or "provider " .. provider.name .. " returned no usable code"))
					end

					if codeStr ~= nil then
						recordProviderSuccess(provider)
						local code = ffi.C.strtoull(codeStr, ffi.cast("char**", 0), 10)
						if tableSize(DOWNLOAD_X64.MRCCache) >= DOWNLOAD_X64.MRCCacheMax then
							trimCache(DOWNLOAD_X64.MRCCache, DOWNLOAD_X64.MRCCacheMax, now, true)
						end
						DOWNLOAD_X64.MRCCache[key] = { code = code, expires = now + 3600, via = provider.name }
						return code
					end

					local kind, reason = classifyProviderFailure(raw, elapsed, timeout, ok)
					recordProviderFailure(provider, kind)
					report[#report + 1] = provider.name .. "=" .. reason
				end
			end
		end

		if attempted then
			if tableSize(DOWNLOAD_X64.MRCFailCache) >= DOWNLOAD_X64.MRCFailCacheMax then
				trimCache(DOWNLOAD_X64.MRCFailCache, DOWNLOAD_X64.MRCFailCacheMax, now, false)
			end
			DOWNLOAD_X64.MRCFailCache[key] = now + 120
			-- Notify only after a real provider attempt; an open breaker already
			-- reported its outage and should not create a popup for every request.
			local msg = "MRC providers failed for manifest " .. key .. ": " .. table.concat(report, ", ")
			W(msg)
			if type(log) == "table" and type(log.notifyWarn) == "function" then
				pcall(log.notifyWarn, msg)
			end
		else
			D("MRC providers unavailable without a request attempt for manifest "
				.. key .. ": " .. table.concat(report, ", "))
		end
		return nil
	end

	local function installHook(label, target, ffiType, callback, refField)
		if target ~= nil then
			DOWNLOAD_X64.hooksWanted = (DOWNLOAD_X64.hooksWanted or 0) + 1
		end
		local okHook, hook = pcall(LuaHook, label, target)
		if not okHook or hook == nil then
			E(label .. ": LuaHook failed: " .. tostring(hook))
			return nil
		end

		DOWNLOAD_X64[refField] = callback

		-- Look the symbol up here and call it as an unbound function. Older
		-- LuaJIT builds lose a setmetatable mock on a dotted call, which made
		-- a forced failure in the harness silently place a real hook instead.
		local okPlace, raw = pcall(function()
			local placeHook = ffi.C.place_lua_hook
			DOWNLOAD_X64.hookPlacementAttempted = true
			return placeHook(hook.index, callback)
		end)
		if not okPlace or isNull(raw) then
			E(label .. ": place_lua_hook failed: " .. tostring(raw))
			return nil
		end

		-- Record a hook only once it is actually placed; a hook object without
		-- a trampoline is not an installed hook.
		DOWNLOAD_X64.hooks[label] = hook
		note(label .. ": hook placed, index=" .. tostring(hook.index) .. " tramp=" .. ptrstr(raw))
		return ffi.cast(ffiType, raw)
	end

	-- Lua errors inside an FFI callback must not escape into Steam's Lua API.
	-- Keep the original function's result as the fallback and always attempt
	-- to release the global hook mutex, matching SpaceBunny's runInHook policy.
	-- KNOWN LIMITATION: SLSsteam's LuaMutex destructor unlocks even after this
	-- explicit release, and the object becomes unreachable when the callback
	-- returns, so the later GC finalizer can unlock a mutex this hook no longer
	-- owns (or release an outer recursive lock too early). The finalizer runs
	-- when Lua itself runs, not inside the native hook, so the risk is a
	-- wrong-depth unlock on Steam's shared Lua mutex, not execution there.
	-- Omitting this release would instead retain the shared lock until
	-- nondeterministic GC. The error fallback runs before this explicit release
	-- to keep its Lua work serialized; this does not fix the LuaMutex lifetime
	-- bug. Trampoline guards only narrow a race; pcall cannot catch native
	-- termination. See styx.md for details.
	local function runInHook(label, fallback, fn, failureDefault)
		local mutex = nil
		local result

		local ok, err = pcall(function()
			mutex = LuaMutex()
			result = fn()
		end)

		if not ok then
			W(label .. " hook error; returning native result: " .. tostring(err))
			local fallbackOk, fallbackResult = pcall(fallback)
			if fallbackOk then
				result = fallbackResult
			else
				W(label .. " fallback failed: " .. tostring(fallbackResult))
				result = failureDefault
			end
		end

		if mutex ~= nil then
			local unlocked, unlockErr = pcall(function()
				mutex:unlock()
			end)
			if not unlocked then
				W(label .. " mutex unlock failed: " .. tostring(unlockErr))
			end
		end
		return result
	end

	if pkgPtr ~= nil then
		local getPackageTramp = nil
		local callback = ffi.cast("DLX_GetPackage_t", function(cache, pkgId, third)
			if getPackageTramp == nil then return nil end
			local result = nil
			local called = false
			local function original()
				if not called then
					called = true
					result = getPackageTramp(cache, pkgId, third)
				end
				return result
			end

			return runInHook("GetPackage", original, function()
				original()

				if tonumber(pkgId) == 0 and result ~= nil and not isNull(result) then
					if DOWNLOAD_X64.RewriteDepotIds then
						local ok, err = pcall(writeDepotIds, result)
						if not ok then
							W("depotIds injection error: " .. tostring(err))
						end
					elseif DOWNLOAD_X64.ExpectedDepotSize ~= nil then
						-- ExpectedDepotSize is a count; re-read the vector size.
						local okDepot, dsize = pcall(function()
							return tonumber(depotVector(result).size)
						end)
						if okDepot and dsize ~= DOWNLOAD_X64.ExpectedDepotSize then
							DOWNLOAD_X64.RewriteDepotIds = true
							note("re-arming depotId injection: depotIds vector size changed")

						end
					end
				end
				return result
			end, nil)
		end)

		getPackageTramp = installHook(
			"DLX_GetPackage", pkgPtr, "DLX_GetPackage_t",
			callback, "hkGetPackage")
		DOWNLOAD_X64.getPackageTramp = getPackageTramp
	end

	if binPtr ~= nil then
		-- The vtable hook installs by slot index, so it is labelled by its
		-- interface name; the arming check below looks it up by this key.
		local getBinaryTramp = nil
		local callback = ffi.cast("DLX_GetBinary_t", function(configStore, store, pChName, pOut, outSize)
			if getBinaryTramp == nil then return 0 end
			local size = 0
			local called = false
			local function original()
				if not called then
					called = true
					size = getBinaryTramp(configStore, store, pChName, pOut, outSize)
				end
				return size
			end

			return runInHook("GetBinary", original, function()
				original()
				local nativeSize = size

				if DOWNLOAD_X64.KeyInjection and not isNull(pChName) then
					local channel = ffi.string(pChName)
					local match = channel:match("(%d+)[\\/]DecryptionKey")

					if match ~= nil then
						local depot = tonumber(match)
						local bytes = DOWNLOAD_X64.KeyBytes[depot]

						if bytes == nil then
							local key = DOWNLOAD_X64.DecryptionKeys[depot]
							if key ~= nil then
								bytes = parseKey(key)
								if bytes ~= nil then
									DOWNLOAD_X64.KeyBytes[depot] = bytes
								else
									W("invalid decryption key for depot " .. tostring(depot))
								end
							end
						end

						if bytes then
							if isNull(pOut) then
								size = 32
							elseif outSize >= 32 then
								ffi.copy(pOut, bytes, 32)
								size = 32
								DOWNLOAD_X64.KeyInjectCount = DOWNLOAD_X64.KeyInjectCount + 1
								if DOWNLOAD_X64.KeyInjectCount <= 20 then
									note("injected key for depot " .. tostring(depot)
										.. " (native size " .. tostring(nativeSize)
										.. ", #" .. tostring(DOWNLOAD_X64.KeyInjectCount) .. ")")
								end
							else
								W("output buffer too small for depot " .. tostring(depot)
									.. ": " .. tostring(outSize))
							end
						end
					end
				end
				return size
			end, 0)
		end)

		getBinaryTramp = installHook(
			"DLX_GetBinary", binPtr, "DLX_GetBinary_t",
			callback, "hkGetBinary")
		DOWNLOAD_X64.getBinaryTramp = getBinaryTramp
	end

	if mrcPtr ~= nil then
		local getMRCTramp = nil
		local callback = ffi.cast("DLX_GetMRC_t", function(a1, appId, depotId, manifestId, branch, pOutMRC)
			if getMRCTramp == nil then return false end
			local success = false
			local called = false
			local function original()
				if not called then
					called = true
					success = getMRCTramp(a1, appId, depotId, manifestId, branch, pOutMRC)
				end
				return success
			end

			return runInHook("GetMRC", original, function()
				original()

				if manifestId ~= 0 and not isNull(pOutMRC) then
					local nativeCode = pOutMRC[0]
					local zero = (nativeCode == 0)

					if zero then
						DOWNLOAD_X64.MRCQueries = DOWNLOAD_X64.MRCQueries + 1
						-- Name the app and depot: a bare manifest id cannot be
						-- traced back to a game from a log.
						local branchName = "unknown"
						if not isNull(branch) then
							local okBranch, value = pcall(ffi.string, branch)
							if okBranch and value ~= "" then branchName = value end
						end
						local context = "app=" .. tostring(appId) .. " depot=" .. tostring(depotId)
							.. " manifest=" .. u64str(manifestId)
							.. " branch=" .. branchName
						local code = fetchMRC(manifestId, context)

						if code ~= nil then
							pOutMRC[0] = code
							success = true
							DOWNLOAD_X64.MRCInjectCount = DOWNLOAD_X64.MRCInjectCount + 1

							if DOWNLOAD_X64.MRCInjectCount <= 20 then
								note("MRC " .. context
									.. " native=" .. u64str(nativeCode)
									.. " injected=" .. u64str(code)
									.. " via=" .. tostring(DOWNLOAD_X64.MRCCache[u64str(manifestId)].via))
							end
						end
					else
						-- Log a few native answers per reload, not a handful for
						-- the whole session. Counted separately from zero-hit
						-- queries so cache-miss traffic cannot use up the cap.
						if DOWNLOAD_X64.MRCNativeLogged < 10 then
							DOWNLOAD_X64.MRCNativeLogged = DOWNLOAD_X64.MRCNativeLogged + 1
							D("GetMRC manifest=" .. u64str(manifestId)
								.. " native=" .. u64str(nativeCode) .. " (non-zero, not overriding)")
						end
					end
				end
				return success
			end, false)
		end)

		getMRCTramp = installHook(
			"DLX_GetMRC", mrcPtr, "DLX_GetMRC_t",
			callback, "hkGetMRC")
		DOWNLOAD_X64.getMRCTramp = getMRCTramp
	end

	-- Say when a working hook was skipped because of the offset, rather than
	-- only when the signature was missing: a planted package 0 under the wrong
	-- offset is worse than no hook, but it should never fail in silence.
	if binPtr ~= nil and not pkgSignatureFound then
		W("GetBinary hook live without depot merging: GetPackage signature not found"
			.. " for depotIds@" .. SpaceBunny.DepotAddressMode
			.. " (try DepotAddress: 0x40 if this client uses the old layout)")
	elseif binPtr ~= nil and pkgSignatureFound and tier0 == nil then
		W("GetBinary hook live without depot merging: libtier0_s.so allocator unavailable")
	end

	local okCfg, cfgErr = pcall(readConfig, "load")
	if not okCfg then
		W("readConfig failed: " .. tostring(cfgErr))
	end

	if SLS ~= nil and SLS.registerCallback ~= nil then
		SLS.registerCallback("SLSsteam::configLoaded", function()
			pcall(readConfig, "configLoaded")
		end)

		SLS.registerCallback("SLSsteam::initialized", function()
			note("initialized: injectedKeys=" .. tostring(DOWNLOAD_X64.KeyInjectCount)
				.. " mrcOverrides=" .. tostring(DOWNLOAD_X64.MRCInjectCount))
		end)

		SLS.registerCallback("SLSsteam::luaReload", function()
			pcall(readConfig, "luaReload")
			note("luaReload: injectedKeys=" .. tostring(DOWNLOAD_X64.KeyInjectCount)
				.. " mrcOverrides=" .. tostring(DOWNLOAD_X64.MRCInjectCount))
		end)
	end

	local function hookLive(label, field)
		return tostring(DOWNLOAD_X64[field] ~= nil and DOWNLOAD_X64.hooks[label] ~= nil)
	end


	note("=== armed: GetPackage=" .. hookLive("DLX_GetPackage", "getPackageTramp")
		.. " GetBinary=" .. hookLive("DLX_GetBinary", "getBinaryTramp")
		.. " GetMRC=" .. hookLive("DLX_GetMRC", "getMRCTramp")
		.. " tier0=" .. tostring(tier0 ~= nil)
		.. " MRCProviders=" .. tostring(#DOWNLOAD_X64.Providers) .. "(" .. providerOrder() .. ")"
		.. " ===")

	note("=== SpaceBunny " .. DOWNLOAD_X64.version .. " summary (depotIds@"
		.. SpaceBunny.DepotAddressMode .. ") ==="
		.. " depots=" .. #DOWNLOAD_X64.AdditionalDepots
		.. " keys=" .. tableSize(DOWNLOAD_X64.DecryptionKeys)
		.. " ===")

	return true
end

local ok, armed = pcall(main)
if not ok then
	E("fatal: " .. tostring(armed))
	-- A failure before touching native hooks is safe to retry. Once a hook was
	-- placed or attempted, we cannot prove the native state is clean.
	if DOWNLOAD_X64.hookPlacementAttempted then
		DOWNLOAD_X64.installFailed = true
	end
elseif armed and not DOWNLOAD_X64.installFailed
	and DOWNLOAD_X64.hookPlacementAttempted
	and tableSize(DOWNLOAD_X64.hooks) ~= (DOWNLOAD_X64.hooksWanted or 0) then
	-- A hook that was found but failed to place leaves native state that cannot
	-- be removed, so do not let a hot reload try to install it again. Hooks
	-- whose signature was simply absent are expected and stay retryable.
	DOWNLOAD_X64.installFailed = true
	E("only " .. tableSize(DOWNLOAD_X64.hooks) .. " of "
		.. tostring(DOWNLOAD_X64.hooksWanted) .. " required hooks placed; restart Steam before retrying")
elseif armed and not DOWNLOAD_X64.installFailed then
	local placed = tableSize(DOWNLOAD_X64.hooks)
	local wanted = DOWNLOAD_X64.hooksWanted or 0
	DOWNLOAD_X64.setup = placed > 0 and placed == wanted
	if not DOWNLOAD_X64.setup then
		if placed == 0 then
			W("no hooks were placed; safe to retry after correcting the client/API issue")
		else
			W("only " .. placed .. " of " .. tostring(wanted)
				.. " hook objects initialized; no native placement failure, safe to retry")
		end
	end
end
