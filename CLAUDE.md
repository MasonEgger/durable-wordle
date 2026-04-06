# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Durable Wordle — a Wordle clone where each game session is a Temporal workflow. No database; the workflow *is* the state. Built as a conference demo teaching five Temporal concepts: start_workflow, Updates, Activities, durability, and workflow completion.

## Stack

- **Backend**: Temporal Python SDK (`temporalio`), FastAPI, Jinja2
- **Frontend**: HTMX, Tailwind CSS (CDN)
- **Package management**: uv
- **Task runner**: just
- **Deployment**: Docker Compose with Temporal dev server

## Commands

```bash
just check      # lint + typecheck + test (the gate)
just test       # uv run pytest
just lint       # uv run ruff check src/ tests/
just typecheck  # uv run mypy src/
just format     # uv run ruff format src/ tests/
just worker     # start Temporal worker
just server     # start FastAPI dev server (uvicorn --reload)
```

Run a single test: `uv run pytest tests/test_game_logic.py::test_name -v`

**Tip**: Run `just format` before `just check` after writing new files — auto-fixes line-length issues and avoids a manual edit round-trip.

## Architecture

```mermaid
flowchart LR
    Browser -->|cookie| FastAPI
    FastAPI -->|Update / Query| UserSessionWorkflow
    UserSessionWorkflow --> validate_guess["validate_guess (Activity)"]
    UserSessionWorkflow --> calculate_feedback["calculate_feedback (pure fn)"]
```

- **One workflow per game session**: cookie holds session_id (UUID), workflow ID = `wordle-{date}-{session_id}`
- **Update handler** (`make_guess`): validates guess, runs activity, computes feedback, mutates state, returns result
- **Query handler** (`get_game_state`): returns current board state for rendering (read-only)
- **Activity** (`validate_guess`): checks word against bundled list (sync activity, file I/O = side effect)
- **Pure function** (`calculate_feedback`): green/yellow/gray logic with duplicate-letter handling
- **Word selection**: `random.seed(date.toordinal())` — deterministic daily word, zero external deps

## Key Modules

- **`config.py`**: Frozen `Settings` dataclass + `load_settings()` factory reading `DURABLE_WORDLE_*` env vars
- **`models.py`**: `LetterFeedback` enum (CORRECT/PRESENT/ABSENT), `GuessResult`, `GameState` with `is_game_over` property
- **`game_logic.py`**: `calculate_feedback(guess, target)` — two-pass algorithm (exact matches first, then remaining letters) handling duplicate letters correctly
- **`workflows.py`**: `UserSessionWorkflow` — Update handler (`make_guess`), validator, Query handler (`get_game_state`), `wait_condition` for game over
- **`activities.py`**: `validate_guess` sync activity — checks word against bundled list via `is_valid_guess`
- **`api.py`**: `create_app()` factory — FastAPI app with cookie-based sessions, Temporal client lifecycle via lifespan (or direct injection for tests), routes: `GET /`, `POST /guess`, `GET /health`. `create_production_app()` wraps the factory with `load_settings()` for uvicorn `--factory` mode
- **`worker.py`**: Temporal worker entry point — connects via `load_settings()`, registers `UserSessionWorkflow` and `validate_guess`, uses `ThreadPoolExecutor` for sync activities

## Temporal Constraints

- Workflow code must be deterministic — no I/O, no `datetime.now()` (use `workflow.now()`), no `random` (use `workflow.random()`)
- Import activities in workflows with `workflow.unsafe.imports_passed_through()`
- Workflow and activity inputs use single dataclass pattern
- Enums in workflow/activity data types must use `StrEnum` or `IntEnum` — the default data converter silently fails with `(str, Enum)`
- Update validators must not mutate state or block
- Sync activities require `ThreadPoolExecutor` on the worker

## Code Conventions

- `src/durable_wordle/` layout — workflows.py and activities.py in separate files (SDK sandbox requirement)
- All files start with 2-line ABOUTME comment (first line prefixed `ABOUTME: `)
- Strict mypy — no `Any` types
- Type hints on all functions, parameters, and return types
- `X | None` over `Optional[X]` (PEP 604, Python 3.12+)
- RST-format docstrings on all public interfaces
- Absolute imports only — no relative imports
- Empty `__init__.py` files — never add content to them
- Descriptive variable names — no single-letter names (`i`, `j`, `x`); use `line_index`, `letter_index`, etc.
- Use method references for queries/updates, not string names
- Config via `DURABLE_WORDLE_TEMPORAL_HOST`, `DURABLE_WORDLE_TEMPORAL_NAMESPACE`, `DURABLE_WORDLE_TEMPORAL_TASK_QUEUE` env vars

## Testing

- **Workflow tests**: `WorkflowEnvironment.start_local()` with real activities, unique `uuid4()` task queues per test. Prefer real activities when they're lightweight (in-memory lookups); reserve mocks for activities with external dependencies (APIs, databases)
- **Activity tests**: `ActivityEnvironment` for isolated activity testing. `validate_guess` is a sync activity, so `ActivityEnvironment.run()` returns directly — do not `await` it
- **API tests**: `httpx.AsyncClient` with `ASGITransport` + inline Workers per test (not fixture-based — ASGITransport doesn't trigger lifespan, and fixture workers cause event loop issues). Set `app.state` directly for test injection via `create_app(temporal_client=...)`
- **Pure logic**: direct unit tests for `calculate_feedback` and word lists
- pytest-asyncio with `asyncio_mode = "auto"`
