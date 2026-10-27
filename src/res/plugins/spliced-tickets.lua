SplicedTickets = SplicedTickets or {
	setup = false,

	getTicketHook = nil, getTicketTramp = nil,
}

if SplicedTickets.setup then
	return
end
SplicedTickets.setup = true

local ffi = require("ffi")

ffi.cdef[[
	void* malloc(int);
	void free(void*);

	void* place_lua_hook(const int, const void*);

	typedef uint32_t(*GetTicket_t)(void*, uint32_t, uint8_t*, uint32_t, uint32_t*, uint32_t*, uint32_t*, uint32_t*);
]]

SplicedTickets.mutexReturn = function(value, mutex)
	mutex:unlock()
	return value
end

local clientUserMap = VFTableInfo_t("14IClientUserMap", "GetAppOwnershipTicketExtendedData")
if not clientUserMap:init() then
	log.notifyError("Failed to initialize IClientUserMap!")
	return
end

local clientUser = VFTableInfo_t("5CUser", "GetAppOwnershipTicketExtendedData", clientUserMap.index, 0)
clientUser:init()

log.debug("User ExtendedData at "..clientUserMap.index)

SplicedTickets.hkGetTicket = ffi.cast("GetTicket_t", function(pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)
	local mutex = LuaMutex()
	local size = SplicedTickets.getTicketTramp(pUser, appId, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)

	if size > 0 then
		return SplicedTickets.mutexReturn(size, mutex)
	end

	size = SplicedTickets.getTicketTramp(pUser, 7, pTicket, ticketSize, pOffAppId, pOffSteamId, pOffSig, pOffSigSize)

	local splicedTicket = {}
	for i = 0, size do
		splicedTicket[i] = pTicket[i]
	end

	local sigOffset = pOffSig[0]

	local pAppId = ffi.cast("uint32_t*", ffi.gc(ffi.C.malloc(4), ffi.C.free))
	pAppId[0] = appId
	local appIdBytes = ffi.cast("uint8_t*", pAppId)

	 for i = 0, 3 do
		table.insert(splicedTicket, sigOffset + i, appIdBytes[i])
	 end

	for i = 0, #splicedTicket - 1 do
		pTicket[i] = splicedTicket[i]
	end

	pOffAppId[0] = pOffSig[0]
	pOffSig[0] = pOffSig[0] + 4

	log.debug("Spliced ticket for " .. appId)

	return SplicedTickets.mutexReturn(size, mutex)
end)

SplicedTickets.getTicketHook = LuaHook("GetTicket", clientUser.ptr)
SplicedTickets.getTicketTramp = ffi.cast("GetTicket_t", ffi.C.place_lua_hook(SplicedTickets.getTicketHook.index, SplicedTickets.hkGetTicket))

log.notify("spliced-tickets.lua loaded!")
