# Durable Wordle task runner

worker:
    uv run python -m durable_wordle.worker

server:
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
