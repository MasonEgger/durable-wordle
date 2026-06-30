# Durable Wordle task runner

worker:
    uv run python -m durable_wordle.worker

# Export a day's leaderboard (incl. emails) to data/archive/*.csv. Optional date arg.
archive *date:
    uv run python scripts/archive_leaderboard.py {{ date }}

server:
    temporal server start-dev

# The worker and web app auto-restart if they die (booth-friendly); Temporal dying
# still shuts the stack down (restarting it would lose in-memory workflow state).
# Start Temporal server + worker + FastAPI UI together; Ctrl-C stops all three.
dev:
    #!/usr/bin/env bash
    set -euo pipefail

    # Auto-restart caps: give up if a process dies more than MAX_RESTARTS times
    # within RESTART_WINDOW seconds, and back off briefly between restarts.
    MAX_RESTARTS=5
    RESTART_WINDOW=60
    RESTART_BACKOFF=2

    temporal_pid=""
    worker_pid=""
    ui_pid=""
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
    }
    trap cleanup EXIT INT TERM

    start_worker() {
        uv run python -m durable_wordle.worker &
        worker_pid=$!
    }

    start_ui() {
        uv run uvicorn --factory durable_wordle.api:create_production_app --reload &
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
        if [ "$(( now - window_start ))" -gt "$RESTART_WINDOW" ]; then
            eval "$window_var=$now"
            eval "$count_var=1"
            return 0
        fi
        count=$(( count + 1 ))
        eval "$count_var=$count"
        if [ "$count" -gt "$MAX_RESTARTS" ]; then
            return 1
        fi
        return 0
    }

    for port in 7233 8233 8000; do
        if lsof -ti :"$port" >/dev/null 2>&1; then
            echo "Port $port is already in use — stop the other process (e.g. a stray 'just dev'/'just booth') and retry." >&2
            exit 1
        fi
    done

    temporal server start-dev --ui-port 8233 &
    temporal_pid=$!

    echo "Waiting for Temporal on localhost:7233..."
    until temporal operator cluster health --address localhost:7233 >/dev/null 2>&1; do
        if ! kill -0 "$temporal_pid" 2>/dev/null; then
            echo "Temporal failed to start." >&2; exit 1
        fi
        sleep 1
    done

    start_worker
    start_ui

    echo "Durable Wordle: http://localhost:8000"
    echo "Temporal UI:    http://localhost:8233"
    echo "Press Ctrl-C to stop all processes."

    # Supervisor: keep the worker + web app alive; bail if Temporal dies.
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

# Full booth mode: start the stack, then open the game + display in Chrome kiosks.
booth:
    #!/usr/bin/env bash
    set -euo pipefail

    temporal_pid=""
    worker_pid=""
    ui_pid=""
    game_profile=""
    display_profile=""

    cleanup() {
        for pid in "$ui_pid" "$worker_pid" "$temporal_pid"; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
            fi
        done
        # Close our kiosk Chrome windows (matched by their throwaway profile dir,
        # so we never touch the user's normal Chrome) and remove the temp dirs.
        for prof in "$game_profile" "$display_profile"; do
            if [ -n "$prof" ]; then
                pkill -f "$prof" 2>/dev/null || true
                rm -rf "$prof" 2>/dev/null || true
            fi
        done
    }
    trap cleanup EXIT INT TERM

    for port in 7233 8233 8000; do
        if lsof -ti :"$port" >/dev/null 2>&1; then
            echo "Port $port is already in use — stop the other process (e.g. a stray 'just dev'/'just booth') and retry." >&2
            exit 1
        fi
    done

    temporal server start-dev --ui-port 8233 &
    temporal_pid=$!

    echo "Waiting for Temporal on localhost:7233..."
    until temporal operator cluster health --address localhost:7233 >/dev/null 2>&1; do
        if ! kill -0 "$temporal_pid" 2>/dev/null; then echo "Temporal failed to start." >&2; exit 1; fi
        sleep 1
    done

    uv run python -m durable_wordle.worker &
    worker_pid=$!

    uv run uvicorn --factory durable_wordle.api:create_production_app --reload &
    ui_pid=$!

    echo "Waiting for the web app on localhost:8000..."
    until curl -fs http://localhost:8000/health >/dev/null 2>&1; do
        if ! kill -0 "$ui_pid" 2>/dev/null; then echo "Web app failed to start." >&2; exit 1; fi
        sleep 1
    done

    # Locate Chrome (common PATH names, or the macOS app bundle).
    chrome_bin=""
    for candidate in google-chrome google-chrome-stable chromium chrome; do
        if command -v "$candidate" >/dev/null 2>&1; then chrome_bin="$candidate"; break; fi
    done
    if [ -z "$chrome_bin" ] && [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
        chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    fi

    if [ -n "$chrome_bin" ]; then
        echo "Launching Chrome kiosks (game + display)..."
        # Separate throwaway profiles → two independent, clean kiosk windows
        # (a shared profile would just open a tab in the first instance).
        game_profile="$(mktemp -d)"
        display_profile="$(mktemp -d)"
        # --kiosk --app: chromeless fullscreen. The rest suppress the
        # first-run/restore-pages/crash bubbles after a hard kill.
        chrome_flags="--kiosk --no-first-run --no-default-browser-check --noerrdialogs --disable-session-crashed-bubble --disable-infobars --disable-features=Translate"
        "$chrome_bin" $chrome_flags --user-data-dir="$game_profile" --app="http://localhost:8000" &
        sleep 2
        "$chrome_bin" $chrome_flags --user-data-dir="$display_profile" --app="http://localhost:8000/display" &
    else
        echo "Chrome not found — open these manually:"
    fi

    echo "Game:        http://localhost:8000"
    echo "Display:     http://localhost:8000/display  (calibrate, then Save & Launch)"
    echo "Temporal UI: http://localhost:8233"
    echo "Note: both kiosks open on the active display; move each onto its fan."
    echo "Press Ctrl-C to stop the stack (closes the kiosks too)."

    # Stay up while all three live; exit cleanly (trap cleans up) if any dies.
    while kill -0 "$temporal_pid" 2>/dev/null \
       && kill -0 "$worker_pid" 2>/dev/null \
       && kill -0 "$ui_pid" 2>/dev/null; do
        sleep 1
    done
    echo "A process exited — shutting down the stack." >&2

ui:
    uv run uvicorn --factory durable_wordle.api:create_production_app --reload

test:
    uv run pytest

lint:
    uv run ruff check src/ tests/

format:
    uv run ruff format src/ tests/

typecheck:
    uv run mypy src/

check: lint typecheck test
