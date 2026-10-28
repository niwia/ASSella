-- katamari.lua (0.2)
--
-- Katamari is the family registry for the SLSsteam plugins.  It does not hook
-- anything and does not write config; it gives every plugin one shared place to
-- announce itself, its role, and its priority, so the family can coordinate.
--
-- Plugins register with:
--   KATAMARI = KATAMARI or { plugins = {} }
--   KATAMARI.plugins["name"] = { version="x", role="...", priority=10, ... }
--
-- If this file is present it adds the helper functions and reads
-- katamari.conf.lua.  Plugins create the table themselves if they load first,
-- so the family still works without this file.

KATAMARI = KATAMARI or { plugins = {} }

KATAMARI.VERSION = "0.2"

local defaults = {
	-- Optional explicit family order.  Lower priority still sorts first; this
	-- list is the tiebreaker and the human-readable intent.
	order = {
		"bakeneko",
		"spacebunny",
		"styx",
		"lobster",
		"bypass",
	},

	-- auto              = derive from bakeneko.mode + spacebunny.mrc_enabled
	-- bakeneko-first    = pre-seed manifests, SpaceBunny MRC as fallback
	-- spacebunny-only   = Bakeneko does not seed; MRC providers are used
	-- bakeneko-only     = Bakeneko seeds; SpaceBunny must not call MRC
	manifest_mode = "auto",

	-- Report unknown plugins in the plugins/ directory.  This is a warning
	-- only; a live plugin cannot unload a file SLSsteam has already queued.
	allow_unknown = true,
}

local function configHome()
	local xdg = os.getenv("XDG_CONFIG_HOME")
	if xdg ~= nil and xdg ~= "" then
		return xdg
	end
	return (os.getenv("HOME") or "") .. "/.config"
end

local function pluginDirectory()
	local source = debug.getinfo(1, "S").source
	if source:sub(1, 1) == "@" then
		source = source:sub(2)
	end
	return source:match("^(.*)[/\\]") or "."
end

local function readConfig(path)
	local handle = io.open(path, "r")
	if handle == nil then
		return nil
	end
	handle:close()

	local ok, value = pcall(dofile, path)
	if not ok or type(value) ~= "table" then
		log.warn("[Katamari] Invalid " .. path .. ": " .. tostring(value))
		return nil
	end
	return value
end

local function loadConfig()
	local config = {}
	for key, value in pairs(defaults) do
		config[key] = value
	end

	local dir = pluginDirectory()
	local paths = {
		configHome() .. "/SLSsteam/katamari.conf.lua",
		dir .. "/katamari.conf.lua",
		dir .. "/../katamari.conf.lua",
	}

	for _, path in ipairs(paths) do
		local values = readConfig(path)
		if values ~= nil then
			-- v0.2: family settings live under `family`.  Keep accepting the
			-- old top-level order/manifest_mode/allow_unknown keys.
			local family = values.family
			if type(family) == "table" then
				for key, value in pairs(family) do
					config[key] = value
				end
			else
				for key, value in pairs(values) do
					if config[key] ~= nil then
						config[key] = value
					end
				end
			end
			KATAMARI.mother = values
			break
		end
	end

	KATAMARI.config = config
	return config
end

function KATAMARI.register(id, meta)
	meta = meta or {}
	meta.id = id
	KATAMARI.plugins[id] = meta
	return meta
end

function KATAMARI.get(id)
	return KATAMARI.plugins[id]
end

function KATAMARI.has(id)
	return KATAMARI.plugins[id] ~= nil
end

function KATAMARI.byRole(role)
	local found = {}
	for _, meta in pairs(KATAMARI.plugins) do
		if meta.role == role then
			found[#found + 1] = meta
		end
	end
	return found
end

function KATAMARI.orderIndex(id)
	for index, value in ipairs(KATAMARI.config.order or {}) do
		if value == id then
			return index
		end
	end
	return 999
end

function KATAMARI.ordered()
	local list = {}
	for id, meta in pairs(KATAMARI.plugins) do
		if type(meta) == "table" then
			meta.id = meta.id or id
			list[#list + 1] = meta
		end
	end

	table.sort(list, function(left, right)
		local lp = tonumber(left.priority) or 100
		local rp = tonumber(right.priority) or 100

		if lp ~= rp then
			return lp < rp
		end

		local li = KATAMARI.orderIndex(left.id)
		local ri = KATAMARI.orderIndex(right.id)

		if li ~= ri then
			return li < ri
		end

		return tostring(left.id) < tostring(right.id)
	end)

	return list
end

function KATAMARI.manifestMode()
	local explicit = KATAMARI.config.manifest_mode or "auto"
	if explicit ~= "auto" then
		return explicit
	end

	local bakeneko = KATAMARI.get("bakeneko")
	local spacebunny = KATAMARI.get("spacebunny")

	local bakenekoMode = bakeneko and bakeneko.mode or "off"
	local mrcEnabled = spacebunny == nil or spacebunny.mrc_enabled ~= false

	if bakenekoMode == "off" then
		return "spacebunny-only"
	end
	if bakenekoMode == "only" or not mrcEnabled then
		return "bakeneko-only"
	end
	return "bakeneko-first"
end

function KATAMARI.summary()
	local parts = {}
	for _, meta in ipairs(KATAMARI.ordered()) do
		parts[#parts + 1] = string.format("%s=%s", meta.id, tostring(meta.version or "?"))
	end
	return table.concat(parts, ", ")
end

loadConfig()

if type(SLS) == "table" and type(SLS.registerCallback) == "function" then
	SLS.registerCallback("SLSsteam::initialized", function()
		log.info("[Katamari] family: " .. KATAMARI.summary()
			.. " | manifest mode: " .. KATAMARI.manifestMode())
	end)
end

log.notify("katamari.lua " .. KATAMARI.VERSION .. " loaded ("
	.. KATAMARI.summary() .. ")")
