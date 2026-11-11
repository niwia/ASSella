#!/usr/bin/env bash
set -euo pipefail

# Bash version guard (declare -A and modern syntax require Bash 4+)
if (( BASH_VERSINFO[0] < 4 )); then
    echo "This installer requires Bash 4.0 or newer (detected version: $BASH_VERSION)." >&2
    exit 1
fi

# ==============================================================================
#                      ASSella Installer
# ==============================================================================

# Terminal Colors & Styling (respect NO_COLOR and non-interactive TTYs)
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    CYAN=''
    BOLD=''
    NC=''
fi

INSTALL_DESTINATION="$HOME/.local/share/ACCELA"
DESKTOP_DIR="$HOME/.local/share/applications"
PRIMARY_DESKTOP_ENTRY="$DESKTOP_DIR/assella.desktop"
LEGACY_DESKTOP_ENTRY="$DESKTOP_DIR/accela.desktop"
DESKTOP_ENTRY="$PRIMARY_DESKTOP_ENTRY"
SCALABLE_ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
PRIMARY_SVG_ICON_PATH="$SCALABLE_ICON_DIR/assella.svg"
LEGACY_SVG_ICON_PATH="$SCALABLE_ICON_DIR/accela.svg"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
PRIMARY_ICON_PATH="$ICON_DIR/assella.png"
LEGACY_ICON_PATH="$ICON_DIR/accela.png"
ICON_PATH="$PRIMARY_ICON_PATH"
VERSION_FILE="$INSTALL_DESTINATION/version"
NIXOS_LAUNCHER="$INSTALL_DESTINATION/launch_nixos.sh"

TARGET_CHANNEL=""
UPDATE_SERVER="auto"
R2_RELEASES_PUBLIC_URL="https://pub-16d0a0f940ee42f89e73b896a22be434.r2.dev"
LOCAL_VER="Unknown"
LATEST_VER=""
LATEST_URL=""
LATEST_SHA256=""
BETA_VER=""
BETA_URL=""
BETA_SHA256=""
STABLE_VER=""
STABLE_URL=""
STABLE_SHA256=""
PRESELECTED_VER=""
PRESELECTED_URL=""
PRESELECTED_SHA256=""
FUSE_INSTALLED=false
STEAM_TYPE="none"
STEAM_DIR=""
SLS_CONFIG_DIR=""
SLS_CONFIG_FILE=""
HEADCRAB_INSTALLED=false
IS_STEAM_DECK=false
IS_NIXOS=false
IS_CACHYOS=false
INTERACTIVE=false
TMP_DIR=""
SKIP_CHECKSUM=false
DESKTOP_TMP=""
ICON_TMP=""
SVG_ICON_TMP=""

cleanup() {
    if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR" 2>/dev/null || true
    fi

    rm -f "${DESKTOP_TMP:-}" "${ICON_TMP:-}" "${SVG_ICON_TMP:-}" 2>/dev/null || true
    # Sweep hidden mktemp leftovers from a run killed before its trap fired
    # (SIGKILL cannot be trapped). Guard the directories: an empty value would
    # turn the pattern into a glob at the filesystem root.
    if [ -n "${DESKTOP_DIR:-}" ]; then
        rm -f "$DESKTOP_DIR/.assella.desktop.tmp."* 2>/dev/null || true
    fi
    if [ -n "${ICON_DIR:-}" ]; then
        rm -f "$ICON_DIR/.assella.png.tmp."* 2>/dev/null || true
    fi
    if [ -n "${SCALABLE_ICON_DIR:-}" ]; then
        rm -f "$SCALABLE_ICON_DIR/.assella.svg.tmp."* 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

declare -A PKG_FUSE_CMD=(
    [arch]="sudo pacman -S --needed fuse2"
    [debian]="sudo apt install -y libfuse2"
    [fedora]="sudo dnf install -y fuse-libs"
    [opensuse]="sudo zypper install -y libfuse2"
    [alpine]="sudo apk add fuse2"
    [void]="sudo xbps-install -S fuse"
    [solus]="sudo eopkg install -y fuse"
    [gentoo]="sudo emerge --ask sys-fs/fuse"
)

write_desktop_entry() {
    local desktop_tmp
    local exec_cmd="${EXEC_COMMAND:-$INSTALL_DESTINATION/ACCELA.AppImage %u}"

    # Ensure executable path is quoted if unquoted and containing whitespace
    if [[ "$exec_cmd" != \"* ]] && [[ "$exec_cmd" == *" "* ]]; then
        local bin_part="${exec_cmd%% %*}"
        local arg_part="${exec_cmd#$bin_part}"
        if [[ "$bin_part" != \"*\" ]]; then
            exec_cmd="\"$bin_part\"$arg_part"
        fi
    fi

    mkdir -p "$DESKTOP_DIR"
    # Create the file beside the destination, then rename it.  This replaces a
    # destination symlink itself instead of following it (which previously
    # allowed assella.desktop and accela.desktop to become a symlink loop).
    desktop_tmp=$(mktemp "$DESKTOP_DIR/.assella.desktop.tmp.XXXXXX")
    DESKTOP_TMP="$desktop_tmp"
    cat >"$desktop_tmp" <<EOL
[Desktop Entry]
Version=2.0
Name=ASSella
Comment=Steam Game & Depot Management Suite
Exec=$exec_cmd
Icon=assella
Terminal=false
Type=Application
Categories=Utility;Game;
StartupWMClass=ASSella
EOL
    chmod 0644 "$desktop_tmp"
    # Unlink any existing entry/symlink so older coreutils mv (e.g. <= 8.32 on Ubuntu 22.04)
    # does not fail with ELOOP ("Too many levels of symbolic links") when stat'ing DEST.
    rm -f "$PRIMARY_DESKTOP_ENTRY"
    mv -f "$desktop_tmp" "$PRIMARY_DESKTOP_ENTRY"
    DESKTOP_TMP=""
    test -f "$PRIMARY_DESKTOP_ENTRY"
    test ! -L "$PRIMARY_DESKTOP_ENTRY"
    grep -q '^Exec=' "$PRIMARY_DESKTOP_ENTRY"

    # Clean up any legacy accela.desktop entry or symlink so application launchers
    # do not display duplicate ASSella entries (issue #31).
    rm -f "$LEGACY_DESKTOP_ENTRY"
}

_install_icon_variant() {
    local dir="$1"
    local primary_path="$2"
    local legacy_path="$3"
    local tmp_prefix="$4"
    local primary_name="$5"
    local url="$6"
    local tmp_var_name="$7"
    local icon_tmp

    mkdir -p "$dir"
    icon_tmp=$(mktemp "$dir/$tmp_prefix.XXXXXX")
    printf -v "$tmp_var_name" '%s' "$icon_tmp"
    if curl -fsSL --retry 3 --retry-delay 2 -o "$icon_tmp" "$url"; then
        chmod 0644 "$icon_tmp"
        rm -f "$primary_path"
        mv -f "$icon_tmp" "$primary_path"
        printf -v "$tmp_var_name" '%s' ""
    else
        rm -f "$icon_tmp"
        printf -v "$tmp_var_name" '%s' ""
        echo -e "${YELLOW}[WARN] Could not download $primary_name icon; keeping any existing regular icon.${NC}"
    fi

    # Maintain legacy alias if the primary icon exists as a regular file
    if [ -f "$primary_path" ] && [ ! -L "$primary_path" ]; then
        rm -f "$legacy_path"
        ln -s "$primary_name" "$legacy_path"
    fi
}

install_application_icon() {
    _install_icon_variant \
        "$SCALABLE_ICON_DIR" "$PRIMARY_SVG_ICON_PATH" "$LEGACY_SVG_ICON_PATH" \
        ".assella.svg.tmp" "assella.svg" \
        "https://raw.githubusercontent.com/niwia/ASSella/main/src/res/logo/icon.svg" \
        "SVG_ICON_TMP"
    _install_icon_variant \
        "$ICON_DIR" "$PRIMARY_ICON_PATH" "$LEGACY_ICON_PATH" \
        ".assella.png.tmp" "assella.png" \
        "https://raw.githubusercontent.com/niwia/ASSella/main/src/res/logo/icon.png" \
        "ICON_TMP"
}

# ------------------------------------------------------------------------------
#  1. System & Distro Detection
# ------------------------------------------------------------------------------
detect_distro() {
    DISTRO_ID="unknown"
    DISTRO_NAME="Generic Linux"
    DISTRO_LIKE=""
    IS_STEAM_DECK=false
    IS_NIXOS=false
    IS_CACHYOS=false

    if [ -f /etc/os-release ]; then
        set +e
        . /etc/os-release
        set -e
        DISTRO_ID="${ID:-unknown}"
        DISTRO_NAME="${PRETTY_NAME:-${NAME:-Generic Linux}}"
        DISTRO_LIKE="${ID_LIKE:-}"
    fi

    if [[ "$DISTRO_ID" == "steamos" ]] || [[ -f /etc/steamos-release ]] || [[ "$HOME" == *deck* ]]; then
        IS_STEAM_DECK=true
    fi

    if [[ "$DISTRO_ID" == "nixos" ]]; then
        IS_NIXOS=true
    fi

    if [[ "$DISTRO_ID" == "cachyos" ]] || [[ "$DISTRO_NAME" == *"CachyOS"* ]]; then
        IS_CACHYOS=true
    fi
}

detect_distro_family() {
    local family="unknown"
    if [ "$IS_STEAM_DECK" = true ]; then
        family="arch"
    elif [ "$DISTRO_ID" = "bazzite" ] || [ "$DISTRO_ID" = "chimeraos" ] || [ "$DISTRO_ID" = "holoiso" ]; then
        family="arch"
    elif [[ "$DISTRO_ID" == "fedora" || "$DISTRO_ID" == "rhel" || "$DISTRO_ID" == "centos" || "$DISTRO_ID" == "nobara" || "$DISTRO_LIKE" =~ "fedora" || "$DISTRO_LIKE" =~ "rhel" ]]; then
        family="fedora"
    elif [[ "$DISTRO_ID" == "debian" || "$DISTRO_ID" == "ubuntu" || "$DISTRO_ID" == "linuxmint" || "$DISTRO_ID" == "pop" || "$DISTRO_LIKE" =~ "debian" || "$DISTRO_LIKE" =~ "ubuntu" ]]; then
        family="debian"
    elif [[ "$DISTRO_ID" == "arch" || "$DISTRO_ID" == "cachyos" || "$DISTRO_ID" == "manjaro" || "$DISTRO_ID" == "endeavouros" || "$DISTRO_LIKE" =~ "arch" ]]; then
        family="arch"
    elif [[ "$DISTRO_ID" =~ opensuse || "$DISTRO_ID" =~ suse || "$DISTRO_LIKE" =~ opensuse || "$DISTRO_LIKE" =~ suse ]]; then
        family="opensuse"
    elif [[ "$DISTRO_ID" == "alpine" ]]; then
        family="alpine"
    elif [[ "$DISTRO_ID" == "void" ]]; then
        family="void"
    elif [[ "$DISTRO_ID" == "solus" ]]; then
        family="solus"
    elif [[ "$DISTRO_ID" == "gentoo" ]]; then
        family="gentoo"
    fi
    echo "$family"
}

check_fuse_status() {
    FUSE_INSTALLED=false
    if command -v ldconfig &>/dev/null; then
        if ldconfig -p 2>/dev/null | grep -q "libfuse.so.2"; then
            FUSE_INSTALLED=true
        fi
    fi

    if [ "$FUSE_INSTALLED" = false ]; then
        if [ -f /usr/lib/libfuse.so.2 ] || [ -f /lib/libfuse.so.2 ] || [ -f /usr/lib64/libfuse.so.2 ] || [ -f /lib64/libfuse.so.2 ] || [ -f /lib/x86_64-linux-gnu/libfuse.so.2 ]; then
            FUSE_INSTALLED=true
        fi
    fi
}

detect_steam_paths() {
    STEAM_TYPE="none"
    STEAM_DIR=""
    SLS_CONFIG_DIR=""
    SLS_CONFIG_FILE=""

    # 1. Check where SLSsteam configuration exists
    if [ -f "$HOME/.var/app/com.valvesoftware.Steam/.config/SLSsteam/config.yaml" ]; then
        STEAM_TYPE="flatpak"
        SLS_CONFIG_DIR="$HOME/.var/app/com.valvesoftware.Steam/.config/SLSsteam"
    elif [ -f "$HOME/.config/SLSsteam/config.yaml" ]; then
        STEAM_TYPE="native"
        SLS_CONFIG_DIR="$HOME/.config/SLSsteam"
    elif [ -d "$HOME/.var/app/com.valvesoftware.Steam/.config/SLSsteam" ]; then
        STEAM_TYPE="flatpak"
        SLS_CONFIG_DIR="$HOME/.var/app/com.valvesoftware.Steam/.config/SLSsteam"
    elif [ -d "$HOME/.config/SLSsteam" ]; then
        STEAM_TYPE="native"
        SLS_CONFIG_DIR="$HOME/.config/SLSsteam"
    fi

    # 2. Check Steam client installation directories if SLS not located yet
    if [ "$STEAM_TYPE" = "none" ]; then
        if [ -d "$HOME/.var/app/com.valvesoftware.Steam/.steam/steam" ] || [ -d "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam" ]; then
            STEAM_TYPE="flatpak"
            SLS_CONFIG_DIR="$HOME/.var/app/com.valvesoftware.Steam/.config/SLSsteam"
        elif [ -d "$HOME/.local/share/Steam" ] || [ -d "$HOME/.steam/steam" ] || [ -d "$HOME/.steam/root" ]; then
            STEAM_TYPE="native"
            SLS_CONFIG_DIR="$HOME/.config/SLSsteam"
        elif [ -d "$HOME/snap/steam/common/.steam/steam" ]; then
            STEAM_TYPE="snap"
            SLS_CONFIG_DIR="$HOME/snap/steam/common/.config/SLSsteam"
        else
            STEAM_TYPE="native"
            SLS_CONFIG_DIR="$HOME/.config/SLSsteam"
        fi
    fi

    # 3. Resolve Steam directory
    if [ "$STEAM_TYPE" = "flatpak" ]; then
        STEAM_DIR="$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"
        [ -d "$STEAM_DIR" ] || STEAM_DIR="$HOME/.var/app/com.valvesoftware.Steam/.steam/steam"
    elif [ "$STEAM_TYPE" = "snap" ]; then
        STEAM_DIR="$HOME/snap/steam/common/.local/share/Steam"
        [ -d "$STEAM_DIR" ] || STEAM_DIR="$HOME/snap/steam/common/.steam/steam"
    else
        STEAM_DIR="$HOME/.local/share/Steam"
        [ -d "$STEAM_DIR" ] || STEAM_DIR="$HOME/.steam/steam"
    fi

    SLS_CONFIG_FILE="$SLS_CONFIG_DIR/config.yaml"
}

check_headcrab_status() {
    HEADCRAB_INSTALLED=false
    detect_steam_paths
    if [ -f "$SLS_CONFIG_FILE" ] || [ -d "$SLS_CONFIG_DIR" ] || [ -d "/tmp/SLSsteam" ]; then
        HEADCRAB_INSTALLED=true
    elif command -v systemctl &>/dev/null && systemctl --user is-active slssteam &>/dev/null; then
        HEADCRAB_INSTALLED=true
    fi
}

get_local_version() {
    LOCAL_VER="Not Installed"
    if [ -f "$VERSION_FILE" ]; then
        read -r LOCAL_VER < "$VERSION_FILE" || LOCAL_VER="Unknown"
        LOCAL_VER=$(printf '%s' "$LOCAL_VER" | tr -d '\r\n')
    elif [ -f "$INSTALL_DESTINATION/squashfs-root/bin/src/res/version" ]; then
        read -r LOCAL_VER < "$INSTALL_DESTINATION/squashfs-root/bin/src/res/version" || LOCAL_VER="Unknown"
        LOCAL_VER=$(printf '%s' "$LOCAL_VER" | tr -d '\r\n')
    fi
}

detect_channel() {
    if [ -n "${TARGET_CHANNEL:-}" ]; then
        return 0
    fi
    local lower_local
    lower_local=$(printf '%s' "${LOCAL_VER:-}" | tr '[:upper:]' '[:lower:]')
    lower_local=$(printf '%s' "$lower_local" | sed -E 's/^[0-9]{8}\+[a-z0-9._-]+-//; s/^[0-9]{8}[_-]//')
    if [ -z "$lower_local" ] || [ "$lower_local" = "unknown" ]; then
        # Default to beta channel on the beta branch installer when not installed/unknown
        TARGET_CHANNEL="beta"
    elif printf '%s' "$lower_local" | grep -qE '(^|[^a-z])(canary|testing|devtest|release[_-]?candidate)([^a-z]|$)'; then
        TARGET_CHANNEL="canary"
    elif printf '%s' "$lower_local" | grep -qE '(^|[^a-z])(beta|dev|rc|candidate)([^a-z]|$)'; then
        TARGET_CHANNEL="beta"
    elif printf '%s' "$lower_local" | grep -qE '^v?[0-9]+\.[0-9]+(\.[0-9]+)?([-._]?hotfix[0-9]*)?$'; then
        TARGET_CHANNEL="stable"
    else
        TARGET_CHANNEL="beta"
    fi
}

prompt_user() {
    local prompt_msg="$1"
    local default_val="${2:-}"
    local response=""
    if [ -t 0 ]; then
        read -r -p "$prompt_msg" response || true
    elif [ -r /dev/tty ]; then
        read -r -p "$prompt_msg" response </dev/tty || true
    else
        read -r response || true
    fi
    echo "${response:-$default_val}"
}

check_and_warn_fuse() {
    check_fuse_status
    if [ "$FUSE_INSTALLED" = true ] || [ "$IS_NIXOS" = true ]; then
        return 0
    fi

    local family
    family=$(detect_distro_family)
    local cmd="${PKG_FUSE_CMD[$family]:-}"

    echo -e "\n${YELLOW}${BOLD}[WARN] FUSE 2 (libfuse.so.2) is not detected!${NC}"
    echo -e "ASSella AppImage requires fuse2 to run on your system."

    if [ -n "$cmd" ]; then
        echo -e "If the AppImage does not launch, install fuse2 on ${BOLD}$DISTRO_NAME${NC}:"
        echo -e "  ${GREEN}$cmd${NC}\n"
    else
        echo -e "If the AppImage does not launch, please install fuse2 / libfuse2 using your package manager.\n"
    fi
}

check_disk_space() {
    local target_dir="${1:-$INSTALL_DESTINATION}"
    local need_bytes=$(( 200 * 1024 * 1024 )) # 200 MB
    mkdir -p "$target_dir" 2>/dev/null || true
    if command -v df &>/dev/null; then
        local avail
        avail=$(df --output=avail -B1 "$target_dir" 2>/dev/null | tail -n 1 | tr -d '[:space:]')
        if [[ "$avail" =~ ^[0-9]+$ ]] && (( avail < need_bytes )); then
            echo -e "${RED}[ERROR] Insufficient disk space in $target_dir (less than 200MB free).${NC}" >&2
            return 1
        fi
    fi
    return 0
}

fetch_github_json() {
    local url="$1"
    local tries=3
    local delay=2
    local auth_header=()

    if [ -n "${ASSELLA_GH_TOKEN:-}" ]; then
        auth_header=(-H "Authorization: Bearer $ASSELLA_GH_TOKEN")
    elif [ -n "${GITHUB_TOKEN:-}" ]; then
        auth_header=(-H "Authorization: Bearer $GITHUB_TOKEN")
    fi

    while (( tries-- > 0 )); do
        local body
        if body=$(curl -fsSL -H "Accept: application/vnd.github+json" \
                -H "User-Agent: ASSella-Installer" \
                "${auth_header[@]}" \
                --max-time 15 \
                "$url" 2>/dev/null); then
            printf '%s' "$body"
            return 0
        fi
        sleep "$delay"
        delay=$(( delay * 2 ))
    done
    return 1
}

run_headcrab_installer() {
    echo -e "\n${CYAN}${BOLD}=== Running Headcrab (SLSsteam) Installer ===${NC}"
    detect_steam_paths
    local config_file="$SLS_CONFIG_FILE"
    local backup_config=""
    if [ -n "$config_file" ] && [ -f "$config_file" ]; then
        backup_config=$(mktemp "${config_file}.pre_headcrab.XXXXXX")
        cp -a "$config_file" "$backup_config"
        echo -e "${YELLOW}[INFO] Backed up SLSsteam config before running Headcrab.${NC}"
    fi

    local tmp_headcrab
    tmp_headcrab="$(mktemp -t headcrab.XXXXXX.sh)"
    echo -e "${YELLOW}[INFO] Downloading latest Headcrab installer...${NC}"
    if curl -fsSL --retry 3 --retry-delay 2 "https://raw.githubusercontent.com/Deadboy666/h3adcr-b/main/headcrab.sh" -o "$tmp_headcrab"; then
        chmod +x "$tmp_headcrab"
        if [ -t 0 ]; then
            bash "$tmp_headcrab" || true
        elif [ -r /dev/tty ]; then
            bash "$tmp_headcrab" </dev/tty || true
        else
            bash "$tmp_headcrab" || true
        fi
    else
        echo -e "${RED}[ERROR] Failed to download Headcrab installer.${NC}"
    fi
    rm -f "$tmp_headcrab"

    # Restore and merge any custom sections (AdditionalDepots, DecryptionKeys) that Headcrab stripped
    if [ -n "$backup_config" ] && [ -f "$backup_config" ]; then
        detect_steam_paths
        config_file="$SLS_CONFIG_FILE"
        if [ -n "$config_file" ] && [ -f "$config_file" ]; then
            python3 -c '
import sys

backup_path = sys.argv[1]
current_path = sys.argv[2]

try:
    with open(backup_path, "r", encoding="utf-8") as f:
        bak_lines = f.readlines()
    with open(current_path, "r", encoding="utf-8") as f:
        curr_lines = f.readlines()

    bak_sections = {}
    curr_key = None
    curr_content = []

    for line in bak_lines:
        if line.strip() and not line.startswith(" ") and not line.startswith("\t") and not line.startswith("#") and ":" in line:
            if curr_key:
                bak_sections[curr_key] = "".join(curr_content)
            curr_key = line.split(":", 1)[0].strip()
            curr_content = [line]
        elif curr_key:
            curr_content.append(line)
    if curr_key:
        bak_sections[curr_key] = "".join(curr_content)

    curr_text = "".join(curr_lines)
    missing_sections = []
    protected_keys = ["AdditionalDepots", "DecryptionKeys", "ExtraDepots", "DlcData", "FakeAppIds", "AppList"]
    for k in protected_keys:
        if k in bak_sections and f"{k}:" not in curr_text:
            missing_sections.append(bak_sections[k])

    if missing_sections:
        # Append in-place to preserve system inode
        with open(current_path, "a", encoding="utf-8") as f:
            f.write("\n# Restored by ASSella (protected from Headcrab wipe):\n")
            for sec in missing_sections:
                f.write(sec)
                if not sec.endswith("\n"):
                    f.write("\n")
        print("[INFO] Re-injected protected sections (AdditionalDepots/DecryptionKeys) into SLSsteam config.")
except Exception as e:
    print(f"[WARN] Error restoring protected config sections: {e}")
' "$backup_config" "$config_file" 2>/dev/null || true
        fi
        rm -f "$backup_config"
    fi

    check_headcrab_status
    if [ "$HEADCRAB_INSTALLED" = true ]; then
        echo -e "${GREEN}[OK] Headcrab (SLSsteam) installation completed.${NC}"
    else
        echo -e "${YELLOW}[WARN] Headcrab finished, but ~/.config/SLSsteam was not detected.${NC}"
    fi
}

_parse_release_manifest_json() {
    local rel_json="$1"
    local channel="$2"
    local strict_mode="${3:-}"
    [ -z "$rel_json" ] && return 1

    if command -v python3 &>/dev/null; then
        local parsed
        parsed=$(echo "$rel_json" | python3 -c '
import sys, json, re
from datetime import datetime, timezone

target_channel = (sys.argv[1] if len(sys.argv) > 1 else "beta").strip().lower()
strict_mode = (sys.argv[2] if len(sys.argv) > 2 else "").strip().lower()
if target_channel in ("main", "master", ""):
    target_channel = "stable"

canary_tokens = ("canary", "testing", "devtest", "release_candidate", "releasecandidate")
beta_tokens = ("beta", "dev", "rc", "candidate")
canary_re = re.compile(
    r"(?<![a-z])(canary|testing|devtest|release[_-]?candidate)(?![a-z])",
    re.IGNORECASE,
)
beta_re = re.compile(
    r"(?<![a-z])(beta|dev|rc|candidate)(?![a-z])",
    re.IGNORECASE,
)


def norm_branch(val):
    raw = str(val or "").strip().lower()
    if raw.startswith("refs/heads/"):
        raw = raw[len("refs/heads/"):]
    if raw in ("main", "master"):
        return "main"
    if len(raw) >= 7 and re.fullmatch(r"[0-9a-f]{7,40}", raw):
        return ""
    return raw


def ts_key(rel):
    raw = str(rel.get("published_at") or rel.get("created_at") or "").strip()
    if not raw:
        return 0.0
    try:
        iso = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0


def classify_release(rel):
    tag = str(rel.get("tag_name", "")).strip()
    if not tag:
        return None
    tag_lower = tag.lower()
    prerelease = bool(rel.get("prerelease", False))
    branch = norm_branch(rel.get("target_commitish"))
    manifest_channel = str(rel.get("channel") or "").strip().lower()

    has_canary = bool(canary_re.search(tag_lower))
    has_beta = bool(beta_re.search(tag_lower)) and not has_canary

    if has_canary or branch == "canary" or manifest_channel == "canary":
        return "canary" if prerelease else None
    if has_beta or manifest_channel == "beta" or (prerelease and branch in ("", "beta", "main")):
        return "beta"
    if branch and branch != "main":
        return None
    if manifest_channel and manifest_channel not in ("stable", "main"):
        return None
    clean = tag.lstrip("vV")
    if re.match(r"^\d+\.\d+(\.\d+)?$", clean) or re.search(r"\bhotfix\b|[-._]hotfix\d*", tag_lower):
        return "stable"
    return None


def semver_key(tag, prerelease=False):
    clean = tag.strip().lstrip("vV")
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(.*)$", clean)
    if not m:
        return (-1, -1, -1, 0, 0, clean)
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    suffix = (m.group(4) or "").lower()
    if not suffix:
        tier = 2 if prerelease else 3
        return (major, minor, patch, tier, 0, clean)
    if "hotfix" in suffix:
        hm = re.search(r"hotfix\D*(\d+)", suffix)
        tier = 2 if prerelease else 3
        return (major, minor, patch, tier, int(hm.group(1)) if hm else 1, clean)
    nums = re.findall(r"\d+", suffix)
    sub = int(nums[-1]) if nums else 0
    if canary_re.search(suffix):
        tier = 0
    elif beta_re.search(suffix):
        tier = 2
    else:
        tier = 1
    return (major, minor, patch, tier, sub, clean)


try:
    data = json.load(sys.stdin)
    releases = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
    best = None
    for r in releases:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        tag = str(r.get("tag_name", "")).strip()
        rel_channel = classify_release(r)
        if rel_channel is None:
            continue
        if rel_channel != target_channel:
            continue

        for a in r.get("assets", []):
            if not isinstance(a, dict):
                continue
            name = str(a.get("name", ""))
            dl_url = str(a.get("browser_download_url", ""))
            if (
                dl_url
                and name.lower().endswith(".appimage")
                and not name.lower().endswith(".zsync")
                and not name.lower().endswith(".sha256")
            ):
                sha256_val = str(a.get("sha256") or "").strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", sha256_val):
                    sha256_val = ""
                cand = (
                    ts_key(r),
                    semver_key(tag, prerelease=bool(r.get("prerelease", False))),
                    tag,
                    dl_url,
                    sha256_val,
                )
                if best is None or cand[:2] > best[:2]:
                    best = cand
                break

    if best is not None:
        print(best[2])
        print(best[3])
        print(best[4])
        sys.exit(0)
except Exception:
    sys.exit(1)
' "$channel" "$strict_mode" 2>/dev/null || true)
        if [ -n "$parsed" ]; then
            local tag_name dl_url sha_val
            tag_name=$(printf '%s\n' "$parsed" | sed -n '1p')
            dl_url=$(printf '%s\n' "$parsed" | sed -n '2p')
            sha_val=$(printf '%s\n' "$parsed" | sed -n '3p')
            if [ -n "$tag_name" ]; then LATEST_VER="$tag_name"; fi
            if [ -n "$dl_url" ]; then LATEST_URL="$dl_url"; fi
            LATEST_SHA256="$sha_val"
            [ -n "$LATEST_URL" ] && return 0
        fi
    elif command -v jq &>/dev/null; then
        local parsed
        parsed=$(printf '%s' "$rel_json" | jq -r --arg target "${channel:-beta}" '
            (if ($target | ascii_downcase) | (. == "main" or . == "master" or . == "") then "stable" else ($target | ascii_downcase) end) as $tc
            | def norm_branch:
                (. // "" | ascii_downcase | gsub("^refs/heads/"; "")) as $b
                | if ($b == "main" or $b == "master") then "main"
                  elif ($b | test("^[0-9a-f]{7,40}$")) then ""
                  else $b end;
            def classify:
                (.tag_name // "" | gsub("^[[:space:]]+|[[:space:]]+$"; "")) as $tag
                | ($tag | ascii_downcase) as $tl
                | (.prerelease // false) as $pre
                | (.target_commitish | norm_branch) as $br
                | (.channel // "" | ascii_downcase) as $mc
                | ($tl | test("(^|[^a-z])(canary|testing|devtest|release[_-]?candidate)([^a-z]|$)")) as $hc
                | ((($tl | test("(^|[^a-z])(beta|dev|rc|candidate)([^a-z]|$)")) and ($hc | not))) as $hb
                | if ($tag == "") then null
                  elif ($hc or $br == "canary" or $mc == "canary") then (if $pre then "canary" else null end)
                  elif ($hb or $mc == "beta" or ($pre and ($br == "" or $br == "beta" or $br == "main"))) then "beta"
                  elif ($br != "" and $br != "main") then null
                  elif ($mc != "" and $mc != "stable" and $mc != "main") then null
                  elif (($tag | sub("^[vV]"; "") | test("^[0-9]+\\.[0-9]+(\\.[0-9]+)?$")) or ($tl | test("(^|[-._[:space:]])hotfix([0-9]*|$)"))) then "stable"
                  else null end;
            (if type == "array" then . else [.] end)
            | map(
                select(type == "object" and (.draft | not) and (classify == $tc))
                | . as $r
                | ([ (.assets // [])[]
                     | select(type == "object")
                     | (.name // "") as $n
                     | (.browser_download_url // "") as $u
                     | select(($u != "") and ($n | ascii_downcase | (endswith(".appimage") and (endswith(".zsync") | not) and (endswith(".sha256") | not))))
                     | { url: $u, sha: ((.sha256 // "") | ascii_downcase | if test("^[0-9a-f]{64}$") then . else "" end) }
                   ] | .[0]) as $asset
                | select($asset != null)
                | { ts: ($r.published_at // $r.created_at // ""), tag: $r.tag_name, url: $asset.url, sha: $asset.sha }
            )
            | sort_by(.ts)
            | last
            | select(. != null)
            | "\(.tag)\n\(.url)\n\(.sha)"
        ' 2>/dev/null || true)
        if [ -n "$parsed" ]; then
            local tag_name dl_url sha_val
            tag_name=$(printf '%s\n' "$parsed" | sed -n '1p')
            dl_url=$(printf '%s\n' "$parsed" | sed -n '2p')
            sha_val=$(printf '%s\n' "$parsed" | sed -n '3p')
            if [ -n "$tag_name" ]; then LATEST_VER="$tag_name"; fi
            if [ -n "$dl_url" ]; then LATEST_URL="$dl_url"; fi
            LATEST_SHA256="$sha_val"
            [ -n "$LATEST_URL" ] && return 0
        fi
    fi
    return 1
}

get_latest_github_version() {
    LATEST_VER="Unknown"
    LATEST_URL=""
    LATEST_SHA256=""
    detect_channel

    if [ "$UPDATE_SERVER" = "auto" ] || [ "$UPDATE_SERVER" = "github" ]; then
        REL_JSON=$(fetch_github_json "https://api.github.com/repos/niwia/ASSella/releases?per_page=100" || true)
        if [ -n "$REL_JSON" ]; then
            _parse_release_manifest_json "$REL_JSON" "$TARGET_CHANNEL" || true
        fi
    fi

    # Cloudflare R2 fallback (or primary when UPDATE_SERVER=r2); only stable and beta are mirrored to R2
    if [ -z "$LATEST_URL" ] && { [ "$UPDATE_SERVER" = "auto" ] || [ "$UPDATE_SERVER" = "r2" ]; }; then
        if [ "$TARGET_CHANNEL" = "stable" ] || [ "$TARGET_CHANNEL" = "beta" ]; then
            local r2_manifest_url="${R2_RELEASES_PUBLIC_URL%/}/releases/channels/${TARGET_CHANNEL}.json"
            local r2_json=""
            r2_json=$(curl -fsSL --retry 2 --connect-timeout 5 --max-time 15 "$r2_manifest_url" 2>/dev/null || true)
            if [ -n "$r2_json" ]; then
                _parse_release_manifest_json "$r2_json" "$TARGET_CHANNEL" "exact" || true
            fi
        fi
    fi

    # Channel-aware fallback if GitHub API and R2 both fail (skip GitHub fallback when locked to R2)
    if [ -z "$LATEST_URL" ] && [ "$UPDATE_SERVER" != "r2" ]; then
        if [ "$TARGET_CHANNEL" = "canary" ]; then
            LATEST_VER="v3.0Release_candidate111026001"
            LATEST_URL="https://github.com/niwia/ASSella/releases/download/v3.0Release_candidate111026001/assella3.0canary.appimage"
        elif [ "$TARGET_CHANNEL" = "stable" ]; then
            LATEST_VER="v3.1.0"
            LATEST_URL="https://github.com/niwia/ASSella/releases/download/v3.1.0/ASSella.AppImage"
        else
            LATEST_VER="v2.7.0beta"
            LATEST_URL="https://github.com/niwia/ASSella/releases/download/v2.7.0beta/ASSella.AppImage"
        fi
    fi
}

resolve_beta_and_stable_releases() {
    detect_channel
    BETA_VER="Unavailable"
    BETA_URL=""
    BETA_SHA256=""
    STABLE_VER="Unavailable"
    STABLE_URL=""
    STABLE_SHA256=""

    local rel_json=""
    if [ "$UPDATE_SERVER" = "auto" ] || [ "$UPDATE_SERVER" = "github" ]; then
        rel_json=$(fetch_github_json "https://api.github.com/repos/niwia/ASSella/releases?per_page=100" || true)
        if [ -n "$rel_json" ]; then
            LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
            if _parse_release_manifest_json "$rel_json" "beta" "exact"; then
                BETA_VER="$LATEST_VER"
                BETA_URL="$LATEST_URL"
                BETA_SHA256="$LATEST_SHA256"
            fi
            LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
            if _parse_release_manifest_json "$rel_json" "stable" "exact"; then
                STABLE_VER="$LATEST_VER"
                STABLE_URL="$LATEST_URL"
                STABLE_SHA256="$LATEST_SHA256"
            fi
        fi
    fi

    if [ "$UPDATE_SERVER" = "auto" ] || [ "$UPDATE_SERVER" = "r2" ]; then
        if [ -z "$BETA_URL" ]; then
            local r2_beta=""
            r2_beta=$(curl -fsSL --retry 2 --connect-timeout 5 --max-time 15 "${R2_RELEASES_PUBLIC_URL%/}/releases/channels/beta.json" 2>/dev/null || true)
            if [ -n "$r2_beta" ]; then
                LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
                if _parse_release_manifest_json "$r2_beta" "beta" "exact"; then
                    BETA_VER="$LATEST_VER"
                    BETA_URL="$LATEST_URL"
                    BETA_SHA256="$LATEST_SHA256"
                fi
            fi
        fi
        if [ -z "$STABLE_URL" ]; then
            local r2_stable=""
            r2_stable=$(curl -fsSL --retry 2 --connect-timeout 5 --max-time 15 "${R2_RELEASES_PUBLIC_URL%/}/releases/channels/stable.json" 2>/dev/null || true)
            if [ -n "$r2_stable" ]; then
                LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
                if _parse_release_manifest_json "$r2_stable" "stable" "exact"; then
                    STABLE_VER="$LATEST_VER"
                    STABLE_URL="$LATEST_URL"
                    STABLE_SHA256="$LATEST_SHA256"
                fi
            fi
        fi
    fi

    if [ -z "$BETA_URL" ] && [ "$UPDATE_SERVER" != "r2" ]; then
        BETA_VER="v2.7.0beta"
        BETA_URL="https://github.com/niwia/ASSella/releases/download/v2.7.0beta/ASSella.AppImage"
        BETA_SHA256=""
    fi
    if [ -z "$STABLE_URL" ] && [ "$UPDATE_SERVER" != "r2" ]; then
        STABLE_VER="v3.1.0"
        STABLE_URL="https://github.com/niwia/ASSella/releases/download/v3.1.0/ASSella.AppImage"
        STABLE_SHA256=""
    fi

    if [ "${TARGET_CHANNEL:-beta}" = "stable" ] && [ -n "$STABLE_URL" ]; then
        LATEST_VER="$STABLE_VER"
        LATEST_URL="$STABLE_URL"
        LATEST_SHA256="$STABLE_SHA256"
    elif [ -n "$BETA_URL" ]; then
        LATEST_VER="$BETA_VER"
        LATEST_URL="$BETA_URL"
        LATEST_SHA256="$BETA_SHA256"
    elif [ -n "$STABLE_URL" ]; then
        LATEST_VER="$STABLE_VER"
        LATEST_URL="$STABLE_URL"
        LATEST_SHA256="$STABLE_SHA256"
    else
        LATEST_VER="Unknown"
        LATEST_URL=""
        LATEST_SHA256=""
    fi
}

# ------------------------------------------------------------------------------
#  2. Header & Status Display
# ------------------------------------------------------------------------------
show_header() {
    clear 2>/dev/null || true
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
    echo -e "${GREEN}${BOLD}                         ASSella Installer                         ${NC}"
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
    echo -e "  ${BOLD}OS Detected:${NC}      ${DISTRO_NAME:-Generic Linux} ($(uname -m))"
    if [ "${IS_STEAM_DECK:-false}" = true ]; then
        echo -e "  ${BOLD}Device:${NC}           Steam Deck (SteamOS)"
    fi

    # Steam Client Status
    if [ "$STEAM_TYPE" = "flatpak" ]; then
        echo -e "  ${BOLD}Steam Client:${NC}     ${CYAN}Flatpak (com.valvesoftware.Steam)${NC}"
    elif [ "$STEAM_TYPE" = "snap" ]; then
        echo -e "  ${BOLD}Steam Client:${NC}     ${CYAN}Snap (snap.steam)${NC}"
    elif [ "$STEAM_TYPE" = "native" ] && [ -d "$STEAM_DIR" ]; then
        echo -e "  ${BOLD}Steam Client:${NC}     ${GREEN}Native ($STEAM_DIR)${NC}"
    else
        echo -e "  ${BOLD}Steam Client:${NC}     ${YELLOW}Not Detected${NC}"
    fi

    # Headcrab Status
    if [ "${HEADCRAB_INSTALLED:-false}" = true ]; then
        echo -e "  ${BOLD}Headcrab (SLS):${NC}   ${GREEN}Installed ($SLS_CONFIG_DIR)${NC}"
    else
        echo -e "  ${BOLD}Headcrab (SLS):${NC}   ${YELLOW}Not Detected${NC}"
    fi

    # FUSE Status
    if [ "${IS_NIXOS:-false}" = true ]; then
        echo -e "  ${BOLD}AppImage FUSE:${NC}    ${CYAN}NixOS Mode (Uses steam-run / appimage-run wrapper)${NC}"
    elif [ "${FUSE_INSTALLED:-false}" = true ]; then
        echo -e "  ${BOLD}AppImage FUSE:${NC}    ${GREEN}OK (libfuse.so.2 found)${NC}"
    else
        echo -e "  ${BOLD}AppImage FUSE:${NC}    ${RED}WARNING: fuse2 missing (Required for AppImages)${NC}"
    fi

    # Version Status
    echo -e "  ${BOLD}Local Version:${NC}    ${LOCAL_VER:-Not Installed}"
    if [ -n "${LATEST_VER:-}" ] && [ "${LATEST_VER}" != "Unknown" ]; then
        echo -e "  ${BOLD}Latest Online:${NC}    ${LATEST_VER}"
    fi
    echo -e "${CYAN}${BOLD}===================================================================${NC}"
}

# ------------------------------------------------------------------------------
#  4. Distro-Specific Requirements Guide
# ------------------------------------------------------------------------------
pause_if_interactive() {
    if [ "${INTERACTIVE:-false}" = true ]; then
        read -p "Press Enter to continue..." dummy
    fi
}

show_distro_guide() {
    show_header
    echo -e "\n${YELLOW}${BOLD}=== Distro-Specific Setup & Requirements ===${NC}\n"

    if [ "$IS_NIXOS" = true ]; then
        echo -e "${CYAN}${BOLD}NixOS Installation Guide:${NC}"
        echo -e "NixOS does not use standard /lib64 glibc linkers out of the box."
        echo -e "ASSella automatically generates a launcher using ${GREEN}steam-run${NC} or ${GREEN}appimage-run${NC}.\n"
        echo -e "  ${BOLD}Command to launch directly:${NC}"
        echo -e "    ${GREEN}nix-shell -p steam-run --run 'steam-run ~/.local/share/ACCELA/ASSella.AppImage'${NC}\n"
        echo -e "  ${BOLD}Global fix (Optional):${NC} Add ${GREEN}programs.nix-ld.enable = true;${NC} in /etc/nixos/configuration.nix"
    else
        local family
        family=$(detect_distro_family)
        local cmd="${PKG_FUSE_CMD[$family]:-}"
        echo -e "${CYAN}${BOLD}AppImage FUSE Requirements for $DISTRO_NAME:${NC}"
        echo -e "Modern Linux systems require ${GREEN}fuse2${NC} (libfuse.so.2) to execute AppImages.\n"
        if [ -n "$cmd" ]; then
            echo -e "  ${BOLD}Install command:${NC}"
            echo -e "    ${GREEN}$cmd${NC}"
        else
            echo -e "  ${BOLD}Install command:${NC}"
            echo -e "    Install ${GREEN}fuse2${NC} or ${GREEN}libfuse2${NC} via your system package manager."
        fi
    fi

    echo -e "\n${CYAN}${BOLD}-------------------------------------------------------------------${NC}\n"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  5. Main Installation & Update Engine
# ------------------------------------------------------------------------------
_release_tag_ge() {
    local cand_tag="$1"
    local req_tag="$2"
    if [ -z "$req_tag" ] || [ "$req_tag" = "Unknown" ]; then
        return 0
    fi
    if [ -z "$cand_tag" ] || [ "$cand_tag" = "Unknown" ]; then
        return 1
    fi
    if command -v python3 &>/dev/null; then
        python3 -c '
import re, sys

def key(tag):
    clean = re.sub(r"^[0-9]{8}\+[a-zA-Z0-9._-]+-", "", tag.strip()).lstrip("vV")
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$", clean)
    if not m:
        return (0, 0, 0, 0, 0)
    maj, min_, pat = int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0)
    suf = (m.group(4) or "").lower()
    if not suf:
        return (maj, min_, pat, 0, 0)
    if "hotfix" in suf:
        hm = re.search(r"hotfix\D*(\d+)", suf)
        return (maj, min_, pat, 1, int(hm.group(1)) if hm else 1)
    nums = re.findall(r"\d+", suf)
    sub = int(nums[-1]) if nums else 1
    if re.search(r"(?<![a-z])(canary|testing|devtest)(?![a-z])", suf):
        w = -4
    elif re.search(r"(?<![a-z])dev(?![a-z])", suf):
        w = -3
    elif re.search(r"(?<![a-z])beta(?![a-z])", suf):
        w = -2
    elif re.search(r"(?<![a-z])(rc|release[_-]?candidate)(?![a-z])", suf):
        w = -1
    else:
        w = -5
    return (maj, min_, pat, w, sub)

sys.exit(0 if key(sys.argv[1]) >= key(sys.argv[2]) else 1)
' "$cand_tag" "$req_tag" 2>/dev/null
        return $?
    fi
    [ "$(printf '%s' "${cand_tag#v}" | tr '[:upper:]' '[:lower:]')" = "$(printf '%s' "${req_tag#v}" | tr '[:upper:]' '[:lower:]')" ]
}

_fallback_to_r2_release() {
    local channel="$1"
    local req_tag="$2"
    if [ "$channel" != "stable" ] && [ "$channel" != "beta" ]; then
        return 1
    fi
    local prev_ver="$LATEST_VER"
    local prev_url="$LATEST_URL"
    local prev_sha="$LATEST_SHA256"
    local r2_manifest_url="${R2_RELEASES_PUBLIC_URL%/}/releases/channels/${channel}.json"
    local r2_json=""
    r2_json=$(curl -fsSL --retry 2 --connect-timeout 5 --max-time 15 "$r2_manifest_url" 2>/dev/null || true)
    if [ -z "$r2_json" ]; then
        return 1
    fi
    LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
    if ! _parse_release_manifest_json "$r2_json" "$channel" "exact"; then
        LATEST_VER="$prev_ver"; LATEST_URL="$prev_url"; LATEST_SHA256="$prev_sha"
        return 1
    fi
    if [ -z "$LATEST_URL" ] || ! _release_tag_ge "$LATEST_VER" "$req_tag"; then
        echo -e "${YELLOW}[WARN] R2 mirror holds ${LATEST_VER:-unknown}, older than requested ${req_tag:-unknown}.${NC}"
        LATEST_VER="$prev_ver"; LATEST_URL="$prev_url"; LATEST_SHA256="$prev_sha"
        return 1
    fi
    return 0
}

_attempt_download_and_verify() {
    local DL_TMP="$1"
    local allow_interactive="${2:-true}"

    # --- Delta (zsync2 / zsync) fast path ------------------------------------
    local used_zsync=false
    local zsync_bin=""
    if command -v zsync2 &>/dev/null; then
        zsync_bin="zsync2"
    elif command -v zsync &>/dev/null; then
        zsync_bin="zsync"
    fi
    if [ "${ASSELLA_NO_ZSYNC:-0}" != "1" ] && [ -n "$zsync_bin" ] && [ -f "$INSTALL_DESTINATION/ASSella.AppImage" ]; then
        echo -e "${CYAN}[INFO] $zsync_bin delta update available — syncing changed blocks only.${NC}"
        local zsync_url="${LATEST_URL}.zsync"
        if curl -fsL --retry 3 --retry-delay 2 --max-time 30 -o "${DL_TMP}.zsync" "$zsync_url" 2>/dev/null; then
            local zsync_log="${DL_TMP}.zsync.log"
            local -a zsync_cmd=("$zsync_bin")
            if [ "$zsync_bin" = "zsync2" ]; then
                zsync_cmd+=(-u "$LATEST_URL")
            fi
            zsync_cmd+=(-i "$INSTALL_DESTINATION/ASSella.AppImage" -o "$DL_TMP" "$zsync_url")
            if "${zsync_cmd[@]}" >"$zsync_log" 2>&1; then
                [ -s "$zsync_log" ] && sed 's/^/    /' "$zsync_log"
                used_zsync=true
                echo -e "${GREEN}[INFO] $zsync_bin delta sync complete.${NC}"
            else
                [ -s "$zsync_log" ] && sed 's/^/    /' "$zsync_log"
                echo -e "${YELLOW}[WARN] $zsync_bin delta sync failed; falling back to full download.${NC}"
                rm -f "$DL_TMP"
            fi
            rm -f "$zsync_log"
        else
            echo -e "${YELLOW}[INFO] No .zsync published for this release; using full download.${NC}"
        fi
        rm -f "${DL_TMP}.zsync"
    elif [ "${ASSELLA_NO_ZSYNC:-0}" != "1" ] && [ -z "$zsync_bin" ]; then
        echo -e "${YELLOW}[INFO] zsync / zsync2 not installed — using full download.${NC}"
    fi

    if [ "$used_zsync" = false ]; then
        if ! curl -fL --retry 3 --retry-delay 2 --progress-bar -o "$DL_TMP" "$LATEST_URL"; then
            echo -e "${RED}[ERROR] Download failed from $LATEST_URL.${NC}"
            rm -f "$DL_TMP"
            return 2
        fi
    fi

    # --- Integrity check -----------------------------------------------------
    local sha_state="missing"
    local expected_sha="" actual_sha=""

    if curl -fsL --retry 3 --retry-delay 2 --max-time 20 -o "$DL_TMP.sha256" "${LATEST_URL}.sha256" 2>/dev/null; then
        expected_sha=$(grep -oiE '[a-f0-9]{64}' "$DL_TMP.sha256" | head -n1 || true)
        [ -z "$expected_sha" ] && sha_state="malformed"
    elif [ -n "${LATEST_SHA256:-}" ]; then
        expected_sha=$(printf '%s' "${LATEST_SHA256}" | grep -oiE '^[a-f0-9]{64}$' | head -n1 || true)
    fi
    if [ -n "$expected_sha" ]; then
        if command -v sha256sum &>/dev/null; then
            actual_sha=$(sha256sum "$DL_TMP" | awk '{print $1}')
        elif command -v shasum &>/dev/null; then
            actual_sha=$(shasum -a 256 "$DL_TMP" | awk '{print $1}')
        fi
        if [ -z "$actual_sha" ]; then
            sha_state="notool"
        elif [ "$(printf '%s' "$expected_sha" | tr 'A-F' 'a-f')" = "$(printf '%s' "$actual_sha" | tr 'A-F' 'a-f')" ]; then
            sha_state="ok"
        else
            sha_state="mismatch"
        fi
    fi
    rm -f "$DL_TMP.sha256"

    case "$sha_state" in
        ok)
            echo -e "${GREEN}[OK] SHA-256 verified: $actual_sha${NC}"
            return 0
            ;;
        mismatch)
            echo -e "${RED}${BOLD}[ERROR] CHECKSUM MISMATCH — download corrupted or tampered.${NC}"
            echo -e "${RED}  expected: $expected_sha${NC}"
            echo -e "${RED}  actual:   $actual_sha${NC}"
            local proceed="n"
            if [ "${SKIP_CHECKSUM:-false}" = true ] || [ "${ASSELLA_SKIP_CHECKSUM:-0}" = "1" ]; then
                echo -e "${YELLOW}[WARN] Checksum mismatch bypassed via flag/environment.${NC}"
                proceed="y"
            elif [ "$allow_interactive" = true ] && { [ -t 0 ] || [ -r /dev/tty ]; }; then
                proceed=$(prompt_user "Do you want to ignore the checksum mismatch and proceed anyway? [y/N]: " "n")
            fi

            if [[ "$proceed" =~ ^[yY] ]]; then
                echo -e "${YELLOW}[WARN] Proceeding with installation despite checksum mismatch...${NC}"
                return 0
            fi
            rm -f "$DL_TMP"
            return 3
            ;;
        malformed|notool)
            echo -e "${RED}[WARN] Checksum file unusable ($sha_state); cannot verify integrity.${NC}"
            return 0
            ;;
        missing)
            echo -e "${YELLOW}[WARN] No published .sha256 for this release; integrity NOT verified.${NC}"
            if [ "${ASSELLA_REQUIRE_SHA256:-0}" = "1" ]; then
                echo -e "${RED}[ERROR] ASSELLA_REQUIRE_SHA256=1 set, refusing unverified download.${NC}"
                rm -f "$DL_TMP"
                return 3
            fi
            return 0
            ;;
    esac
}

do_install() {
    echo -e "\n${YELLOW}[INFO] Installing / Updating ASSella...${NC}"
    mkdir -p "$INSTALL_DESTINATION"

    check_disk_space "$INSTALL_DESTINATION" || {
        pause_if_interactive
        return 1
    }

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage" ] && [ ! -L "$INSTALL_DESTINATION/ACCELA.AppImage" ]; then
        echo -e "${YELLOW}[INFO] Existing ACCELA.AppImage found (will back up after verification).${NC}"
    fi

    if [ -n "${PRESELECTED_URL:-}" ]; then
        LATEST_VER="${PRESELECTED_VER:-Unknown}"
        LATEST_URL="${PRESELECTED_URL}"
        LATEST_SHA256="${PRESELECTED_SHA256:-}"
        PRESELECTED_VER=""
        PRESELECTED_URL=""
        PRESELECTED_SHA256=""
    else
        get_latest_github_version
    fi
    if [ -z "$LATEST_URL" ]; then
        echo -e "${RED}[ERROR] Could not resolve a release download URL from update server '$UPDATE_SERVER' ($TARGET_CHANNEL).${NC}"
        pause_if_interactive
        return 1
    fi
    echo -e "${YELLOW}[INFO] Downloading ASSella.AppImage ($LATEST_VER)...${NC}"

    TMP_DIR="$(mktemp -d -t assella-install-XXXXXX)" || {
        echo -e "${RED}[ERROR] Could not create temporary download directory.${NC}"
        pause_if_interactive
        return 1
    }
    local DL_TMP="$TMP_DIR/ASSella.AppImage"

    local can_try_r2=false
    if [ "$UPDATE_SERVER" = "auto" ] && { [ "$TARGET_CHANNEL" = "stable" ] || [ "$TARGET_CHANNEL" = "beta" ]; }; then
        local r2_prefix="${R2_RELEASES_PUBLIC_URL%/}/"
        if [ "$LATEST_URL" = "${LATEST_URL#"$r2_prefix"}" ] && [[ "$LATEST_URL" != *.r2.dev/* ]]; then
            can_try_r2=true
        fi
    fi

    local dl_rc=0
    local allow_prompt="true"
    [ "$can_try_r2" = true ] && allow_prompt="false"
    _attempt_download_and_verify "$DL_TMP" "$allow_prompt" || dl_rc=$?

    if [ "$dl_rc" -ne 0 ] && [ "$can_try_r2" = true ]; then
        echo -e "${YELLOW}[WARN] Primary download/verification failed; trying Cloudflare R2 mirror...${NC}"
        local requested_ver="$LATEST_VER"
        if _fallback_to_r2_release "$TARGET_CHANNEL" "$requested_ver"; then
            echo -e "${YELLOW}[INFO] Downloading ASSella.AppImage ($LATEST_VER) from Cloudflare R2...${NC}"
            dl_rc=0
            _attempt_download_and_verify "$DL_TMP" "true" || dl_rc=$?
        fi
    fi

    if [ "$dl_rc" -ne 0 ]; then
        rm -f "$DL_TMP"
        echo -e "${RED}Refusing to install. Existing installation left untouched.${NC}"
        pause_if_interactive
        return 1
    fi

    # Promote verified download into place safely with rollback
    local backup_target="$INSTALL_DESTINATION/ASSella.AppImage.bak"
    local had_previous=0
    if [ -f "$INSTALL_DESTINATION/ASSella.AppImage" ]; then
        had_previous=1
        mv -f "$INSTALL_DESTINATION/ASSella.AppImage" "$backup_target"
    fi

    if ! mv -f "$DL_TMP" "$INSTALL_DESTINATION/ASSella.AppImage"; then
        echo -e "${RED}[ERROR] Failed to move verified binary into place.${NC}"
        if [ "$had_previous" -eq 1 ] && [ -f "$backup_target" ]; then
            echo -e "${YELLOW}[INFO] Restoring previous ASSella.AppImage from backup...${NC}"
            mv -f "$backup_target" "$INSTALL_DESTINATION/ASSella.AppImage"
        fi
        rm -f "$DL_TMP"
        pause_if_interactive
        return 1
    fi

    rm -f "$backup_target"
    chmod +x "$INSTALL_DESTINATION/ASSella.AppImage"

    # Backup previous non-symlink ACCELA.AppImage
    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage" ] && [ ! -L "$INSTALL_DESTINATION/ACCELA.AppImage" ]; then
        echo -e "${YELLOW}[INFO] Backing up previous ACCELA.AppImage to ACCELA.AppImage.bak...${NC}"
        mv -f "$INSTALL_DESTINATION/ACCELA.AppImage" "$INSTALL_DESTINATION/ACCELA.AppImage.bak"
    fi

    # Save local version file
    echo "$LATEST_VER" > "$VERSION_FILE"

    # Compatibility symlink ACCELA.AppImage -> ASSella.AppImage
    rm -f "$INSTALL_DESTINATION/ACCELA.AppImage"
    ln -s "ASSella.AppImage" "$INSTALL_DESTINATION/ACCELA.AppImage"
    echo -e "${GREEN}[INFO] Created compatibility symlink ACCELA.AppImage -> ASSella.AppImage.${NC}"

    # Handle NixOS Launcher Script
    local EXEC_COMMAND=""
    if [ "$IS_NIXOS" = true ]; then
        echo -e "${YELLOW}[INFO] Configuring NixOS compatibility launcher...${NC}"
        cat >"$NIXOS_LAUNCHER" <<'EOL'
#!/usr/bin/env bash
if command -v steam-run &>/dev/null; then
    exec steam-run "$HOME/.local/share/ACCELA/ASSella.AppImage" "$@"
elif command -v appimage-run &>/dev/null; then
    exec appimage-run "$HOME/.local/share/ACCELA/ASSella.AppImage" "$@"
else
    echo "NixOS detected! Please install steam-run or appimage-run to launch ASSella:"
    echo "  nix-shell -p steam-run --run 'steam-run ~/.local/share/ACCELA/ASSella.AppImage'"
    read -p "Press Enter to exit..."
fi
EOL
        chmod +x "$NIXOS_LAUNCHER"
        EXEC_COMMAND="$NIXOS_LAUNCHER %u"
    else
        EXEC_COMMAND="$INSTALL_DESTINATION/ACCELA.AppImage %u"
    fi

    # Desktop Shortcut Creation
    echo -e "${YELLOW}[INFO] Configuring desktop shortcut ($PRIMARY_DESKTOP_ENTRY)...${NC}"
    write_desktop_entry

    # Application Icon Download
    echo -e "${YELLOW}[INFO] Applying application icon...${NC}"
    install_application_icon

    # Update system desktop database
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi
    if [ -z "${XDG_CURRENT_DESKTOP:-}" ] || [[ "$XDG_CURRENT_DESKTOP" != *"KDE"* ]]; then
        command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    fi

    # Check FUSE
    check_fuse_status
    if [ "$FUSE_INSTALLED" = false ] && [ "$IS_NIXOS" = false ]; then
        local family
        family=$(detect_distro_family)
        local cmd="${PKG_FUSE_CMD[$family]:-}"
        echo -e "\n${RED}${BOLD}[WARN] FUSE (libfuse.so.2) is not installed on your system!${NC}"
        if [ -n "$cmd" ]; then
            echo -e "${YELLOW}Please run: $cmd${NC}"
        fi
    fi

    echo -e "\n${GREEN}${BOLD}=========================================${NC}"
    echo -e "${GREEN}${BOLD}[OK] ASSella has been installed & patched!${NC}"
    echo -e "${GREEN}${BOLD}=========================================${NC}\n"

    get_local_version
}

# ------------------------------------------------------------------------------
#  6. Restore Original ACCELA Backup
# ------------------------------------------------------------------------------
do_restore_accela() {
    echo -e "\n${YELLOW}[INFO] Restoring original ACCELA backup...${NC}"

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage.bak" ]; then
        rm -f "$INSTALL_DESTINATION/ACCELA.AppImage"
        mv "$INSTALL_DESTINATION/ACCELA.AppImage.bak" "$INSTALL_DESTINATION/ACCELA.AppImage"
        chmod +x "$INSTALL_DESTINATION/ACCELA.AppImage"
        echo -e "${GREEN}[INFO] Restored ACCELA.AppImage from backup.${NC}"
    else
        echo -e "${YELLOW}[WARN] No ACCELA.AppImage.bak found to restore.${NC}"
    fi

    if [ -f "$PRIMARY_DESKTOP_ENTRY" ]; then
        sed -i 's/^Name=ASSella$/Name=ACCELA/' "$PRIMARY_DESKTOP_ENTRY"
        echo -e "${GREEN}[INFO] Restored desktop entry name to ACCELA in $PRIMARY_DESKTOP_ENTRY.${NC}"
    fi

    echo -e "\n${GREEN}[OK] ACCELA restoration complete!${NC}"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  7. Clean Uninstall ASSella
# ------------------------------------------------------------------------------
do_uninstall() {
    echo -e "\n${RED}${BOLD}=== ASSella Clean Uninstaller ===${NC}\n"
    if [ "${INTERACTIVE:-false}" = true ]; then
        read -p "Are you sure you want to uninstall ASSella? [y/N]: " confirm
        if [[ "$confirm" != "y" ]] && [[ "$confirm" != "Y" ]]; then
            echo "Uninstallation cancelled."
            return
        fi
    fi

    echo -e "${YELLOW}[INFO] Removing binaries, symlinks, and desktop entries...${NC}"
    rm -f "$INSTALL_DESTINATION/ASSella.AppImage"
    rm -f "$INSTALL_DESTINATION/ACCELA.AppImage"
    rm -f "$INSTALL_DESTINATION/version"
    rm -f "$NIXOS_LAUNCHER"
    rm -f "$PRIMARY_DESKTOP_ENTRY"
    rm -f "$LEGACY_DESKTOP_ENTRY"
    rm -f "$PRIMARY_ICON_PATH"
    rm -f "$LEGACY_ICON_PATH"
    rm -f "$PRIMARY_SVG_ICON_PATH"
    rm -f "$LEGACY_SVG_ICON_PATH"

    if [ -f "$INSTALL_DESTINATION/ACCELA.AppImage.bak" ]; then
        mv "$INSTALL_DESTINATION/ACCELA.AppImage.bak" "$INSTALL_DESTINATION/ACCELA.AppImage"
        echo -e "${GREEN}[INFO] Restored original ACCELA.AppImage backup.${NC}"
    fi

    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi

    echo -e "\n${GREEN}[OK] ASSella has been uninstalled.${NC}"
    pause_if_interactive
}

# ------------------------------------------------------------------------------
#  8. Pre-Flight Diagnostics
# ------------------------------------------------------------------------------
run_diagnostics() {
    show_header
    echo -e "\n${YELLOW}${BOLD}=== ASSella Pre-Flight Diagnostics ===${NC}\n"

    echo -n "  1. Python 3: "
    if command -v python3 &>/dev/null; then
        echo -e "${GREEN}OK ($(python3 --version | cut -d' ' -f2))${NC}"
    else
        echo -e "${RED}MISSING${NC}"
    fi

    echo -n "  2. Curl utility: "
    if command -v curl &>/dev/null; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}MISSING${NC}"
    fi

    echo -n "  3. FUSE (libfuse.so.2): "
    if [ "$FUSE_INSTALLED" = true ] || [ "$IS_NIXOS" = true ]; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}MISSING (AppImage won't open without fuse2)${NC}"
    fi

    echo -n "  4. Steam Client: "
    if [ "$STEAM_TYPE" = "flatpak" ]; then
        echo -e "${GREEN}FLATPAK ($STEAM_DIR)${NC}"
    elif [ "$STEAM_TYPE" = "snap" ]; then
        echo -e "${GREEN}SNAP ($STEAM_DIR)${NC}"
    elif [ "$STEAM_TYPE" = "native" ] && [ -d "$STEAM_DIR" ]; then
        echo -e "${GREEN}NATIVE ($STEAM_DIR)${NC}"
    else
        echo -e "${YELLOW}NOT DETECTED${NC}"
    fi

    echo -n "  5. Headcrab (SLSsteam): "
    if [ "$HEADCRAB_INSTALLED" = true ]; then
        echo -e "${GREEN}INSTALLED ($SLS_CONFIG_DIR)${NC}"
    else
        echo -e "${YELLOW}NOT INSTALLED${NC}"
    fi

    echo -n "  6. Desktop Shortcut: "
    if [ -f "$PRIMARY_DESKTOP_ENTRY" ]; then
        echo -e "${GREEN}OK ($PRIMARY_DESKTOP_ENTRY)${NC}"
    elif [ -f "$LEGACY_DESKTOP_ENTRY" ]; then
        echo -e "${GREEN}OK (legacy: $LEGACY_DESKTOP_ENTRY)${NC}"
    else
        echo -e "${YELLOW}NOT CREATED YET${NC}"
    fi

    echo -e "\n${CYAN}${BOLD}-------------------------------------------------------------------${NC}\n"
    read -p "Press Enter to return to main menu..." dummy
}

# ------------------------------------------------------------------------------
#  9. Interactive Setup Wizard & Menu Loop
# ------------------------------------------------------------------------------
interactive_flow() {
    INTERACTIVE=true
    show_header
    echo -e "${BOLD}${CYAN}Welcome to the ASSella Setup Wizard${NC}\n"

    # Step 1: Headcrab (SLSsteam)
    echo -e "${BOLD}--- [Step 1/2] Headcrab (SLSsteam) Setup ---${NC}"
    check_headcrab_status
    local default_hc="n"
    local hc_prompt=""
    if [ "$HEADCRAB_INSTALLED" = true ]; then
        echo -e "  Status: ${GREEN}Installed ($SLS_CONFIG_DIR)${NC}"
        hc_prompt="Do you want to re-run / update Headcrab (SLSsteam)? [y/N]: "
        default_hc="n"
    else
        echo -e "  Status: ${YELLOW}Not Detected${NC}"
        echo -e "  ${CYAN}Headcrab is required for SLSsteam integration and game launching.${NC}"
        hc_prompt="Do you want to install Headcrab (SLSsteam) now? [Y/n]: "
        default_hc="y"
    fi

    local run_hc=""
    run_hc=$(prompt_user "$hc_prompt" "$default_hc")
    if [[ "$run_hc" =~ ^[yY] ]]; then
        run_headcrab_installer
    else
        echo -e "${YELLOW}[INFO] Skipping Headcrab installation.${NC}"
    fi

    # FUSE 2 Check
    check_and_warn_fuse

    echo ""
    # Step 2: ASSella
    echo -e "${BOLD}--- [Step 2/2] ASSella Application Setup ---${NC}"
    get_local_version
    resolve_beta_and_stable_releases
    echo -e "  Currently Installed: ${YELLOW}$LOCAL_VER${NC}"
    if [ -n "${BETA_URL:-}" ]; then
        echo -e "  ${BOLD}[1]${NC} Latest Beta:   ${CYAN}$BETA_VER${NC} (${CYAN}Beta / Pre-release${NC})"
    else
        echo -e "  ${BOLD}[1]${NC} Latest Beta:   ${YELLOW}Unavailable${NC}"
    fi
    if [ -n "${STABLE_URL:-}" ]; then
        echo -e "  ${BOLD}[2]${NC} Latest Stable: ${GREEN}$STABLE_VER${NC} (${GREEN}Stable / Main${NC})"
    else
        echo -e "  ${BOLD}[2]${NC} Latest Stable: ${YELLOW}Unavailable${NC}"
    fi

    local default_track="1"
    if [ "${TARGET_CHANNEL:-}" = "stable" ] && [ -n "${STABLE_URL:-}" ]; then
        default_track="2"
    elif [ -z "${BETA_URL:-}" ] && [ -n "${STABLE_URL:-}" ]; then
        default_track="2"
    fi

    local track_choice=""
    track_choice=$(prompt_user "Choose release to install [1=Beta / 2=Stable / n=Skip] (default: $default_track): " "$default_track")
    case "${track_choice,,}" in
        1|b|beta)
            TARGET_CHANNEL="beta"
            if [ -n "${BETA_URL:-}" ]; then
                PRESELECTED_VER="$BETA_VER"
                PRESELECTED_URL="$BETA_URL"
                PRESELECTED_SHA256="${BETA_SHA256:-}"
            fi
            do_install
            ;;
        2|s|stable)
            TARGET_CHANNEL="stable"
            if [ -n "${STABLE_URL:-}" ]; then
                PRESELECTED_VER="$STABLE_VER"
                PRESELECTED_URL="$STABLE_URL"
                PRESELECTED_SHA256="${STABLE_SHA256:-}"
            fi
            do_install
            ;;
        n|no|skip|q)
            echo -e "${YELLOW}[INFO] Skipping ASSella installation.${NC}"
            ;;
        *)
            if [[ "$track_choice" =~ ^[yY] ]]; then
                if [ "$default_track" = "2" ]; then
                    TARGET_CHANNEL="stable"
                    PRESELECTED_VER="${STABLE_VER:-}"
                    PRESELECTED_URL="${STABLE_URL:-}"
                    PRESELECTED_SHA256="${STABLE_SHA256:-}"
                else
                    TARGET_CHANNEL="beta"
                    PRESELECTED_VER="${BETA_VER:-}"
                    PRESELECTED_URL="${BETA_URL:-}"
                    PRESELECTED_SHA256="${BETA_SHA256:-}"
                fi
                do_install
            else
                echo -e "${YELLOW}[INFO] Skipping ASSella installation.${NC}"
            fi
            ;;
    esac

    echo -e "\n${GREEN}${BOLD}Setup completed!${NC}\n"
}

interactive_menu() {
    INTERACTIVE=true
    detect_distro
    check_fuse_status
    check_headcrab_status
    get_local_version
    get_latest_github_version

    while true; do
        show_header
        echo -e "  ${BOLD}[1]${NC} Install / Update ASSella"
        echo -e "  ${BOLD}[2]${NC} Run Headcrab (SLSsteam) Installer"
        echo -e "  ${BOLD}[3]${NC} Check for Updates & View Release Info"
        echo -e "  ${BOLD}[4]${NC} View Distro Requirements Guide"
        echo -e "  ${BOLD}[5]${NC} Restore Original ACCELA (Revert Backup)"
        echo -e "  ${BOLD}[6]${NC} Uninstall ASSella (Clean Files & Shortcuts)"
        echo -e "  ${BOLD}[7]${NC} Run Pre-Flight Diagnostics"
        echo -e "  ${BOLD}[8]${NC} Exit"
        echo -e "${CYAN}${BOLD}===================================================================${NC}"
        read -p "Select option [1-8]: " choice

        case "$choice" in
            1)
                do_install
                read -p "Press Enter to continue..." dummy
                ;;
            2)
                run_headcrab_installer
                read -p "Press Enter to continue..." dummy
                ;;
            3)
                show_header
                echo -e "\n${YELLOW}Installed:${NC} $LOCAL_VER  -->  ${GREEN}Latest Online ($TARGET_CHANNEL):${NC} $LATEST_VER"
                if [ "$LOCAL_VER" != "$LATEST_VER" ]; then
                    echo -e "${GREEN}${BOLD}An update is available! Select Option 1 to update.${NC}\n"
                else
                    echo -e "${GREEN}You are already on the latest version!${NC}\n"
                fi
                read -p "Press Enter to continue..." dummy
                ;;
            4)
                show_distro_guide
                ;;
            5)
                do_restore_accela
                ;;
            6)
                do_uninstall
                ;;
            7)
                run_diagnostics
                ;;
            8|q|Q)
                echo "Exiting..."
                exit 0
                ;;
            *)
                echo -e "${RED}Invalid option!${NC}"
                sleep 1
                ;;
        esac
    done
}

# ------------------------------------------------------------------------------
#  10. CLI Execution Entry Point
# ------------------------------------------------------------------------------
main() {
    detect_distro
    check_fuse_status
    check_headcrab_status
    get_local_version

    local non_interactive=false
    local action="interactive"
    local run_hc=false

    while [ $# -gt 0 ]; do
        case "$1" in
            --canary)
                TARGET_CHANNEL="canary"
                ;;
            --beta)
                TARGET_CHANNEL="beta"
                ;;
            --stable)
                TARGET_CHANNEL="stable"
                ;;
            --server)
                if [ $# -lt 2 ]; then
                    echo "Missing value for --server (expected: auto, github, or r2)"
                    exit 1
                fi
                shift
                case "${1,,}" in
                    auto|github|r2)
                        UPDATE_SERVER="${1,,}"
                        ;;
                    *)
                        echo "Invalid --server value: $1 (expected: auto, github, or r2)"
                        exit 1
                        ;;
                esac
                ;;
            --server=*)
                local srv_val="${1#*=}"
                case "${srv_val,,}" in
                    auto|github|r2)
                        UPDATE_SERVER="${srv_val,,}"
                        ;;
                    *)
                        echo "Invalid --server value: $srv_val (expected: auto, github, or r2)"
                        exit 1
                        ;;
                esac
                ;;
            --r2)
                UPDATE_SERVER="r2"
                ;;
            --headcrab|--sls)
                action="headcrab"
                ;;
            --with-headcrab)
                run_hc=true
                action="install"
                ;;
            --install|-i)
                action="install"
                ;;
            --yes|-y)
                non_interactive=true
                ;;
            --menu|-m)
                action="menu"
                ;;
            --update|-u)
                action="update"
                ;;
            --restore)
                action="restore"
                ;;
            --uninstall)
                action="uninstall"
                ;;
            --skip-checksum|--ignore-checksum|--no-verify)
                SKIP_CHECKSUM=true
                ;;
            --help|-h)
                action="help"
                ;;
            *)
                echo "Unknown option: $1"
                echo "Use ./install.sh --help for usage instructions."
                exit 1
                ;;
        esac
        shift
    done

    detect_channel

    case "$action" in
        interactive)
            if [ "$non_interactive" = true ]; then
                do_install
            elif [ -t 0 ] || [ -r /dev/tty ]; then
                interactive_flow
            else
                do_install
            fi
            ;;
        headcrab)
            run_headcrab_installer
            ;;
        install)
            if [ "$run_hc" = true ]; then
                run_headcrab_installer
            fi
            do_install
            ;;
        menu)
            interactive_menu
            ;;
        update)
            get_latest_github_version
            if [ -z "$LATEST_URL" ] || [ "$LATEST_VER" = "Unknown" ]; then
                echo -e "${RED}[ERROR] Could not check updates from '$UPDATE_SERVER' ($TARGET_CHANNEL).${NC}"
                exit 1
            elif [ "$LOCAL_VER" != "$LATEST_VER" ]; then
                do_install
            else
                echo -e "${GREEN}ASSella is already up to date ($LOCAL_VER) on channel ($TARGET_CHANNEL).${NC}"
            fi
            ;;
        restore)
            do_restore_accela
            ;;
        uninstall)
            do_uninstall
            ;;
        help)
            echo "ASSella Installer & Management Suite"
            echo "Usage: ./install.sh [OPTIONS]"
            echo ""
            echo "Channel & Server Options:"
            echo "  --beta           Target Beta channel (latest pre-release + beta builds)"
            echo "  --canary         Target Canary channel (nightly / testing builds)"
            echo "  --stable         Target Stable channel"
            echo "  --server MODE    Update source: auto (default), github, or r2"
            echo "  --r2             Shortcut for --server r2"
            echo ""
            echo "Execution Options:"
            echo "  (no args)        Interactive wizard (prompts for Headcrab & ASSella)"
            echo "  --install, -i    Install or update ASSella directly"
            echo "  --with-headcrab  Install Headcrab (SLSsteam) followed by ASSella"
            echo "  --headcrab       Run Headcrab (SLSsteam) installer only"
            echo "  --yes, -y        Non-interactive / assume yes"
            echo "  --menu, -m       Launch full maintenance menu"
            echo "  --update, -u     Check and update if a new release exists"
            echo "  --restore        Restore original ACCELA backup"
            echo "  --uninstall      Uninstall ASSella"
            echo "  --skip-checksum  Ignore checksum mismatch and proceed with installation"
            echo "  --help, -h       Display this help message"
            ;;
    esac
}

if [ "${#BASH_SOURCE[@]}" -eq 0 ] || [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
