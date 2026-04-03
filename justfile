# Justfile for demo-wordle

install:
    uv sync

check:
    uv run ruff format --check src/ tests/
    uv run ruff check src/ tests/
    uv run pyright src/ tests/
    uv run pytest -v

fmt:
    uv run ruff format src/ tests/
    uv run ruff check --fix src/ tests/

test:
    uv run pytest -v

worker:
    uv run python -m demo_wordle.worker

api:
    uv run uvicorn demo_wordle.api:app --reload

start-game:
    uv run python -m demo_wordle.start_game
