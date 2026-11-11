#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REAL_HOME="$HOME"
TEST_HOME=$(mktemp -d)
export HOME="$TEST_HOME"
export XDG_DATA_HOME="$TEST_HOME/.local/share"
export XDG_CONFIG_HOME="$TEST_HOME/.config"
trap 'rm -rf "$TEST_HOME"' EXIT

# Source the beta installer first so this test exercises its real desktop-entry
# and scalable SVG icon helpers without downloading a release or running the UI.
# shellcheck source=/dev/null
source "$REPO_DIR/install.sh"

# install.sh installs its own cleanup trap, replacing the one above. Re-register
# one that also removes the test home, or every run leaks a temp directory.
trap 'cleanup; rm -rf "$TEST_HOME"' EXIT INT TERM

DESKTOP_DIR="$TEST_HOME/.local/share/applications"
PRIMARY_DESKTOP_ENTRY="$DESKTOP_DIR/assella.desktop"
LEGACY_DESKTOP_ENTRY="$DESKTOP_DIR/accela.desktop"
EXEC_COMMAND="$TEST_HOME/.local/share/ACCELA/ASSella.AppImage %u"

mkdir -p "$DESKTOP_DIR"

# Reproduce the reported state: both desktop names point at each other.
ln -s accela.desktop "$PRIMARY_DESKTOP_ENTRY"
ln -s assella.desktop "$LEGACY_DESKTOP_ENTRY"

write_desktop_entry

test -f "$PRIMARY_DESKTOP_ENTRY"
test ! -L "$PRIMARY_DESKTOP_ENTRY"
test ! -e "$LEGACY_DESKTOP_ENTRY"
grep -q '^Exec=' "$PRIMARY_DESKTOP_ENTRY"
grep -q '^Icon=assella$' "$PRIMARY_DESKTOP_ENTRY"
grep -q '^Comment=Steam Game & Depot Management Suite$' "$PRIMARY_DESKTOP_ENTRY"
grep -q '^StartupWMClass=ASSella$' "$PRIMARY_DESKTOP_ENTRY"
test -z "$DESKTOP_TMP"

# Test that paths containing spaces get properly quoted around the executable
EXEC_COMMAND="$TEST_HOME/Steam Deck User/.local/share/ACCELA/ASSella.AppImage %u"
write_desktop_entry
grep -q '^Exec="' "$PRIMARY_DESKTOP_ENTRY"
grep -q 'ASSella.AppImage" %u$' "$PRIMARY_DESKTOP_ENTRY"

# Test icon preservation on network failure (scalable SVG + PNG fallback)
ICON_DIR="$TEST_HOME/.local/share/icons/hicolor/256x256/apps"
PRIMARY_ICON_PATH="$ICON_DIR/assella.png"
LEGACY_ICON_PATH="$ICON_DIR/accela.png"
SCALABLE_ICON_DIR="$TEST_HOME/.local/share/icons/hicolor/scalable/apps"
PRIMARY_SVG_ICON_PATH="$SCALABLE_ICON_DIR/assella.svg"
LEGACY_SVG_ICON_PATH="$SCALABLE_ICON_DIR/accela.svg"
mkdir -p "$ICON_DIR" "$SCALABLE_ICON_DIR"
echo "existing icon data" > "$PRIMARY_ICON_PATH"
echo "<svg/>" > "$PRIMARY_SVG_ICON_PATH"
# Mock curl failure by pointing to non-existent local port
if command -v curl &>/dev/null; then
    curl() { return 1; }
    install_application_icon
    unset -f curl
    test -f "$PRIMARY_ICON_PATH"
    test "$(cat "$PRIMARY_ICON_PATH")" = "existing icon data"
    test -f "$PRIMARY_SVG_ICON_PATH"
    test "$(cat "$PRIMARY_SVG_ICON_PATH")" = "<svg/>"
    test -L "$LEGACY_SVG_ICON_PATH"
    test -z "$ICON_TMP"
    test -z "$SVG_ICON_TMP"
fi

# Interrupted-run cleanup: tracked temp files and stale hidden mktemp leftovers
# must be removed, while the committed desktop entry survives.
DESKTOP_TMP=$(mktemp "$DESKTOP_DIR/.assella.desktop.tmp.XXXXXX")
ICON_TMP=$(mktemp "$ICON_DIR/.assella.png.tmp.XXXXXX")
SVG_ICON_TMP=$(mktemp "$SCALABLE_ICON_DIR/.assella.svg.tmp.XXXXXX")
stale_desktop="$DESKTOP_DIR/.assella.desktop.tmp.stale"
stale_icon="$ICON_DIR/.assella.png.tmp.stale"
stale_svg_icon="$SCALABLE_ICON_DIR/.assella.svg.tmp.stale"
: > "$stale_desktop"
: > "$stale_icon"
: > "$stale_svg_icon"
cleanup
test ! -e "$DESKTOP_TMP"
test ! -e "$ICON_TMP"
test ! -e "$SVG_ICON_TMP"
test ! -e "$stale_desktop"
test ! -e "$stale_icon"
test ! -e "$stale_svg_icon"
test -f "$PRIMARY_DESKTOP_ENTRY"
DESKTOP_TMP=""
ICON_TMP=""
SVG_ICON_TMP=""

# Stdin pipe execution (curl ... | bash): BASH_SOURCE must not trigger an
# unbound variable error under set -u, and must execute main.
help_output=$(cat "$REPO_DIR/install.sh" | bash -s -- --help)
echo "$help_output" | grep -q 'ASSella Installer & Management Suite'
echo "$help_output" | grep -q -- '--server MODE'

# Strict channel filtering, v3.0.1 exclusion, published_at ordering, and SHA256 extraction
FAKE_RELEASES_JSON='[
  {"tag_name":"v3.0.1","prerelease":false,"draft":false,"target_commitish":"beta","published_at":"2026-10-22T15:04:23Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.0.1/ASSella.AppImage"}]},
  {"tag_name":"v3.0.5-beta.1","prerelease":true,"draft":false,"target_commitish":"beta","published_at":"2026-10-15T10:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.0.5-beta.1/ASSella.AppImage"}]},
  {"tag_name":"v2.9.9beta","prerelease":true,"draft":false,"target_commitish":"beta","published_at":"2026-11-11T12:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v2.9.9beta/ASSella.AppImage","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]},
  {"tag_name":"v2.9.9","prerelease":true,"draft":false,"target_commitish":"beta","published_at":"2026-11-11T11:30:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v2.9.9/ASSella.AppImage"}]},
  {"tag_name":"v3.1.1","prerelease":false,"draft":false,"target_commitish":"main","published_at":"2026-11-11T12:05:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.1.1/ASSella.AppImage","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]},
  {"tag_name":"v3.3.0-canary.1","prerelease":false,"draft":false,"target_commitish":"canary","published_at":"2026-11-11T14:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.3.0-canary.1/ASSella.AppImage"}]},
  {"tag_name":"v3.2.0-canary.1","prerelease":true,"draft":false,"target_commitish":"canary","published_at":"2026-11-11T13:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.2.0-canary.1/ASSella.AppImage"}]},
  {"tag_name":"v3.1.0","prerelease":false,"draft":false,"target_commitish":"main","published_at":"2026-11-10T10:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.1.0/ASSella.AppImage"}]}
]'
LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
_parse_release_manifest_json "$FAKE_RELEASES_JSON" "stable"
test "$LATEST_VER" = "v3.1.1"
test "$LATEST_SHA256" = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
# Both exact and normal beta CLI strictly resolve beta-only (v2.9.9beta beats older v3.0.5-beta.1),
# never stable v3.1.1 and never historical prerelease=false v3.0.1.
LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
_parse_release_manifest_json "$FAKE_RELEASES_JSON" "beta" "exact"
test "$LATEST_VER" = "v2.9.9beta"
test "$LATEST_SHA256" = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
_parse_release_manifest_json "$FAKE_RELEASES_JSON" "beta"
test "$LATEST_VER" = "v2.9.9beta"
# If only v3.0.1 (prerelease=false, target_commitish=beta) is present, beta rejects it.
LATEST_VER=""; LATEST_URL=""
! _parse_release_manifest_json '[{"tag_name":"v3.0.1","prerelease":false,"draft":false,"target_commitish":"beta","published_at":"2026-10-22T15:04:23Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.0.1/ASSella.AppImage"}]}]' "beta"
# Canary requires prerelease=true (v3.3.0-canary.1 with prerelease=false is rejected)
LATEST_VER=""; LATEST_URL=""
_parse_release_manifest_json "$FAKE_RELEASES_JSON" "canary"
test "$LATEST_VER" = "v3.2.0-canary.1"

# Also test resolve_beta_and_stable_releases populates BETA_* and STABLE_* including SHA256
fetch_github_json() { printf '%s' "$FAKE_RELEASES_JSON"; }
resolve_beta_and_stable_releases
unset -f fetch_github_json
test "$BETA_VER" = "v2.9.9beta"
test "$BETA_URL" = "https://example.invalid/v2.9.9beta/ASSella.AppImage"
test "$BETA_SHA256" = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
test "$STABLE_VER" = "v3.1.1"
test "$STABLE_URL" = "https://example.invalid/v3.1.1/ASSella.AppImage"
test "$STABLE_SHA256" = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

# Verify .zsync.sha256 fallback is removed and zsync2 support is present in both installers
for inst_script in "$REPO_DIR/install.sh" "$REPO_DIR/install_stable.sh"; do
    if grep -q 'zsync\.sha256' "$inst_script"; then
        echo "Unexpected .zsync.sha256 fallback present in $inst_script" >&2
        exit 1
    fi
    grep -q 'command -v zsync2' "$inst_script"
done

# Verify zsync2 delta + LATEST_SHA256 verification path end-to-end with stubbed curl & zsync2
(
    FAKE_BIN="$TEST_HOME/fake_bin"
    mkdir -p "$FAKE_BIN" "$INSTALL_DESTINATION"
    cat > "$FAKE_BIN/zsync2" <<'ZEOF'
#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        *) shift ;;
    esac
done
printf 'stub zsync2 updated binary payload' > "$out"
ZEOF
    chmod +x "$FAKE_BIN/zsync2"
    PAYLOAD_SHA=$(printf 'stub zsync2 updated binary payload' | sha256sum | awk '{print $1}')
    : > "$INSTALL_DESTINATION/ASSella.AppImage"
    curl() {
        local out="" url=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -o) out="$2"; shift 2 ;;
                http://*|https://*) url="$1"; shift ;;
                *) shift ;;
            esac
        done
        if [[ "$url" == *.zsync ]]; then
            : > "$out"
            return 0
        fi
        return 1
    }
    PATH="$FAKE_BIN:$PATH"
    TARGET_CHANNEL="beta"
    PRESELECTED_VER="v2.9.9beta"
    PRESELECTED_URL="https://example.invalid/v2.9.9beta/ASSella.AppImage"
    PRESELECTED_SHA256="$PAYLOAD_SHA"
    do_install
    actual_installed=$(sha256sum "$INSTALL_DESTINATION/ASSella.AppImage" | awk '{print $1}')
    test "$actual_installed" = "$PAYLOAD_SHA"
)

# Verify python-less fallback: strict via jq if installed, and fails closed without python3/jq (never unfiltered grep)
(
    EMPTY_BIN="$TEST_HOME/empty_bin"
    mkdir -p "$EMPTY_BIN"
    for cmd in grep sed awk cut tr printf cat; do
        if command -v "$cmd" &>/dev/null; then
            ln -sf "$(command -v "$cmd")" "$EMPTY_BIN/$cmd"
        fi
    done
    PATH="$EMPTY_BIN"
    LATEST_VER=""; LATEST_URL=""
    if _parse_release_manifest_json "$FAKE_RELEASES_JSON" "beta"; then
        echo "_parse_release_manifest_json must not use unfiltered grep when both python3 and jq are absent" >&2
        exit 1
    fi
)
if command -v jq &>/dev/null; then
    (
        JQ_ONLY_BIN="$TEST_HOME/jq_only_bin"
        mkdir -p "$JQ_ONLY_BIN"
        for cmd in jq grep sed awk cut tr printf cat; do
            ln -sf "$(command -v "$cmd")" "$JQ_ONLY_BIN/$cmd"
        done
        PATH="$JQ_ONLY_BIN"
        LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
        _parse_release_manifest_json "$FAKE_RELEASES_JSON" "beta"
        test "$LATEST_VER" = "v2.9.9beta"
        test "$LATEST_SHA256" = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
fi

# Also verify install_stable.sh strictly filters out prereleases, betas, drafts, canaries & non-main branches.
(
    source "$REPO_DIR/install_stable.sh"
    trap 'cleanup; rm -rf "$TEST_HOME"' EXIT INT TERM
    rm -f "$PRIMARY_DESKTOP_ENTRY" "$LEGACY_DESKTOP_ENTRY"
    ln -s accela.desktop "$PRIMARY_DESKTOP_ENTRY"
    ln -s assella.desktop "$LEGACY_DESKTOP_ENTRY"
    write_desktop_entry
    test -f "$PRIMARY_DESKTOP_ENTRY"
    test ! -L "$PRIMARY_DESKTOP_ENTRY"
    test ! -e "$LEGACY_DESKTOP_ENTRY"

    stable_help=$(cat "$REPO_DIR/install_stable.sh" | bash -s -- --help)
    echo "$stable_help" | grep -q 'ASSella Installer & Management Suite (Stable)'
    echo "$stable_help" | grep -q -- '--server MODE'
    if echo "$stable_help" | grep -q -- '--beta'; then
        echo "install_stable.sh --help unexpectedly lists --beta" >&2
        exit 1
    fi

    if bash "$REPO_DIR/install_stable.sh" --beta 2>/dev/null; then
        echo "install_stable.sh should reject --beta" >&2
        exit 1
    fi

    STABLE_FILTER_JSON='[
      {"tag_name":"v3.9.9","prerelease":true,"draft":false,"target_commitish":"main","published_at":"2026-11-11T15:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.9.9-pre/ASSella.AppImage"}]},
      {"tag_name":"v3.8.0-beta.1","prerelease":false,"draft":false,"target_commitish":"main","published_at":"2026-11-11T14:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.8.0-beta/ASSella.AppImage"}]},
      {"tag_name":"v3.7.0","prerelease":false,"draft":true,"target_commitish":"main","published_at":"2026-11-11T13:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.7.0-draft/ASSella.AppImage"}]},
      {"tag_name":"v3.6.0","prerelease":false,"draft":false,"target_commitish":"beta","published_at":"2026-11-11T12:30:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.6.0-beta-branch/ASSella.AppImage"}]},
      {"tag_name":"v3.1.1","prerelease":false,"draft":false,"target_commitish":"main","published_at":"2026-11-11T12:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.1.1/ASSella.AppImage","sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}]},
      {"tag_name":"v3.1.0","prerelease":false,"draft":false,"target_commitish":"main","published_at":"2026-11-10T10:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/v3.1.0/ASSella.AppImage"}]}
    ]'
    LATEST_VER=""; LATEST_URL=""; LATEST_SHA256=""
    _parse_release_manifest_json "$STABLE_FILTER_JSON"
    test "$LATEST_VER" = "v3.1.1"
    test "$LATEST_URL" = "https://example.invalid/v3.1.1/ASSella.AppImage"
    test "$LATEST_SHA256" = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
)

# Verify detect_channel classifies clean stable & hotfix builds as stable (beta only for beta/unknown),
# token-aware classifiers reject alpha/latest, jq uses token-aware canary rules, and 'auto' downloads
# fall back from failed GitHub downloads/checksums to newer-or-equal R2 mirrors in both installers.
(
    source "$REPO_DIR/install.sh"
    trap 'cleanup; rm -rf "$TEST_HOME"' EXIT INT TERM

    TARGET_CHANNEL=""; LOCAL_VER="v3.1.0"; detect_channel; test "$TARGET_CHANNEL" = "stable"
    TARGET_CHANNEL=""; LOCAL_VER="3.0.4"; detect_channel; test "$TARGET_CHANNEL" = "stable"
    TARGET_CHANNEL=""; LOCAL_VER="v2.6.2-hotfix1"; detect_channel; test "$TARGET_CHANNEL" = "stable"
    TARGET_CHANNEL=""; LOCAL_VER="20261109+ASSella-3.1.0"; detect_channel; test "$TARGET_CHANNEL" = "stable"
    TARGET_CHANNEL=""; LOCAL_VER="2.9.9beta"; detect_channel; test "$TARGET_CHANNEL" = "beta"
    TARGET_CHANNEL=""; LOCAL_VER="2.7.0dev"; detect_channel; test "$TARGET_CHANNEL" = "beta"
    TARGET_CHANNEL=""; LOCAL_VER="Unknown"; detect_channel; test "$TARGET_CHANNEL" = "beta"
    TARGET_CHANNEL=""; LOCAL_VER="3.0.0testing051026003"; detect_channel; test "$TARGET_CHANNEL" = "canary"
    TARGET_CHANNEL=""; LOCAL_VER="v3.2.0-canary.1"; detect_channel; test "$TARGET_CHANNEL" = "canary"
    TARGET_CHANNEL=""; LOCAL_VER="v3.0Release_candidate111026001"; detect_channel; test "$TARGET_CHANNEL" = "canary"

    TARGET_CHANNEL=""
    LOCAL_VER="v3.1.0"
    fetch_github_json() { printf '%s' "$FAKE_RELEASES_JSON"; }
    resolve_beta_and_stable_releases
    unset -f fetch_github_json
    test "$TARGET_CHANNEL" = "stable"

    TARGET_CHANNEL=""
    LOCAL_VER="Unknown"
    fetch_github_json() { printf '%s' "$FAKE_RELEASES_JSON"; }
    resolve_beta_and_stable_releases
    unset -f fetch_github_json
    test "$TARGET_CHANNEL" = "beta"

    TOKEN_FILTER_JSON='[
      {"tag_name":"v3.9.9-alpha.1","prerelease":true,"draft":false,"target_commitish":"","published_at":"2026-11-12T15:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/alpha/ASSella.AppImage"}]},
      {"tag_name":"v3.9.9-latest","prerelease":true,"draft":false,"target_commitish":"","published_at":"2026-11-12T14:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/latest/ASSella.AppImage"}]},
      {"tag_name":"v3.0Release_candidate111026001","prerelease":true,"draft":false,"target_commitish":"","published_at":"2026-11-11T10:00:00Z","assets":[{"name":"ASSella.AppImage","browser_download_url":"https://example.invalid/rc-canary/ASSella.AppImage"}]}
    ]'
    LATEST_VER=""; LATEST_URL=""
    _parse_release_manifest_json "$TOKEN_FILTER_JSON" "canary"
    test "$LATEST_VER" = "v3.0Release_candidate111026001"
    if command -v jq &>/dev/null; then
        JQ_ONLY_BIN="$TEST_HOME/jq_canary_bin"
        mkdir -p "$JQ_ONLY_BIN"
        for cmd in jq grep sed awk cut tr printf cat; do
            ln -sf "$(command -v "$cmd")" "$JQ_ONLY_BIN/$cmd"
        done
        (
            PATH="$JQ_ONLY_BIN"
            LATEST_VER=""; LATEST_URL=""
            _parse_release_manifest_json "$TOKEN_FILTER_JSON" "canary"
            test "$LATEST_VER" = "v3.0Release_candidate111026001"
        )
    fi

    # Version comparison & R2 fallback checks under UPDATE_SERVER=auto vs github
    _release_tag_ge "v2.9.9beta" "v2.9.9beta"
    _release_tag_ge "v3.1.1" "v2.9.9beta"
    ! _release_tag_ge "v2.9.8beta" "v2.9.9beta"

    R2_PAYLOAD="r2 verified beta binary"
    R2_SHA=$(printf '%s' "$R2_PAYLOAD" | sha256sum | awk '{print $1}')
    mkdir -p "$INSTALL_DESTINATION"
    rm -f "$INSTALL_DESTINATION/ASSella.AppImage"

    # Case 1: UPDATE_SERVER=auto falls back to R2 when GitHub full download fails
    curl() {
        local out="" url=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -o) out="$2"; shift 2 ;;
                http://*|https://*) url="$1"; shift ;;
                *) shift ;;
            esac
        done
        case "$url" in
            */releases/channels/beta.json)
                if [ -n "$out" ]; then
                    printf '{"tag_name":"v2.9.9beta","prerelease":true,"assets":[{"name":"ASSella.AppImage","browser_download_url":"https://r2.example.invalid/beta/ASSella.AppImage","sha256":"%s"}]}' "$R2_SHA" > "$out"
                else
                    printf '{"tag_name":"v2.9.9beta","prerelease":true,"assets":[{"name":"ASSella.AppImage","browser_download_url":"https://r2.example.invalid/beta/ASSella.AppImage","sha256":"%s"}]}' "$R2_SHA"
                fi
                return 0
                ;;
            https://r2.example.invalid/beta/ASSella.AppImage)
                if [ -n "$out" ]; then
                    printf '%s' "$R2_PAYLOAD" > "$out"
                else
                    printf '%s' "$R2_PAYLOAD"
                fi
                return 0
                ;;
            *)
                return 1
                ;;
        esac
    }
    UPDATE_SERVER="auto"
    TARGET_CHANNEL="beta"
    RESOLVED_FROM_SOURCE="github"
    PRESELECTED_VER="v2.9.9beta"
    PRESELECTED_URL="https://github.invalid/v2.9.9beta/ASSella.AppImage"
    PRESELECTED_SHA256="$R2_SHA"
    do_install
    actual_r2_sha=$(sha256sum "$INSTALL_DESTINATION/ASSella.AppImage" | awk '{print $1}')
    test "$actual_r2_sha" = "$R2_SHA"

    # Case 2: UPDATE_SERVER=github stays strict and NEVER falls back to R2
    rm -f "$INSTALL_DESTINATION/ASSella.AppImage"
    UPDATE_SERVER="github"
    TARGET_CHANNEL="beta"
    RESOLVED_FROM_SOURCE="github"
    PRESELECTED_VER="v2.9.9beta"
    PRESELECTED_URL="https://github.invalid/v2.9.9beta/ASSella.AppImage"
    PRESELECTED_SHA256="$R2_SHA"
    if do_install; then
        echo "UPDATE_SERVER=github must not fall back to R2" >&2
        exit 1
    fi

    # Case 3: Stale R2 mirror tag (< GitHub resolved tag) is rejected cleanly
    UPDATE_SERVER="auto"
    TARGET_CHANNEL="beta"
    RESOLVED_FROM_SOURCE="github"
    LATEST_VER="v2.9.9beta"
    curl() {
        local out="" url=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -o) out="$2"; shift 2 ;;
                http://*|https://*) url="$1"; shift ;;
                *) shift ;;
            esac
        done
        if [[ "$url" == */releases/channels/beta.json ]]; then
            local body='{"tag_name":"v2.0.0beta","prerelease":true,"assets":[{"name":"ASSella.AppImage","browser_download_url":"https://r2.example.invalid/old/ASSella.AppImage"}]}'
            if [ -n "$out" ]; then printf '%s' "$body" > "$out"; else printf '%s' "$body"; fi
            return 0
        fi
        return 1
    }
    if _fallback_to_r2_release "beta" "v2.9.9beta"; then
        echo "Stale R2 tag v2.0.0beta must not overwrite v2.9.9beta" >&2
        exit 1
    fi
)

# Verify install_stable.sh auto R2 download fallback and stale R2 tag guard
(
    source "$REPO_DIR/install_stable.sh"
    trap 'cleanup; rm -rf "$TEST_HOME"' EXIT INT TERM
    test "${INSTALL_DESTINATION#"$TEST_HOME"}" != "$INSTALL_DESTINATION"
    STABLE_R2_PAYLOAD="r2 verified stable binary"
    STABLE_R2_SHA=$(printf '%s' "$STABLE_R2_PAYLOAD" | sha256sum | awk '{print $1}')
    mkdir -p "$INSTALL_DESTINATION"
    rm -f "$INSTALL_DESTINATION/ASSella.AppImage"

    get_latest_github_version() {
        LATEST_VER="v3.1.1"
        LATEST_URL="https://github.invalid/v3.1.1/ASSella.AppImage"
        LATEST_SHA256="$STABLE_R2_SHA"
    }
    curl() {
        local out="" url=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -o) out="$2"; shift 2 ;;
                http://*|https://*) url="$1"; shift ;;
                *) shift ;;
            esac
        done
        case "$url" in
            */releases/channels/stable.json)
                if [ -n "$out" ]; then
                    printf '{"tag_name":"v3.1.1","prerelease":false,"assets":[{"name":"ASSella.AppImage","browser_download_url":"https://r2.example.invalid/stable/ASSella.AppImage","sha256":"%s"}]}' "$STABLE_R2_SHA" > "$out"
                else
                    printf '{"tag_name":"v3.1.1","prerelease":false,"assets":[{"name":"ASSella.AppImage","browser_download_url":"https://r2.example.invalid/stable/ASSella.AppImage","sha256":"%s"}]}' "$STABLE_R2_SHA"
                fi
                return 0
                ;;
            https://r2.example.invalid/stable/ASSella.AppImage)
                if [ -n "$out" ]; then
                    printf '%s' "$STABLE_R2_PAYLOAD" > "$out"
                else
                    printf '%s' "$STABLE_R2_PAYLOAD"
                fi
                return 0
                ;;
            *)
                return 1
                ;;
        esac
    }
    UPDATE_SERVER="auto"
    TARGET_CHANNEL="stable"
    RESOLVED_FROM_SOURCE="github"
    do_install
    actual_stable_sha=$(sha256sum "$INSTALL_DESTINATION/ASSella.AppImage" | awk '{print $1}')
    test "$actual_stable_sha" = "$STABLE_R2_SHA"

    LATEST_VER="v3.1.1"
    curl() {
        local out="" url=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -o) out="$2"; shift 2 ;;
                http://*|https://*) url="$1"; shift ;;
                *) shift ;;
            esac
        done
        if [[ "$url" == */releases/channels/stable.json ]]; then
            local body='{"tag_name":"v3.0.4","prerelease":false,"assets":[{"name":"ASSella.AppImage","browser_download_url":"https://r2.example.invalid/old/ASSella.AppImage"}]}'
            if [ -n "$out" ]; then printf '%s' "$body" > "$out"; else printf '%s' "$body"; fi
            return 0
        fi
        return 1
    }
    if _fallback_to_r2_release "v3.1.1"; then
        echo "install_stable.sh must reject stale R2 mirror v3.0.4 for v3.1.1" >&2
        exit 1
    fi
)

echo "desktop shortcut symlink-loop & robust installer regression: PASS"

