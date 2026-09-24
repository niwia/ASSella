-- ASSella Bridge Plugin for SLSsteam
-- Enables two-way communication between ASSella and SLSsteam
-- Completely inert with zero CPU usage when ASSella is inactive

AssellaBridge = AssellaBridge or {}
AssellaBridge.ipcSocketPath = "/tmp/assella_ipc.sock"
AssellaBridge.cmdPath = "/tmp/assella_cmd.json"

local ffi = require("ffi")

pcall(ffi.cdef, [[
	int socket(int domain, int type, int protocol);
	int connect(int sockfd, const void *addr, unsigned int addrlen);
	int close(int fd);
	ssize_t write(int fd, const void *buf, size_t count);
	ssize_t read(int fd, void *buf, size_t count);
	int unlink(const char *pathname);

	struct sockaddr_un {
		unsigned short sun_family;
		char sun_path[108];
	};
]])

local AF_UNIX = 1
local SOCK_STREAM = 1

-- Notify ASSella IPC server if active
AssellaBridge.notifyAssella = function(eventName, extraData)
	local fd = ffi.C.socket(AF_UNIX, SOCK_STREAM, 0)
	if fd < 0 then
		return false
	end

	local addr = ffi.new("struct sockaddr_un")
	addr.sun_family = AF_UNIX
	ffi.copy(addr.sun_path, AssellaBridge.ipcSocketPath)

	-- Connect to ASSella IPC socket (fails instantly if ASSella is not running)
	local rc = ffi.C.connect(fd, ffi.cast("void*", addr), ffi.sizeof("struct sockaddr_un"))
	if rc ~= 0 then
		ffi.C.close(fd)
		return false
	end

	local depotCount = (Downloader and Downloader.AdditionalDepots) and #Downloader.AdditionalDepots or 0
	local keyCount = 0
	if Downloader and Downloader.DecryptionKeys then
		for _ in pairs(Downloader.DecryptionKeys) do
			keyCount = keyCount + 1
		end
	end

	local payload = string.format(
		'{"event":"%s","steam_active":true,"downloader_loaded":%s,"depots_count":%d,"keys_count":%d}\n',
		tostring(eventName),
		tostring(Downloader ~= nil),
		depotCount,
		keyCount
	)

	ffi.C.write(fd, payload, #payload)

	-- Check if ASSella sent an immediate reply / command
	local buf = ffi.new("char[2048]")
	local n = ffi.C.read(fd, buf, 2047)
	if n > 0 then
		local resp = ffi.string(buf, n)
		AssellaBridge.processCommand(resp)
	end

	ffi.C.close(fd)
	return true
end

-- Process pending command from ASSella
AssellaBridge.processCommand = function(cmdStr)
	if not cmdStr or #cmdStr == 0 then
		return
	end

	local cmd = cmdStr:match('"cmd"%s*:%s*"([^"]+)"')
	if not cmd then
		return
	end

	if cmd == "add_depot" then
		local depotId = tonumber(cmdStr:match('"depot_id"%s*:%s*(%d+)'))
		if depotId and Downloader then
			table.insert(Downloader.AdditionalDepots, depotId)
			Downloader.RewriteDepots = true
			Downloader.writeDepotIds()
			log.info("[ASSellaBridge] Dynamically added depot " .. depotId)
		end

	elseif cmd == "add_key" then
		local depotId = tonumber(cmdStr:match('"depot_id"%s*:%s*(%d+)'))
		local key = cmdStr:match('"key"%s*:%s*"([a-fA-F0-9]+)"')
		if depotId and key and #key == 64 and Downloader then
			Downloader.DecryptionKeys[depotId] = key
			log.info("[ASSellaBridge] Dynamically added key for depot " .. depotId)
		end

	elseif cmd == "reload_config" then
		if Downloader and Downloader.configLoaded then
			Downloader.configLoaded()
		end
	end
end

-- Check if ASSella dropped a command file
AssellaBridge.checkCommandFile = function()
	local f = io.open(AssellaBridge.cmdPath, "r")
	if f then
		local content = f:read("*a")
		f:close()
		ffi.C.unlink(AssellaBridge.cmdPath)
		AssellaBridge.processCommand(content)
	end
end

-- Callbacks on SLSsteam events
SLS.registerCallback("SLSsteam::configLoaded", function()
	AssellaBridge.checkCommandFile()
	AssellaBridge.notifyAssella("configLoaded")
end)

SLS.registerCallback("SLSsteam::luaReload", function()
	AssellaBridge.checkCommandFile()
	AssellaBridge.notifyAssella("luaReload")
end)

-- Initial notification on load
AssellaBridge.notifyAssella("pluginLoaded")

log.notify("assella_bridge.lua loaded!")
