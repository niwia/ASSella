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
--   }
-- =========================================================================

Lobster = Lobster or {
	setup = false,
	enabled = true,
	notify = true,
	auto_recover = true,
	config_path = nil,   -- nil = ~/.config/SLSsteam/config.yaml
}

if Lobster.setup then
	return
end
Lobster.setup = true

-- =========================================================================
-- Config loading
-- =========================================================================

local function lobsterLog(level, msg)
	-- Map our levels to SLSsteam log levels; fall back to print if SLS is
	-- not available (e.g. during a non-Steam test).
	local fn = level == "error" and log.notifyError
		or level == "warn" and log.notifyWarn
		or level == "info" and log.notify
		or log.debug
	if type(fn) == "function" then
		fn("[Lobster] " .. msg)
	else
		print("[Lobster] [" .. string.upper(level) .. "] " .. msg)
	end
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
	local cfg_dir = getSLSsteamConfigDir()
	local cfg_path = cfg_dir .. "/lobster.conf.lua"
	local ok, cfg = pcall(dofile, cfg_path)
	if not ok or type(cfg) ~= "table" then
		lobsterLog("warn", "No valid lobster.conf.lua found, using defaults")
		return
	end
	if type(cfg.enabled) == "boolean" then Lobster.enabled = cfg.enabled end
	if type(cfg.notify) == "boolean" then Lobster.notify = cfg.notify end
	if type(cfg.auto_recover) == "boolean" then
		Lobster.auto_recover = cfg.auto_recover
	end
	if type(cfg.config_path) == "string" and cfg.config_path ~= "" then
		Lobster.config_path = cfg.config_path
	end
	lobsterLog("info", "lobster.conf.lua loaded: enabled="
		.. tostring(Lobster.enabled)
		.. " notify=" .. tostring(Lobster.notify)
		.. " auto_recover=" .. tostring(Lobster.auto_recover))
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
local function findLatestBackup(configDir)
	local pattern = configDir .. "/config.yaml.headcrab-*"
	local candidates = {}
	local fh = io.popen("ls -1 " .. pattern:gsub(" ", "\\ ") .. " 2>/dev/null")
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

-- Check whether the current config already has both sections.
-- Returns (hasDepots, hasKeys)
local function checkConfigHasSections(configPath)
	local f = io.open(configPath, "rb")
	if f == nil then
		return false, false
	end
	local content = f:read("*a")
	f:close()
	local lines = sbSplitLines(content)
	return findSection(lines, "AdditionalDepots") ~= nil,
		   findSection(lines, "DecryptionKeys") ~= nil
end

-- Build the new config content by injecting missing sections from backup.
local function reconstructConfig(currentContent, backupContent)
	local curLines = sbSplitLines(currentContent)
	local bakLines = sbSplitLines(backupContent)

	local curDepotLine = findSection(curLines, "AdditionalDepots")
	local curKeyLine = findSection(curLines, "DecryptionKeys")
	local bakDepotLine = findSection(bakLines, "AdditionalDepots")
	local bakKeyLine = findSection(bakLines, "DecryptionKeys")

	local needsDepots = curDepotLine == nil
	local needsKeys = curKeyLine == nil

	if not needsDepots and not needsKeys then
		return nil, nil  -- nothing to do
	end

	-- Build output line by line.
	-- Strategy: scan current file, when we hit a section we're missing,
	-- insert the backup's section content at that point (or append at end).
	-- We use a simple approach: rebuild from current, inserting missing
	-- sections after the last top-level key that appears before EOF.

	local result = {}
	local insertedDepots = false
	local insertedKeys = false

	-- Find where to insert: after the last top-level key before EOF,
	-- or at the very end if the file is empty/minimal.
	-- We'll insert at the end of the file for simplicity, since these are
	-- supplementary sections that SLSsteam reads regardless of position.

	-- Actually, let's be smarter: insert after AppIds section if present,
	-- otherwise at the end.
	local insertAfterLine = #curLines

	for i = 1, #curLines do
		if curLines[i]:match("^AppIds:%s*:") then
			insertAfterLine = i
			-- Skip blank lines after it
			for j = i + 1, #curLines do
				if curLines[j]:match("^%s*$") then
					insertAfterLine = j
				else
					break
				end
			end
			break
		end
	end

	-- Copy current content
	for i = 1, #curLines do
		table.insert(result, curLines[i])
	end

	-- Insert missing sections at the end
	local inserts = {}
	if needsDepots and bakDepotLine then
		local block, _ = extractSection(bakLines, bakDepotLine)
		table.insert(inserts, "\n" .. block .. "\n")
	end
	if needsKeys and bakKeyLine then
		local block, _ = extractSection(bakLines, bakKeyLine)
		table.insert(inserts, "\n" .. block .. "\n")
	end

	for _, block in ipairs(inserts) do
		table.insert(result, block)
	end

	return table.concat(result, "\n"), #inserts
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

local function runRecovery()
	if not Lobster.enabled then
		lobsterLog("info", "Lobster disabled, skipping")
		return
	end

	local configPath = getLobsterConfigPath()
	lobsterLog("info", "Checking config: " .. configPath)

	local hasDepots, hasKeys = checkConfigHasSections(configPath)
	lobsterLog("info", "AdditionalDepots: " .. tostring(hasDepots)
		.. "  DecryptionKeys: " .. tostring(hasKeys))

	if hasDepots and hasKeys then
		lobsterLog("info", "Config already has both sections, no recovery needed")
		return
	end

	-- Find latest backup
	local backupDir = getBackupDir(configPath)
	local backupPath = findLatestBackup(backupDir)
	if backupPath == nil then
		lobsterLog("error", "No headcrab backup found in " .. backupDir)
		return
	end
	lobsterLog("info", "Found backup: " .. backupPath)

	-- Read current config content
	local cf = io.open(configPath, "rb")
	if cf == nil then
		lobsterLog("error", "Cannot read config: " .. configPath)
		return
	end
	local currentContent = cf:read("*a")
	cf:close()

	local backupF = io.open(backupPath, "rb")
	if backupF == nil then
		lobsterLog("error", "Cannot read backup: " .. backupPath)
		return
	end
	backupContent = backupF:read("*a")
	backupF:close()

	local newContent, sectionsRecovered = reconstructConfig(
		currentContent, backupContent)

	if newContent == nil then
		lobsterLog("info", "Nothing to recover")
		return
	end

	lobsterLog("info", "Recovering " .. tostring(sectionsRecovered)
		.. " section(s) from backup")

	if not Lobster.auto_recover then
		lobsterLog("info", "auto_recover=false, would recover but skipping write")
		return
	end

	local ok, err = writeInPlace(configPath, newContent)
	if not ok then
		lobsterLog("error", "Failed to write config: " .. tostring(err))
		return
	end

	lobsterLog("info", "Config recovered successfully")
	if Lobster.notify then
		-- SLSsteam's notify will fire via the callback; just log it here.
		lobsterLog("info", "Recovery complete: AdditionalDepots and DecryptionKeys restored")
	end
end

-- =========================================================================
-- SLSsteam integration
-- =========================================================================

loadConfig()

if Lobster.enabled then
	-- Run immediately on load (config may already be loaded by this point)
	-- and also register for configLoaded in case SLSsteam re-reads config.
	runRecovery()

	SLS.registerCallback("SLSsteam::configLoaded", function()
		runRecovery()
	end)

	lobsterLog("info", "lobster.lua loaded and active")
else
	lobsterLog("info", "lobster.lua loaded but disabled (set enabled=true in lobster.conf.lua)")
end
