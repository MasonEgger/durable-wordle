# Durable Wordle task runner

worker:
    uv run python -m durable_wordle.worker

# Export a day's leaderboard (incl. emails) to data/archive/*.csv. Optional date arg.
archive *date:
    uv run python scripts/archive_leaderboard.py {{ date }}

server:
    temporal server start-dev

# Start Temporal server + worker + FastAPI UI together; Ctrl-C stops all three.
# Worker/web app auto-restart if they die; see scripts/run_stack.sh.
dev:
    bash scripts/run_stack.sh dev

# Full booth mode: the dev stack, plus the game + display in Chrome kiosks.
booth:
    bash scripts/run_stack.sh booth

ui:
    uv run uvicorn --factory durable_wordle.api:create_production_app --reload

test:
    uv run pytest

# Full-stack browser tests: boot the stack + headless Chromium. Needs the
# temporal binary and `uv run playwright install chromium` once.
test-e2e:
    uv run pytest -m e2e tests/e2e

lint:
    uv run ruff check src/ tests/

format:
    uv run ruff format src/ tests/

typecheck:
    uv run mypy src/

check: lint typecheck test
