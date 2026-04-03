# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Demo Wordle is a Temporal-powered Wordle clone for live presentations teaching workflow concepts. It is a teaching tool, not a production app — prioritize clarity over robustness.

## Tech Stack

- **Backend**: Temporal Python SDK, FastAPI, Jinja2
- **Frontend**: HTMX + Tailwind CSS (CDN, no build step), embedded JS for animations
- **Package manager**: uv
- **Task runner**: just

## Commands

```bash
just install        # uv sync
just check          # ruff format --check + ruff check + pyright + pytest
just fmt            # ruff format + ruff check --fix
just test           # pytest -v
just worker         # Start Temporal worker
just api            # Start FastAPI with uvicorn --reload
just start-game     # Start a Daily Game Workflow
```

Run a single test: `uv run pytest tests/test_game_logic.py::test_name -v`

Requires `temporal server start-dev` running separately for worker/api/start-game.

## Architecture

Two Temporal workflows in a parent-child relationship:

- **DailyGameWorkflow (parent)**: Picks a word via `pick_word` activity, spawns child workflows per player via `create_session` Update, tracks stats via `get_statistics` Query.
- **UserSessionWorkflow (child)**: Handles one player's game. Guesses submitted via `submit_guess` Update, state read via `get_game_state` Query. Returns `GameResult` to parent on completion.

FastAPI bridges the browser to Temporal — `POST /guess` sends Updates, `GET /` queries state, cookies track the player's child workflow ID.

### Key Design Decisions

- `calculate_feedback` is a **pure function** (not an activity) — it's deterministic, runs inside the workflow.
- `pick_word` must be an activity because `random.choice` is non-deterministic.
- Use **dataclasses** for all Temporal-facing data types (not Pydantic) for simpler serialization.
- Task queue: `"demo-wordle"`. Workflow ID pattern: `demo-wordle-{date}`, child: `demo-wordle-{date}-{uuid}`.
- `parent_close_policy=ABANDON` on child workflows so they survive parent completion.

## Testing

- **Game logic**: Pure unit tests, no Temporal.
- **Activities**: Use `ActivityEnvironment`.
- **Workflows**: Use `WorkflowEnvironment.start_local()` with mock activities (`@activity.defn(name="original_name")`).
- **API**: Use FastAPI `TestClient` with mocked Temporal client.

## File Layout

Source in `src/demo_wordle/`, templates in `templates/`, tests in `tests/`. See `spec.md` for the full project structure and `plan.md` for the implementation plan with TDD steps.
