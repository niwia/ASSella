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

	-- GetMRC vars
	MAX_MANIFEST_STRING_SIZE = 32,
	MAX_MANIFEST_TRIES = 5,
}

Downloader.resetSettings = function()
	-- Config stuff. Reset these on every Lua reload
	Downloader.AdditionalDepots = {}
	Downloader.DecryptionKeys = {}

end
Downloader.resetSettings()

-- Include guard, do not run multiple times
if Downloader.setup then
	return
end
Downloader.setup = true

local ffi = require("ffi")
local tier0 = ffi.load("tier0_s")

ffi.cdef[[
	void snprintf(const char*, size_t size, const char*, ...);
	unsigned long long strtoull(const char*, char*, int);

	void* Plat_Alloc(int);
	void Plat_Free(void*);

	void* place_lua_hook(const int, const void*);
	unsigned int sleep(unsigned int seconds);

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

local steamClientMod = memhlp.getModule("steamclient.so")
local getPackageInfoPtr = memhlp.getJmpTarget(memhlp.patternScan("E8 ? ? ? ? 83 C4 ? 83 78 ? ? 0F 84", steamClientMod))
local getMRCPtr = memhlp.getJmpTarget(memhlp.patternScan("E8 ? ? ? ? 83 C4 ? 83 F8 ? 0F 85 ? ? ? ? 83 EC ? 6A ? FF 75 ? E8 ? ? ? ? 83 C4 ? 88 45", steamClientMod))

local configStoreMapGetBinary = VFTableInfo_t("21IClientConfigStoreMap", "GetBinary")
if not configStoreMapGetBinary:init() then
	log.notifyError("Failed to find GetBinary!")
	return
end

local configStoreGetBinary = VFTableInfo_t("12CConfigStore", "GetBinary", configStoreMapGetBinary.index)
if not configStoreGetBinary:init() then
	log.notifyError("Failed to find GetBinary!")
	return
end

Downloader.mutexReturn = function(value, mutex)
	mutex:unlock()
	return value
end

-- GetPackage methods
Downloader.getAllDepots = function()
	-- Do not assign other table, iirc Lua creates a ref if you do
	local merged = {}

	for k, v in pairs(Downloader.PackageDepotIds) do
		table.insert(merged, v)
	end

	for k, v in pairs(Downloader.AdditionalDepots) do
		table.insert(merged, v)
	end

	return merged
end

Downloader.writeDepotIds = function()
	local pkg = Downloader.Package

	if pkg == nil then
		return
	end

	log.debug("writeDepotIds into Package " .. pkg.packageId .. " at " .. tostring(pkg))

	local depots = pkg.depots
	local mem = depots.mem
	local size = depots.size

	if #Downloader.PackageDepotIds < 1 then
		-- Back up original depot Ids
		for i = 0, size - 1 do
			local id = mem[i]
			table.insert(Downloader.PackageDepotIds, id)
			--log.debug("Original package " .. i .. " -> " .. tostring(id))
		end
	end

	local merged = Downloader.getAllDepots()
	local new = ffi.cast("uint32_t*", tier0.Plat_Alloc(4 * #merged))

	for i = 0, #merged - 1 do
		new[i] = merged[i + 1]
		--log.debug("New package " .. i .. " -> " .. tostring(new[i]))
	end

	-- Overwrite mem ptr
	depots.mem = new -- Write new mem ptr
	depots.size = #merged -- Write size
	depots.alloc = depots.size -- Useless, but just because we can

	-- -- Free old depot list
	tier0.Plat_Free(mem)
end

Downloader.hkGetPackage = ffi.cast("GetPackage_t", function(pPkgInfoCache, pkgId, a2, a3)
	local mutex = LuaMutex()
	local pPkg = Downloader.getPackageTramp(pPkgInfoCache, pkgId, a2, a3)

	-- We only want to modify package 0
	if pkgId ~= 0 then
		return Downloader.mutexReturn(pPkg, mutex)
	end

	-- Pkg not cached yet, abort
	if pPkg == nil then
		return Downloader.mutexReturn(pPkg, mutex)
	end

	-- Nothing to inject
	if not Downloader.RewriteDepots then
		return Downloader.mutexReturn(pPkg, mutex)
	end

	if Downloader.Package == nil then
		-- Catch the pkg
		Downloader.Package = pPkg
	end

	Downloader.writeDepotIds()
	Downloader.RewriteDepots = false

	return Downloader.mutexReturn(pPkg, mutex)
end)

-- GetBinary methods
Downloader.keyToBinary = function(depotId, key)
	if #key ~= 64 then
		log.error("Invalid key for " .. depotId)
		return nil
	end

	local bytes = {}

	for i = 1, #key, 2 do
		local sub = key:sub(i, i + 1)
		local byte = tonumber(sub, 16)

		if byte == nil then
			log.error("Invalid digit in " .. sub .. " in key for " .. depotId)
			return nil
		end

		table.insert(bytes, byte)
	end

	return bytes
end

Downloader.hkGetBinary = ffi.cast("GetBinary_t", function(pConfigStore, store, pChName, pOut, outSize)
	local mutex = LuaMutex()
	local size = Downloader.getBinaryTramp(pConfigStore, store, pChName, pOut, outSize)

	local nameMatch = ffi.string(pChName):match("%d+\\DecryptionKey")
	if nameMatch == nil then
		return Downloader.mutexReturn(size, mutex)
	end

	local depotId = tonumber(nameMatch:match("%d+"))
	local key = Downloader.DecryptionKeys[depotId]

	if key == nil then
		-- No key in SLSsteam config & no key in ConfigStore
		if size < 1 then
			log.warn("Missing decryptionkey for " .. depotId)
		end

		return Downloader.mutexReturn(size, mutex)
	end

	local bytes = Downloader.keyToBinary(depotId, key)

	if bytes == nil then
		return Downloader.mutexReturn(size, mutex)
	end

	log.debug("Using config key for " .. depotId)

	for i = 0, #bytes - 1 do
		pOut[i] = bytes[i + 1]
	end

	return Downloader.mutexReturn(#bytes, mutex)
end)

-- getManifestRequestCodeViaImpersonate
-- Fallback: one attempt using the bundled curl_chrome120 binary which sends a
-- real Chrome 120 TLS Client Hello. This bypasses Cloudflare's TLS fingerprint
-- filter that blocks plain libcurl requests and causes 503 responses.
--
-- Returns a uint64 MRC cdata on success, or nil on failure.
-- The binary lives in a 'bin/' directory alongside this plugin file.
Downloader.IMPERSONATE_BIN = nil  -- resolved lazily on first use

Downloader.resolveImpersonateBin = function()
	if Downloader.IMPERSONATE_BIN ~= nil then
		return Downloader.IMPERSONATE_BIN
	end

	-- Check config for an override path first
	local cfgPath = SLS.config:getString("ImpersonateBin")
	if cfgPath and #cfgPath > 0 then
		Downloader.IMPERSONATE_BIN = cfgPath
		return cfgPath
	end

	-- Default: bin/curl_chrome120 next to this plugin file
	-- __FILE__ is not available in LuaJIT, so use the well-known SLS plugin dir
	local candidate = os.getenv("HOME") .. "/.config/SLSsteam/plugins/bin/curl_chrome120"
	-- Verify it exists and is executable using a quick test
	local f = io.open(candidate, "r")
	if f then
		f:close()
		Downloader.IMPERSONATE_BIN = candidate
		return candidate
	end

	log.warn("curl_chrome120 not found at " .. candidate .. " — impersonate fallback disabled")
	Downloader.IMPERSONATE_BIN = ""  -- mark as checked, don't retry
	return nil
end

Downloader.getManifestRequestCodeViaImpersonate = function(manifestStr)
	local bin = Downloader.resolveImpersonateBin()
	if bin == nil or bin == "" then
		return nil
	end

	local cmd = bin .. " --silent --max-time 6 "
		.. "\"http://gmrc.wudrm.com/manifest/" .. manifestStr .. "\""

	local ok, body = pcall(function()
		local handle = io.popen(cmd)
		if handle == nil then return nil end
		local out = handle:read("*a")
		handle:close()
		return out
	end)

	if not ok or body == nil then
		log.warn("impersonate fallback: io.popen failed for manifest " .. manifestStr)
		return nil
	end

	local trimmed = body:match("^%s*(.-)%s*$")
	local mrcNum = tonumber(trimmed)
	if mrcNum ~= nil and mrcNum > 0 then
		local mrc = ffi.C.strtoull(trimmed, ffi.cast("char*", 0), 10)
		log.debug("impersonate fallback: got MRC for manifest " .. manifestStr)
		return mrc
	end

	log.warn("impersonate fallback: non-numeric response for manifest " .. manifestStr
		.. ": " .. (trimmed:sub(1, 60)))
	return nil
end

-- GetManifestRequestCode
-- Returns the MRC uint64 on success, or nil if all attempts exhausted.
-- Uses iterative retries with exponential backoff to avoid blocking Steam
-- inside a mutex for multiple seconds (the old recursive approach could
-- hold the lock for up to MAX_MANIFEST_TRIES * sleep_time seconds).
--
-- Per-attempt flow:
--   1. Try curl.downloadString (fast path, plain libcurl)
--   2. If body is non-numeric (Cloudflare 503 etc), try curl_chrome120 once
--      as a fallback (Chrome TLS fingerprint bypasses CF filtering)
--   3. If fallback also fails, sleep + continue to next attempt as normal
-- If all attempts are exhausted, return nil → Steam fails cleanly.
Downloader.getManifestRequestCode = function(manifestId)
	local manifestCStr = ffi.new("char[?]", Downloader.MAX_MANIFEST_STRING_SIZE)
	ffi.C.snprintf(manifestCStr, Downloader.MAX_MANIFEST_STRING_SIZE, "%llu", manifestId)
	local manifestStr = ffi.string(manifestCStr, Downloader.MAX_MANIFEST_STRING_SIZE)

	local url = "http://gmrc.wudrm.com/manifest/" .. manifestStr

	local BASE_SLEEP = 1   -- seconds between retries
	local MAX_SLEEP  = 8   -- cap backoff at 8 seconds

	for attempt = 1, Downloader.MAX_MANIFEST_TRIES do
		local codeStr = tostring(curl.downloadString(url, 5))

		if #codeStr < 1 then
			-- Empty response — network error or timeout
			log.warn("MRC fetch attempt " .. attempt .. "/" .. Downloader.MAX_MANIFEST_TRIES
				.. " returned empty body for manifest " .. manifestStr
				.. " — trying impersonate fallback")

			local mrc = Downloader.getManifestRequestCodeViaImpersonate(manifestStr)
			if mrc ~= nil then
				return mrc
			end
		else
			local mrcNum = tonumber(codeStr)

			-- Sanity check: a valid MRC is a large non-zero uint64
			if mrcNum ~= nil and mrcNum > 0 then
				-- We don't use tonumber for the final value because of precision loss
				local mrc = ffi.C.strtoull(codeStr, ffi.cast("char*", 0), 10)
				log.debug("Got MRC for manifest " .. manifestStr .. " on attempt " .. attempt)
				return mrc
			end

			-- Non-numeric body — likely an HTTP error page (503, 403, etc.)
			local errSummary = codeStr:match("<title>(.-)</title>")
				or (codeStr:len() > 60 and codeStr:sub(1, 60) .. "..." or codeStr)

			-- Detect server-side errors (5xx)
			local is5xx = codeStr:find("503") ~= nil
				or codeStr:find("Service Unavailable") ~= nil
				or codeStr:find("502") ~= nil
				or codeStr:find("500") ~= nil

			if is5xx then
				log.warn("MRC blocked/down (" .. errSummary .. ") for manifest "
					.. manifestStr .. " — attempt " .. attempt .. "/" .. Downloader.MAX_MANIFEST_TRIES
					.. ", trying impersonate fallback")
			else
				log.warn("Invalid MRC response (" .. errSummary .. ") for manifest "
					.. manifestStr .. " — attempt " .. attempt .. "/" .. Downloader.MAX_MANIFEST_TRIES
					.. ", trying impersonate fallback")
			end

			-- Try Chrome TLS impersonation fallback before sleeping
			local mrc = Downloader.getManifestRequestCodeViaImpersonate(manifestStr)
			if mrc ~= nil then
				return mrc
			end
		end

		if attempt < Downloader.MAX_MANIFEST_TRIES then
			-- Exponential backoff: 1s, 2s, 4s, 8s (capped)
			local sleepTime = math.min(BASE_SLEEP * (2 ^ (attempt - 1)), MAX_SLEEP)
			ffi.C.sleep(sleepTime)
		end
	end

	-- All attempts (plain + impersonate) failed
	log.error("MRC fetch exhausted " .. Downloader.MAX_MANIFEST_TRIES
		.. " attempts (plain + impersonate fallback) for manifest " .. manifestStr
		.. " (wudrm may be offline)")
	return nil
end

Downloader.hkGetMRC = ffi.cast("GetMRC_t", function(a1, appId, depotId, manifestId, pChBranch, pOutMRC)
	local mutex = LuaMutex()
	local success = Downloader.getMRCTramp(a1, appId, depotId, manifestId, pChBranch, pOutMRC)

	-- Native Steam already produced an MRC — nothing to do
	if pOutMRC[0] ~= ffi.cast("uint64_t", 0) then
		return Downloader.mutexReturn(success, mutex)
	end

	local code = Downloader.getManifestRequestCode(manifestId)
	if code == nil then
		-- wudrm is offline/unreachable and all retries are exhausted.
		-- Return false (the native Steam result) so Steam fails cleanly.
		-- A silent "success" with pOutMRC=0 would be worse for updates — Steam
		-- could proceed with a stale depotcache manifest and appear to succeed
		-- while the game stays on the old version.
		-- The honest failure here lets ASSella's VaporWatcher time out and keep
		-- the game as update_available so the user can retry when wudrm is back.
		log.error("MRC unavailable for depot " .. depotId
			.. " — wudrm offline, failing so Steam aborts cleanly")
		return Downloader.mutexReturn(success, mutex)
	end

	local codeCStr = ffi.new("char[?]", Downloader.MAX_MANIFEST_STRING_SIZE)
	ffi.C.snprintf(codeCStr, Downloader.MAX_MANIFEST_STRING_SIZE, "%llu", code)
	log.debug("Using MRC " .. tostring(ffi.string(codeCStr)) .. " for depot " .. depotId)

	pOutMRC[0] = code

	return Downloader.mutexReturn(true, mutex)
end)

-- Hooking
Downloader.getPackageHook = LuaHook("GetPackage", getPackageInfoPtr)
Downloader.getPackageTramp = ffi.cast("GetPackage_t", ffi.C.place_lua_hook(Downloader.getPackageHook.index, Downloader.hkGetPackage))

Downloader.getBinaryHook = LuaHook("GetBinary", configStoreGetBinary.ptr)
Downloader.getBinaryTramp = ffi.cast("GetBinary_t", ffi.C.place_lua_hook(Downloader.getBinaryHook.index, Downloader.hkGetBinary))

Downloader.getMRCHook = LuaHook("GetManifestRequestCode", getMRCPtr)
Downloader.getMRCTramp = ffi.cast("GetMRC_t", ffi.C.place_lua_hook(Downloader.getMRCHook.index, Downloader.hkGetMRC))

-- Config
Downloader.configLoaded = function()
	Downloader.resetSettings()

	-- DepotIds
	Downloader.AdditionalDepots = SLS.config:getIntList("AdditionalDepots")
	Downloader.RewriteDepots = true -- Lazy inject, Package may still be nil

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
end

Downloader.luaReload = function()
	Downloader.resetSettings()
	Downloader.writeDepotIds()
end

-- Callbacks
SLS.registerCallback("SLSsteam::configLoaded", Downloader.configLoaded)
SLS.registerCallback("SLSsteam::luaReload", Downloader.luaReload) --Only needed in debug sessions

log.notify("download.lua loaded!")
