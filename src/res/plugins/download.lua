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

-- GetManifestRequestCode
Downloader.getManifestRequestCode = function(manifestId, try)
	if try > Downloader.MAX_MANIFEST_TRIES then
		return nil
	end

	local manifestCStr = ffi.new("char[?]", Downloader.MAX_MANIFEST_STRING_SIZE)
	ffi.C.snprintf(manifestCStr, Downloader.MAX_MANIFEST_STRING_SIZE, "%llu", manifestId)

	-- local url = "https://manifest.opensteamtool.com/" .. tostring(ffi.string(manifestCStr, MAX_MANIFEST_STRING_SIZE))
	-- local headers = { "User-Agent: OpenSteamTool/1.0" }
	-- local codeStr = tostring(curl.downloadString(url, headers, 5))
	local url = "http://gmrc.wudrm.com/manifest/" .. tostring(ffi.string(manifestCStr, Downloader.MAX_MANIFEST_STRING_SIZE))
	local codeStr = tostring(curl.downloadString(url, 5))
	if #codeStr < 1 then
		log.warn("Failed to download manifest request code for " .. manifestId)
	end

	-- log.debug("Downloaded MRC string " .. codeStr)

	if tonumber(codeStr) == nil then
		log.error("Invalid MRC response " .. codeStr)
		return Downloader.getManifestRequestCode(manifestId, try + 1)
	end

	-- We don't use tonumber because of precision loss
	local mrcCStr = ffi.new("char[?]", #codeStr + 1)
	ffi.copy(mrcCStr, codeStr)

	local mrc = ffi.C.strtoull(codeStr, ffi.cast("char*", 0), 10)
	return mrc
end

Downloader.hkGetMRC = ffi.cast("GetMRC_t", function(a1, appId, depotId, manifestId, pChBranch, pOutMRC)
	local mutex = LuaMutex()
	local success = Downloader.getMRCTramp(a1, appId, depotId, manifestId, pChBranch, pOutMRC)

	-- Don't rely on success. Check wheter MRC has been written
	if pOutMRC[0] ~= ffi.cast("uint64_t", 0) then
		return Downloader.mutexReturn(success, mutex)
	end

	local code = Downloader.getManifestRequestCode(manifestId, 1)
	if code == nil then
		log.error("Failed to get MRC for " .. depotId)
		return Downloader.mutexReturn(success, mutex)
	end

	local codeCStr = ffi.new("char[?]", Downloader.MAX_MANIFEST_STRING_SIZE)
	ffi.C.snprintf(codeCStr, Downloader.MAX_MANIFEST_STRING_SIZE, "%llu", code)
	log.debug("Using MRC " .. tostring(ffi.string(codeCStr) .. " for " .. depotId))

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
