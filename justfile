# Durable Wordle task runner

worker:
    uv run python -m durable_wordle.worker

server:
    temporal server start-dev

# Start Temporal server + worker + FastAPI UI together; Ctrl-C stops all three.
dev:
    #!/usr/bin/env bash
    set -euo pipefail

    temporal_pid=""
    worker_pid=""
    ui_pid=""

    cleanup() {
        for pid in "$ui_pid" "$worker_pid" "$temporal_pid"; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
            fi
        done
    }
    trap cleanup EXIT INT TERM

    temporal server start-dev --ui-port 8233 &
    temporal_pid=$!

    echo "Waiting for Temporal on localhost:7233..."
    until temporal operator cluster health --address localhost:7233 >/dev/null 2>&1; do
        if ! kill -0 "$temporal_pid" 2>/dev/null; then
            wait "$temporal_pid"
        fi
        sleep 1
    done

    uv run python -m durable_wordle.worker &
    worker_pid=$!

    uv run uvicorn --factory durable_wordle.api:create_production_app --reload &
    ui_pid=$!

    echo "Durable Wordle: http://localhost:8000"
    echo "Temporal UI:    http://localhost:8233"
    echo "Press Ctrl-C to stop all processes."

    while true; do
        for pid in "$temporal_pid" "$worker_pid" "$ui_pid"; do
            if ! kill -0 "$pid" 2>/dev/null; then
                wait "$pid"
            fi
        done
        sleep 1
    done

# Full booth mode: start the stack, then open the game + display in Firefox kiosks.
booth:
    #!/usr/bin/env bash
    set -euo pipefail

    temporal_pid=""
    worker_pid=""
    ui_pid=""

    cleanup() {
        for pid in "$ui_pid" "$worker_pid" "$temporal_pid"; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
            fi
        done
    }
    trap cleanup EXIT INT TERM

    temporal server start-dev --ui-port 8233 &
    temporal_pid=$!

    echo "Waiting for Temporal on localhost:7233..."
    until temporal operator cluster health --address localhost:7233 >/dev/null 2>&1; do
        if ! kill -0 "$temporal_pid" 2>/dev/null; then wait "$temporal_pid"; fi
        sleep 1
    done

    uv run python -m durable_wordle.worker &
    worker_pid=$!

    uv run uvicorn --factory durable_wordle.api:create_production_app --reload &
    ui_pid=$!

    echo "Waiting for the web app on localhost:8000..."
    until curl -fs http://localhost:8000/health >/dev/null 2>&1; do
        if ! kill -0 "$ui_pid" 2>/dev/null; then wait "$ui_pid"; fi
        sleep 1
    done

    # Locate Firefox (PATH, or the standard macOS app bundle).
    firefox_bin="firefox"
    if ! command -v firefox >/dev/null 2>&1; then
        firefox_bin="/Applications/Firefox.app/Contents/MacOS/firefox"
    fi

    if [ -x "$firefox_bin" ] || command -v "$firefox_bin" >/dev/null 2>&1; then
        echo "Launching Firefox kiosks (game + display)..."
        "$firefox_bin" --kiosk --new-window "http://localhost:8000" &
        sleep 2
        "$firefox_bin" --kiosk --new-window "http://localhost:8000/display" &
    else
        echo "Firefox not found — open these manually:"
    fi

    echo "Game:        http://localhost:8000"
    echo "Display:     http://localhost:8000/display  (calibrate, then Save & Launch)"
    echo "Temporal UI: http://localhost:8233"
    echo "Note: drag each kiosk window onto its fan if they open on the same screen."
    echo "Press Ctrl-C to stop the stack."

    while true; do
        for pid in "$temporal_pid" "$worker_pid" "$ui_pid"; do
            if ! kill -0 "$pid" 2>/dev/null; then wait "$pid"; fi
        done
        sleep 1
    done

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
