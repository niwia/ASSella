-- =========================================================================
-- lobster.lua — SLSsteam config recovery plugin
--
-- When headcrab's install script merges a fresh config template, it strips
-- AdditionalDepots and DecryptionKeys (they don't exist in the template).
-- This plugin detects that situation on config load and restores the two
-- sections from the most recent .headcrab-*. backup.
--
-- Config: ~/.config/SLSsteam/lobster.conf.lua
--   return {
--     enabled    = true,   -- master switch
--     notify     = true,   -- desktop notification on recovery
--     auto_recover = true, -- false = log only, don't write
--     run_on_reload = false, -- false = only run once on Steam boot
--   }
-- =========================================================================

Lobster = Lobster or {}

Lobster.VERSION = Lobster.VERSION or "1.2.1"
Lobster.setup = Lobster.setup or false
Lobster.enabled = Lobster.enabled ~= false
Lobster.notify = Lobster.notify ~= false
Lobster.auto_recover = Lobster.auto_recover ~= false
Lobster.run_on_reload = Lobster.run_on_reload == true
Lobster.recovering = Lobster.recovering == true

KATAMARI = KATAMARI or { plugins = {} }
KATAMARI.plugins.lobster = {
	version = Lobster.VERSION,
	role = "config-recovery",
	priority = 40,
}
-- Lobster.config_path is intentionally left as-is (nil means default path).
Lobster._config_path_explicit = Lobster._config_path_explicit
	or (Lobster.config_path ~= nil)

if Lobster.setup then
	return
end

-- `setup` is set at the end of a successful load.  SLSsteam re-runs plugin
-- files whenever anything changes in plugins/, and the luaReload callback below
-- already re-reads lobster.conf.lua, so a second pass must be a no-op.
-- =========================================================================
-- Changelog
-- =========================================================================
-- v1.2.1
--   - Recovery is surgical: only missing/empty AdditionalDepots and
--     DecryptionKeys sections are copied from the newest backup.  The rest of
--     the current config is never rewritten.
--   - Hot reload registers callbacks once, and a recovering flag prevents a
--     configLoaded callback from re-entering recovery during a write.
--   - A timestamped config.yaml.lobster-*.bak is written before touching the
--     config, and the write is verified afterwards.
--   - Fixed include guard preventing config reload from taking effect.
--     Previously, changing run_on_reload in lobster.conf.lua required a
--     full Steam restart. Now luaReload re-reads the config.
--
-- v1.2.0
--   - Added run_on_reload config option (default: false)
--   - Plugin now only runs once per Steam boot by default
--   - Set run_on_reload=true to check on every config reload
--
-- v1.1.0
--   - Added config_path option for testing
--
-- v1.0.0
--   - Initial release
-- =========================================================================

-- =========================================================================
-- Config loading
-- =========================================================================

local function lobsterLog(level, msg)
	-- Map our levels to SLSsteam log levels; fall back to print when log is
	-- unavailable (tests, or an early load failure).
	if type(log) == "table" then
		local fn = level == "error" and log.notifyError
			or level == "warn" and log.notifyWarn
			or level == "info" and log.notify
			or log.debug

		if type(fn) == "function" then
			fn("[Lobster] " .. msg)
			return
		end
	end

	print("[Lobster] [" .. string.upper(level) .. "] " .. msg)
end

local function getSLSsteamConfigDir()
	-- Match spacebunny's XDG-compliant path detection.
	local home = os.getenv("HOME") or ""
	local configHome = os.getenv("XDG_CONFIG_HOME")
	if configHome == nil or configHome == "" then
		configHome = home .. "/.config"
	end
	return configHome .. "/SLSsteam"
end

local function loadConfig()
	-- Reset to defaults first so a value removed from the conf does not linger
	-- across a luaReload.
	Lobster.enabled = true
	Lobster.notify = true
	Lobster.auto_recover = true
	Lobster.run_on_reload = false

	local explicitPath = Lobster._config_path_explicit and Lobster.config_path or nil
	Lobster.config_path = nil

	local cfg_dir = getSLSsteamConfigDir()
	local cfg_path = cfg_dir .. "/lobster.conf.lua"

	-- Mother config first; a per-plugin lobster.conf.lua overrides it.
	local mother_ok, mother = pcall(dofile, cfg_dir .. "/katamari.conf.lua")
	if mother_ok and type(mother) == "table" and type(mother.lobster) == "table" then
		local cfg = mother.lobster
		if type(cfg.enabled) == "boolean" then Lobster.enabled = cfg.enabled end
		if type(cfg.notify) == "boolean" then Lobster.notify = cfg.notify end
		if type(cfg.auto_recover) == "boolean" then
			Lobster.auto_recover = cfg.auto_recover
		end
		if type(cfg.run_on_reload) == "boolean" then
			Lobster.run_on_reload = cfg.run_on_reload
		end
		if type(cfg.config_path) == "string" and cfg.config_path ~= "" then
			Lobster.config_path = cfg.config_path
		end
	end

	local handle = io.open(cfg_path, "r")

	if handle == nil then
		if explicitPath ~= nil then
			Lobster.config_path = explicitPath
		end
		lobsterLog("info", "No lobster.conf.lua, using defaults + katamari.conf.lua")
		return
	end

	handle:close()

	local ok, cfg = pcall(dofile, cfg_path)
	if not ok or type(cfg) ~= "table" then
		lobsterLog("warn", "Invalid lobster.conf.lua, using defaults + katamari.conf.lua")
		if explicitPath ~= nil then
			Lobster.config_path = explicitPath
		end
		return
	end
	if type(cfg.enabled) == "boolean" then Lobster.enabled = cfg.enabled end
	if type(cfg.notify) == "boolean" then Lobster.notify = cfg.notify end
	if type(cfg.auto_recover) == "boolean" then
		Lobster.auto_recover = cfg.auto_recover
	end
	if type(cfg.run_on_reload) == "boolean" then
		Lobster.run_on_reload = cfg.run_on_reload
	end
	if type(cfg.config_path) == "string" and cfg.config_path ~= "" then
		Lobster.config_path = cfg.config_path
	elseif explicitPath ~= nil then
		Lobster.config_path = explicitPath
	end
	lobsterLog("info", "lobster.conf.lua loaded: enabled="
		.. tostring(Lobster.enabled)
		.. " notify=" .. tostring(Lobster.notify)
		.. " auto_recover=" .. tostring(Lobster.auto_recover)
		.. " run_on_reload=" .. tostring(Lobster.run_on_reload))
end

-- =========================================================================
-- YAML section helpers (line-based, no external parser needed)
--
-- A "top-level key" is a line that starts at column 0 with an identifier
-- followed by ':' and nothing else significant on that line (no '#').
-- Everything indented under it belongs to that section.
-- =========================================================================

local function sbSplitLines(text)
	local lines = {}
	for line in text:gmatch("[^\r\n]+") do
		table.insert(lines, line)
	end
	return lines
end

-- Find the line index (1-based) of a section header, or nil.
local function findSection(lines, key)
	for i = 1, #lines do
		if lines[i]:match("^" .. key:gsub("%.", "%%.") .. "%s*:") then
			return i
		end
	end
	return nil
end

-- Check whether a line is a top-level key (not indented, not a comment).
local function isTopLevelKey(line)
	-- Strip leading whitespace
	local stripped = line:match("^%s*(.*)")
	-- Must start with a letter or underscore
	if not stripped:match("^[A-Za-z_]") then
		return false
	end
	-- Must contain a ':' before any '#' (ignore comments)
	local without_comment = stripped:match("([^#]*)")
	return without_comment:match("^%S+:%s*$") ~= nil
end

-- Extract a section starting at `startLine` (the header line).
-- Returns the extracted text block and the line number of the next section.
local function extractSection(lines, startLine)
	local block = { lines[startLine] }
	local i = startLine + 1
	while i <= #lines do
		local line = lines[i]
		if line:match("^%s") then
			-- Indented line: part of current section
			table.insert(block, line)
			i = i + 1
		elseif isTopLevelKey(line) then
			-- Next top-level key: stop
			break
		else
			-- Blank line or comment at top level: keep going if next non-blank
			-- is indented (section continuation) or stop.
			local j = i + 1
			while j <= #lines and lines[j]:match("^%s*$") do
				j = j + 1
			end
			if j > #lines or not lines[j]:match("^%s") then
				break
			end
			table.insert(block, line)
			i = i + 1
		end
	end
	return table.concat(block, "\n"), i
end

-- Find the best backup file matching config.yaml.headcrab-YYYYMMDD-HHMMSS[_N].
-- Returns the full path or nil.
local function shellQuote(value)
	return "'" .. tostring(value):gsub("'", "'\\''") .. "'"
end

local function findLatestBackup(configDir)
	-- Quote only the directory; the trailing glob must stay unquoted so the
	-- shell expands it.  This still works for paths with spaces or quotes.
	local candidates = {}
	local fh = io.popen("ls -1 -- " .. shellQuote(configDir)
		.. "/config.yaml.headcrab-* 2>/dev/null")
	if fh then
		for f in fh:lines() do
			table.insert(candidates, f)
		end
		fh:close()
	end
	if #candidates == 0 then
		return nil
	end
	-- Sort lexicographically — the timestamp in the filename makes this
	-- equivalent to chronological order.
	table.sort(candidates)
	return candidates[#candidates]
end

-- =========================================================================
-- Recovery logic
-- =========================================================================

local function getLobsterConfigPath()
	if Lobster.config_path then
		return Lobster.config_path
	end
	return getSLSsteamConfigDir() .. "/config.yaml"
end

local function getBackupDir(configPath)
	-- Directory of the config file
	local base = configPath:gsub("/[^/]*$", "")
	if base == configPath then
		base = "."
	end
	return base
end

-- Count real entries under a top-level section.  A section can exist but be
-- empty (headcrab leaves "AdditionalDepots:" with no children), which should
-- still count as missing.
local function sectionEntryCount(lines, key)
	local start = findSection(lines, key)
	if start == nil then
		return 0
	end

	local count = 0

	for i = start + 1, #lines do
		local line = lines[i]

		if line:match("^%S") then
			break
		end

		if key == "AdditionalDepots" then
			if line:match("^%s+%-%s*%d+") then
				count = count + 1
			end
		elseif line:match("^%s+%d+%s*:") then
			count = count + 1
		end
	end

	return count
end

-- Copy one top-level section (header + indented/blank lines) from the backup.
-- Stops at the next non-indented line so comments and keys belonging to later
-- sections are never dragged along.
local function extractSectionBlock(lines, key)
	local start = findSection(lines, key)
	if start == nil then
		return nil
	end

	local block = { lines[start] }

	for i = start + 1, #lines do
		local line = lines[i]

		if line:match("^%s*$") or line:match("^%s") then
			block[#block + 1] = line
		else
			break
		end
	end

	while #block > 1 and block[#block]:match("^%s*$") do
		table.remove(block)
	end

	return block
end

local function countDepotEntries(block)
	local count = 0
	for i = 2, #block do
		if block[i]:match("^%s+%-%s*%d+") then
			count = count + 1
		end
	end
	return count
end

local function countKeyEntries(block)
	local count = 0
	for i = 2, #block do
		local value = block[i]:match("^%s+%d+%s*:%s*([^#%s]+)")
		if value ~= nil then
			value = value:gsub("^[\"']", ""):gsub("[\"']$", "")
			if value:match("^%x+$") then
				count = count + 1
			end
		end
	end
	return count
end

-- Build the replacement text by inserting only the missing section blocks.
-- Everything already in the current config is copied verbatim, in place.
-- Drop an existing empty section header before inserting the restored copy,
-- otherwise an empty "AdditionalDepots:" would be duplicated.
local function withoutEmptySections(currentLines, blocks)
	local remove = {}

	for _, block in ipairs(blocks) do
		if sectionEntryCount(currentLines, block.key) == 0 then
			local start = findSection(currentLines, block.key)

			if start ~= nil then
				local stop = start
				while stop + 1 <= #currentLines
						and not currentLines[stop + 1]:match("^%S") do
					stop = stop + 1
				end

				for i = start, stop do
					remove[i] = true
				end
			end
		end
	end

	local filtered = {}
	for i = 1, #currentLines do
		if not remove[i] then
			filtered[#filtered + 1] = currentLines[i]
		end
	end

	return filtered
end

local function insertSectionBlocks(currentLines, blocks)
	if #blocks == 0 then
		return nil
	end

	currentLines = withoutEmptySections(currentLines, blocks)

	local anchor = findSection(currentLines, "ManifestIds")
		or findSection(currentLines, "DepotBlacklist")
		or (#currentLines + 1)

	local out = {}

	for i = 1, anchor - 1 do
		out[#out + 1] = currentLines[i]
	end

	for index, block in ipairs(blocks) do
		if index > 1 or (#out > 0 and out[#out] ~= "") then
			out[#out + 1] = ""
		end

		for _, line in ipairs(block.lines) do
			out[#out + 1] = line
		end
	end

	if #out > 0 and out[#out] ~= "" then
		out[#out + 1] = ""
	end

	for i = anchor, #currentLines do
		out[#out + 1] = currentLines[i]
	end

	return table.concat(out, "\n") .. "\n"
end

-- Write text in-place (preserves inode for SLSsteam's inotify watcher).
-- Same approach as SpaceBunny's sbWriteInPlace.
local function writeInPlace(path, text)
	local ok, err = pcall(function()
		local ffi = require("ffi")
		ffi.cdef([[
			int open(const char*, int, int);
			long write(int, const void*, unsigned long);
			int ftruncate(int, long);
			int fsync(int);
			int close(int);
		]])
		local O_RDWR = 2
		local mode = 384  -- 0600
		local fd = ffi.C.open(path, O_RDWR, mode)
		if fd < 0 then
			error("open failed")
		end
		local buf = ffi.new("char[?]", #text + 1, text)
		local written = ffi.C.write(fd, buf, #text)
		if written ~= #text then
			ffi.C.close(fd)
			error("short write")
		end
		ffi.C.ftruncate(fd, #text)
		ffi.C.fsync(fd)
		ffi.C.close(fd)
	end)
	if ok then
		return true, nil
	end
	-- Fallback: plain io write
	local f = io.open(path, "r+")
	if f == nil then
		return false, "cannot open " .. path
	end
	local ok2, err2 = pcall(function()
		f:seek("set", 0)
		f:write(text)
		f:flush()
	end)
	f:close()
	if not ok2 then
		return false, tostring(err2)
	end
	return true, nil
end

-- Save the current config before Lobster touches it.  Timestamped, never
-- overwrites an earlier backup.
local function fileExists(path)
	local handle = io.open(path, "r")
	if handle == nil then
		return false
	end
	handle:close()
	return true
end

local function backupCurrentConfig(path)
	local stamp = os.date("%Y%m%d-%H%M%S")
	local backup = path .. ".lobster-" .. stamp
	local suffix = 1

	while fileExists(backup) do
		backup = path .. ".lobster-" .. stamp .. "-" .. suffix
		suffix = suffix + 1
	end

	local source = io.open(path, "rb")
	if source == nil then
		return nil, "cannot read current config"
	end

	local content = source:read("*a")
	source:close()

	local destination = io.open(backup, "wb")
	if destination == nil then
		return nil, "cannot create backup"
	end

	destination:write(content)
	destination:close()

	-- The config contains decryption keys.  Backups must be owner-only.
	local chmodOk = pcall(function()
		local ffi = require("ffi")
		ffi.cdef([[ int chmod(const char*, int); ]])
		ffi.C.chmod(backup, 384) -- 0600
	end)

	if not chmodOk then
		os.execute("chmod 600 -- " .. shellQuote(backup) .. " 2>/dev/null")
	end

	return backup
end

local function readFile(path)
	local file = io.open(path, "rb")
	if file == nil then
		return nil
	end

	local content = file:read("*a")
	file:close()

	return content
end

local function recoverNow()
	if not Lobster.enabled then
		lobsterLog("info", "Lobster disabled, skipping")
		return
	end

	local configPath = getLobsterConfigPath()
	lobsterLog("info", "Checking config: " .. configPath)

	local currentContent = readFile(configPath)
	if currentContent == nil then
		lobsterLog("error", "Cannot read config: " .. configPath)
		return
	end

	local currentLines = sbSplitLines(currentContent)
	local depotCount = sectionEntryCount(currentLines, "AdditionalDepots")
	local keyCount = sectionEntryCount(currentLines, "DecryptionKeys")

	lobsterLog("info", "AdditionalDepots entries: " .. depotCount
		.. "  DecryptionKeys entries: " .. keyCount)

	if depotCount > 0 and keyCount > 0 then
		lobsterLog("info", "Both sections already have entries, no recovery needed")
		return
	end

	local backupPath = findLatestBackup(getBackupDir(configPath))
	if backupPath == nil then
		lobsterLog("error", "No headcrab backup found in " .. getBackupDir(configPath))
		return
	end

	lobsterLog("info", "Found backup: " .. backupPath)

	local backupContent = readFile(backupPath)
	if backupContent == nil then
		lobsterLog("error", "Cannot read backup: " .. backupPath)
		return
	end

	local backupLines = sbSplitLines(backupContent)
	local blocks = {}
	local restored = {}
	local wantDepots = false
	local wantKeys = false

	if depotCount == 0 then
		local block = extractSectionBlock(backupLines, "AdditionalDepots")
		local entries = block and countDepotEntries(block) or 0

		if entries > 0 then
			blocks[#blocks + 1] = { key = "AdditionalDepots", lines = block }
			restored[#restored + 1] = "AdditionalDepots (" .. entries .. " entries)"
			wantDepots = true
		else
			lobsterLog("warn", "Backup has no usable AdditionalDepots section")
		end
	end

	if keyCount == 0 then
		local block = extractSectionBlock(backupLines, "DecryptionKeys")
		local entries = block and countKeyEntries(block) or 0

		if entries > 0 then
			blocks[#blocks + 1] = { key = "DecryptionKeys", lines = block }
			restored[#restored + 1] = "DecryptionKeys (" .. entries .. " entries)"
			wantKeys = true
		else
			lobsterLog("warn", "Backup has no usable DecryptionKeys section")
		end
	end

	if #blocks == 0 then
		lobsterLog("error", "Nothing valid to restore from the backup")
		return
	end

	local newContent = insertSectionBlocks(currentLines, blocks)
	if newContent == nil then
		lobsterLog("error", "Failed to build the restored config")
		return
	end

	if not Lobster.auto_recover then
		lobsterLog("info", "auto_recover=false, would restore "
			.. table.concat(restored, " and ") .. " but skipping write")
		return
	end

	local savedPath, backupErr = backupCurrentConfig(configPath)
	if savedPath == nil then
		lobsterLog("error", "Not writing config, backup failed: " .. tostring(backupErr))
		return
	end

	local ok, err = writeInPlace(configPath, newContent)
	if not ok then
		lobsterLog("error", "Failed to write config: " .. tostring(err))
		return
	end

	-- Verify only the sections we intended to restore.  If the write is wrong,
	-- put the untouched original back.
	local verified = readFile(configPath) or ""
	local verifiedLines = sbSplitLines(verified)
	local depotsOk = (not wantDepots) or sectionEntryCount(verifiedLines, "AdditionalDepots") > 0
	local keysOk = (not wantKeys) or sectionEntryCount(verifiedLines, "DecryptionKeys") > 0

	if not depotsOk or not keysOk then
		local original = readFile(savedPath)
		if original ~= nil then
			writeInPlace(configPath, original)
		end

		lobsterLog("error", "Recovery verification failed; original config restored")
		return
	end

	lobsterLog("info", "Restored " .. table.concat(restored, " and ")
		.. " from " .. backupPath)
	lobsterLog("info", "Previous config saved as " .. savedPath)
end

local function runRecovery()
	if Lobster.recovering then
		return
	end

	Lobster.recovering = true
	local ok, err = pcall(recoverNow)
	Lobster.recovering = false

	if not ok then
		lobsterLog("error", "Recovery failed: " .. tostring(err))
	end
end

-- =========================================================================
-- SLSsteam integration
-- =========================================================================

loadConfig()

if type(SLS) == "table" and type(SLS.registerCallback) == "function" then
	SLS.registerCallback("SLSsteam::luaReload", function()
		loadConfig()
		if Lobster.enabled then
			runRecovery()
		end
	end)

	SLS.registerCallback("SLSsteam::configLoaded", function()
		if Lobster.run_on_reload then
			runRecovery()
		end
	end)
end

if Lobster.enabled then
	runRecovery()
	lobsterLog("info", "lobster.lua v" .. Lobster.VERSION .. " loaded and active")
else
	lobsterLog("info", "lobster.lua v" .. Lobster.VERSION
		.. " loaded but disabled (set enabled=true in lobster.conf.lua)")
end

Lobster.setup = true
