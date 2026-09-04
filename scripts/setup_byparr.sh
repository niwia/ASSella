#!/usr/bin/env bash
# ==============================================================================
# ACCELA - Byparr Cloudflare Solver Setup Script
# Automatically sets up Byparr inside ~/.local/share/ACCELA/byparr.
# Managed directly by ACCELA (starts when ACCELA opens, closes when ACCELA exits).
# No root / sudo required.
# ==============================================================================

set -e

echo "=== ACCELA: Setting up Byparr Cloudflare Solver ==="

ACCELA_DIR="$HOME/.local/share/ACCELA"
BYPARR_DIR="$ACCELA_DIR/byparr"
LOCAL_BIN="$HOME/.local/bin"

mkdir -p "$LOCAL_BIN" "$ACCELA_DIR"
export PATH="$LOCAL_BIN:$PATH"

# 1. Install uv (fast Python & environment manager) if not present
if ! command -v uv &>/dev/null; then
    echo "[1/4] Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv &>/dev/null; then
    echo "ERROR: Failed to install 'uv'. Please ensure curl is available."
    exit 1
fi
echo "[1/4] uv is ready: $(uv --version)"

# 2. Clone or update Byparr repository
if [ -d "$BYPARR_DIR/.git" ]; then
    echo "[2/4] Updating existing Byparr repository in $BYPARR_DIR..."
    git -C "$BYPARR_DIR" pull --ff-only || true
else
    echo "[2/4] Cloning Byparr repository to $BYPARR_DIR..."
    rm -rf "$BYPARR_DIR"
    git clone --depth 1 https://github.com/ThePhaseless/Byparr.git "$BYPARR_DIR"
fi

# 3. Create the ACCELA supervisor runner for automatic cleanup on crash/exit
echo "[3/4] Writing process supervisor..."
cat <<'EOF' > "$BYPARR_DIR/accela_runner.py"
#!/usr/bin/env python3
"""
ACCELA Byparr Process Supervisor.
Monitors the ACCELA parent PID. If ACCELA exits or crashes abruptly,
this supervisor immediately terminates Byparr, Uvicorn, and all spawned
invisible-playwright Firefox browsers so zero background processes linger.
"""
import os
import sys
import time
import signal
import threading

parent_pid_str = os.environ.get("ASSELLA_PID") or os.environ.get("ACCELA_PID")
parent_pid = int(parent_pid_str) if parent_pid_str and parent_pid_str.isdigit() else 0

def _watchdog():
    if not parent_pid:
        return
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [ASSELLA_RUNNER] Supervisor active. Monitoring parent ASSELLA PID {parent_pid}...", flush=True)
    while True:
        time.sleep(1.0)
        try:
            os.kill(parent_pid, 0)
        except OSError:
            # Parent ASSELLA is dead! Suicide the process group immediately.
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [ASSELLA_RUNNER] Parent ASSELLA PID {parent_pid} terminated or crashed. Terminating Byparr process tree.", flush=True)
            try:
                os.killpg(0, signal.SIGKILL)
            except Exception:
                pass
            os._exit(0)

if parent_pid:
    t = threading.Thread(target=_watchdog, daemon=True)
    t.start()

if __name__ == "__main__":
    import uvicorn
    from src.consts import HOST, PORT
    from main import app
    uvicorn.run(app, host=HOST, port=PORT)
EOF

chmod +x "$BYPARR_DIR/assella_runner.py"
cp -f "$BYPARR_DIR/assella_runner.py" "$BYPARR_DIR/accela_runner.py" 2>/dev/null || true

# 4. Initialize Python 3.14 & download the stealth browser
echo "[4/4] Initializing Python environment and downloading stealth browser..."
cd "$BYPARR_DIR"
INVPW_TRUE_HEADLESS=1 uv run python main.py --init

# Copy script to ACCELA dir for easy offline access
cp -f "$0" "$ACCELA_DIR/setup_byparr.sh" 2>/dev/null || true

echo ""
echo "=========================================================================="
echo "  Byparr setup successfully completed!"
echo "  Location: $BYPARR_DIR"
echo "  ACCELA will now automatically start Byparr when opened,"
echo "  and stop it (freeing all memory & CPU) when closed or crashed."
echo "=========================================================================="
