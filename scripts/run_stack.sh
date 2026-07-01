#!/usr/bin/env bash
# ABOUTME: Boot Temporal + worker + web app together with an auto-restart
# ABOUTME: supervisor. `run_stack.sh booth` also opens Chrome kiosks without reload.
#
# Usage: scripts/run_stack.sh [dev|booth]
#   dev   - Temporal dev server + worker + FastAPI UI (Ctrl-C stops all).
#   booth - same, then open the game + display in Chrome kiosks.
#
# The worker and web app auto-restart if they die (booth-friendly). Temporal
# dying still shuts the stack down — restarting it would lose in-memory
# workflow state.
set -euo pipefail

MODE="${1:-dev}"

# Auto-restart caps: give up if a process dies more than MAX_RESTARTS times
# within RESTART_WINDOW seconds, and back off briefly between restarts.
MAX_RESTARTS=5
RESTART_WINDOW=60
RESTART_BACKOFF=2

# Current booth display layout:
#   TH-65EQ2 game display: -58,-1080
#   HDMI-OPT fan display:  1862,-581 (mirrored HDMI-OPT pair shares this space)
# Override these if macOS rearranges displays.
BOOTH_GAME_WINDOW_POSITION="${BOOTH_GAME_WINDOW_POSITION:--58,-1080}"
BOOTH_FAN_WINDOW_POSITION="${BOOTH_FAN_WINDOW_POSITION:-1862,-581}"
BOOTH_WINDOW_SIZE="${BOOTH_WINDOW_SIZE:-1920,1080}"

temporal_pid=""
worker_pid=""
ui_pid=""
game_profile=""
display_profile=""
shutting_down=0

worker_restart_count=0
worker_window_start=0
ui_restart_count=0
ui_window_start=0

cleanup() {
    shutting_down=1
    for pid in "$ui_pid" "$worker_pid" "$temporal_pid"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    # Close our kiosk Chrome windows (matched by their throwaway profile dir, so
    # we never touch the user's normal Chrome) and remove the temp dirs.
    for prof in "$game_profile" "$display_profile"; do
        if [ -n "$prof" ]; then
            pkill -f "$prof" 2>/dev/null || true
            rm -rf "$prof" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT INT TERM

start_worker() {
    uv run python -m durable_wordle.worker &
    worker_pid=$!
}

start_ui() {
    if [ "$MODE" = "booth" ]; then
        uv run uvicorn --factory durable_wordle.api:create_production_app &
    else
        uv run uvicorn --factory durable_wordle.api:create_production_app --reload &
    fi
    ui_pid=$!
}

# allow_restart <name>: track a rolling restart window for the named process.
# Returns 0 if a restart is within budget, 1 if the cap was exceeded.
allow_restart() {
    local name="$1"
    local count_var="${name}_restart_count"
    local window_var="${name}_window_start"
    local now window_start count
    now=$(date +%s)
    window_start="${!window_var}"
    count="${!count_var}"
    if [ "$((now - window_start))" -gt "$RESTART_WINDOW" ]; then
        eval "$window_var=$now"
        eval "$count_var=1"
        return 0
    fi
    count=$((count + 1))
    eval "$count_var=$count"
    if [ "$count" -gt "$MAX_RESTARTS" ]; then
        return 1
    fi
    return 0
}

launch_kiosks() {
    # Locate Chrome (common PATH names, or the macOS app bundle).
    local chrome_bin=""
    for candidate in google-chrome google-chrome-stable chromium chrome; do
        if command -v "$candidate" >/dev/null 2>&1; then chrome_bin="$candidate"; break; fi
    done
    if [ -z "$chrome_bin" ] && [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
        chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    fi
    if [ -z "$chrome_bin" ]; then
        echo "Chrome not found — open the URLs below manually."
        return
    fi
    echo "Launching Chrome kiosks (game + display)..."
    # Separate throwaway profiles → two independent, clean kiosk windows
    # (a shared profile would just open a tab in the first instance).
    game_profile="$(mktemp -d)"
    display_profile="$(mktemp -d)"
    # --kiosk --app: chromeless fullscreen. The rest suppress the
    # first-run/restore-pages/crash bubbles after a hard kill, and silence
    # Chrome's background chatter (GCM/push registration, sync, telemetry,
    # component updates) that otherwise floods this terminal with
    # PHONE_REGISTRATION_ERROR / GCM login errors — none of it is needed for a
    # kiosk. --log-level=3 = fatal only; stderr is also sent to /dev/null.
    local flags="--kiosk --no-first-run --no-default-browser-check --noerrdialogs"
    flags="$flags --disable-session-crashed-bubble --disable-infobars"
    flags="$flags --disable-features=Translate --disable-background-networking"
    flags="$flags --disable-sync --disable-component-update --metrics-recording-only"
    flags="$flags --log-level=3"
    echo "Game kiosk target: TH-65EQ2 at ${BOOTH_GAME_WINDOW_POSITION}"
    echo "Fan kiosk target:  HDMI-OPT at ${BOOTH_FAN_WINDOW_POSITION}"
    "$chrome_bin" $flags \
        --window-position="$BOOTH_GAME_WINDOW_POSITION" \
        --window-size="$BOOTH_WINDOW_SIZE" \
        --user-data-dir="$game_profile" \
        --app="http://localhost:8000" >/dev/null 2>&1 &
    sleep 2
    "$chrome_bin" $flags \
        --window-position="$BOOTH_FAN_WINDOW_POSITION" \
        --window-size="$BOOTH_WINDOW_SIZE" \
        --user-data-dir="$display_profile" \
        --app="http://localhost:8000/display" >/dev/null 2>&1 &
}

# ── Pre-flight: required ports must be free ──────────────────────────────────
for port in 7233 8233 8000; do
    if lsof -ti :"$port" >/dev/null 2>&1; then
        echo "Port $port is already in use — stop the other process (e.g. a stray 'just dev'/'just booth') and retry." >&2
        exit 1
    fi
done

# ── Temporal ─────────────────────────────────────────────────────────────────
temporal server start-dev --ui-port 8233 &
temporal_pid=$!

echo "Waiting for Temporal on localhost:7233..."
until temporal operator cluster health --address localhost:7233 >/dev/null 2>&1; do
    if ! kill -0 "$temporal_pid" 2>/dev/null; then
        echo "Temporal failed to start." >&2
        exit 1
    fi
    sleep 1
done

# ── Worker + web app ─────────────────────────────────────────────────────────
start_worker
start_ui

echo "Waiting for the web app on localhost:8000..."
until curl -fs http://localhost:8000/health >/dev/null 2>&1; do
    if ! kill -0 "$ui_pid" 2>/dev/null; then
        echo "Web app failed to start." >&2
        exit 1
    fi
    sleep 1
done

if [ "$MODE" = "booth" ]; then
    launch_kiosks
fi

echo "Game:        http://localhost:8000"
echo "Display:     http://localhost:8000/display  (calibrate, then Save & Launch)"
echo "Temporal UI: http://localhost:8233"
if [ "$MODE" = "booth" ]; then
    echo "Note: both kiosks open on the active display; move each onto its fan."
fi
echo "Press Ctrl-C to stop the stack."

# ── Supervisor: keep worker + web app alive; bail if Temporal dies ───────────
while true; do
    [ "$shutting_down" -eq 1 ] && exit 0

    if ! kill -0 "$temporal_pid" 2>/dev/null; then
        echo "Temporal dev server exited — shutting down the stack (restarting it would lose in-memory workflow state)." >&2
        exit 1
    fi

    if ! kill -0 "$worker_pid" 2>/dev/null; then
        if allow_restart worker; then
            echo "[$(date '+%H:%M:%S')] Worker exited — restarting (${worker_restart_count} in last ${RESTART_WINDOW}s)..." >&2
            sleep "$RESTART_BACKOFF" || true
            [ "$shutting_down" -eq 1 ] && exit 0
            start_worker
        else
            echo "Worker died more than ${MAX_RESTARTS} times in ${RESTART_WINDOW}s — giving up, shutting down the stack." >&2
            exit 1
        fi
    fi

    if ! kill -0 "$ui_pid" 2>/dev/null; then
        if allow_restart ui; then
            echo "[$(date '+%H:%M:%S')] Web app exited — restarting (${ui_restart_count} in last ${RESTART_WINDOW}s)..." >&2
            sleep "$RESTART_BACKOFF" || true
            [ "$shutting_down" -eq 1 ] && exit 0
            start_ui
        else
            echo "Web app died more than ${MAX_RESTARTS} times in ${RESTART_WINDOW}s — giving up, shutting down the stack." >&2
            exit 1
        fi
    fi

    sleep 1 || true
done
