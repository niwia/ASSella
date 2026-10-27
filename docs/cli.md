# Command Line Interface (CLI)

ASSella includes a modern, headless Command Line Interface powered by [Python Rich](https://github.com/Textualize/rich). It allows you to check for updates, query local Steam libraries, search online manifest repositories, and download games directly from a terminal or headless SSH session without requiring a desktop environment or X11/Wayland display server.

::: tip Availability
The modern Rich CLI interface is currently available in the **Canary** release channel (`canary` branch) and can be run via the canary AppImage or directly from source.
:::

---

## How to Run

You can invoke CLI commands using the `-cli` (or `--cli`) argument:

```bash
# If using the downloaded AppImage:
./ASSella.AppImage -cli <command> [arguments] [options]

# If running from source repository:
python3 src/main.py -cli <command> [arguments] [options]

# Or using the launcher script:
./run.sh -cli <command> [arguments] [options]
```

To display the built-in command reference at any time:
```bash
./ASSella.AppImage -cli --help
```

---

## Commands & Workflows

### 1. Update All Check (`update-all`)

Checks all installed ASSella-managed games across all detected Steam libraries against remote manifest repositories:

```bash
./ASSella.AppImage -cli update-all
```

**What it does:**
- Scans all local Steam library folders.
- Filters for ASSella-managed games (`ACCELA`, `AT0-M`, `DLC-Only`).
- Queries Hubcap / Steam manifest APIs in headless mode.
- Renders an interactive results table with status indicators (`✓ Up to date`, `▲ Update Available`, `AT0-M`, `? Unknown`).
- Caches results to `UpdateStatusCache` and prints a summary breakdown.

---

### 2. Local Library Search & List (`library search` / `list`)

Search your installed game libraries or list all games currently managed:

```bash
# Search for installed games by title or AppID:
./ASSella.AppImage -cli library search "Slay"

# List all installed games across Steam libraries:
./ASSella.AppImage -cli list
```

**Table Columns:**
- **AppID**: Steam Application ID.
- **Game Name**: Resolved title from manifests and local metadata.
- **Build ID**: Currently installed build version.
- **Mode**: Management mode badge (`ACCELA`, `AT0-M`, `DLC-Only`, or `Steam`).
- **Library Path**: Steam library directory where game files are stored.

---

### 3. Online Hubcap Manifest Search (`search`)

Search the online Hubcap manifest repository for games and view upload history:

```bash
./ASSella.AppImage -cli search "Cyberpunk"
```

**Output:**
- Displays ranked search results with upload dates and manifest availability flags (`Available` / `Missing`).
- Displays the exact command to download any discovered AppID.

---

### 4. Game Downloads & Installation (`download`)

Download and configure games using DepotDownloader directly from the terminal:

```bash
# Download by AppID:
./ASSella.AppImage -cli download 1091500

# Download by game title (shows ranked search matches if multiple exist):
./ASSella.AppImage -cli download "Hollow Knight"
```

#### Download Options & Flags

| Flag | Description |
|---|---|
| `--auto`, `--smart` | Auto-select recommended depots for the current operating system (skips interactive prompts). |
| `--all` | Select all available depots (including soundtrack, language packs, and optional content). |
| `--branch <name>` | Specify target Steam branch to download (e.g. `public`, `beta`, `experimental`). |
| `--dest <path>` | Install into a specific destination directory instead of querying Steam libraries. |
| `--help` | Show download command options and examples. |

#### Examples

```bash
# Smart-download with recommended depots for Linux / Proton:
./ASSella.AppImage -cli download 1091500 --smart

# Download a specific beta branch:
./ASSella.AppImage -cli download "Hollow Knight" --branch beta --smart

# Download to a specific custom directory:
./ASSella.AppImage -cli download 2868840 --dest /mnt/fast_nvme/games --smart

# Download all depots:
./ASSella.AppImage -cli download 1091500 --all
```

---

## Interactive Menu Controls

When running interactively without automated flags:

### 1. Branch Selection
If a game has multiple branches on Steam PICS:
- A table displays each branch name, build ID, and description.
- Enter the number (e.g. `2`), branch name (e.g. `beta`), or press <kbd>Enter</kbd> to accept the default/current branch.

### 2. Depot Selection
A color-coded table displays all available depots with OS tags, language, size, and an **`Auto`** indicator:
- **`✓`**: Depot is recommended for your platform (Proton / Linux Native).
- **`-`**: Depot is optional (bonus soundtracks, artbooks, alternate OS binaries, etc.).

**Prompt Syntax:**
- <kbd>Enter</kbd>: Accept all recommended depots automatically.
- `1,2,4`: Select specific depot numbers.
- `1-3`: Select a numeric range.
- `all` or `*`: Select all depots.
- `none` or `0`: Deselect all depots.
- `smart` or `auto`: Select the recommended depots.
- `q`: Cancel the operation.

### 3. Destination Selection
- Renders a table of detected Steam library folders.
- Press <kbd>Enter</kbd> to select library `[1]`, or pick another number.

### 4. Live Download & Post-Processing
- Displays a Rich progress bar showing percentage, download speed (`MB/s`), and file status.
- Once downloaded, automatically triggers post-processing:
  - Manifest integration and depotcache population.
  - ACF manifest registration (via SLSsteam native discovery).
  - Executable permissions (`chmod +x` on Linux).
  - Optional components (Goldberg emulator auto-apply or Steamless DRM removal if enabled in your settings).

---

## Diagnostic & Verbose Output

By default, CLI mode quiets background framework logs and prints only formatted Rich tables and status messages. Full detailed logs continue to be written to:
`~/.local/share/ACCELA/logs/assella_<timestamp>.log`

To print full debug logs directly to the console during execution, pass `--verbose` or `-v`:

```bash
./ASSella.AppImage -cli --verbose search "Elden"
```
