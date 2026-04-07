# Session Summary: Workflow Architecture Refactoring
**Date**: 2026-04-06
**Duration**: ~3 hours
**Conversation Turns**: ~50
**Estimated Cost**: ~$18-25 (heavy MCP usage, large tool results from Temporal docs)
**Model**: Claude Opus 4.6 (1M context)

## Key Actions

### File & Model Reorganization
- Renamed `workflows.py` → `workflow.py` (singular) and `test_workflows.py` → `test_workflow.py`
- Moved `WorkflowInput`, `MakeGuessInput`, `ValidateGuessInput` dataclasses from workflow/activity files into `models.py`
- Updated all imports across the codebase (api, worker, tests)

### Word Selection → Workflow-Owned Activity
- Created `select_daily_word` activity wrapping `get_daily_word()`
- Added `SelectDailyWordInput` model
- Moved word selection from `api.py` into the workflow's `run()` method
- Removed `target_word` from `WorkflowInput`, replaced with `game_date`

### Random Mode Feature
- Added `random_mode: bool` to `WorkflowInput` — toggleable between daily word and random word
- Daily mode: uses `workflow.now().date()` + `select_daily_word` activity
- Random mode: uses `workflow.random().choice(ANSWER_LIST)` (Temporal's deterministic RNG)
- Removed `game_date` from `WorkflowInput` entirely — workflow derives date from `workflow.now()`
- Updated API with `random_mode` form field and `game_id` cookie for random games

### Update Handler Race Condition Fix
- Discovered update-before-init race: updates arriving before `select_daily_word` activity completes
- Applied `workflow.wait_condition(lambda: self._game_state is not None)` in update handler
- Researched `@workflow.init` pattern via MCP — can't use it here since we need async activity call

### Config Replacement
- Deleted custom `config.py` and `test_config.py` (6 tests removed)
- Replaced with Temporal's built-in `temporalio.envconfig.ClientConfig.load_client_connect_config()`
- Updated `docker-compose.yml` from `DURABLE_WORDLE_TEMPORAL_HOST` → `TEMPORAL_ADDRESS`
- Task queue read from `TEMPORAL_TASK_QUEUE` env var (app-specific, not part of envconfig)

### calculate_feedback → Activity
- Wrapped `calculate_feedback` as a Temporal activity for event history observability
- Added `CalculateFeedbackInput` model
- Each guess now produces two activity events: `validate_guess` + `calculate_feedback`
- Every step of every guess is inspectable in the Temporal UI

### Merge game_logic.py into Activity
- Inlined `calculate_feedback` logic from `game_logic.py` directly into the activity in `activities.py`
- Deleted `game_logic.py` — no more wrapper delegation
- Updated `test_game_logic.py` to test via `ActivityEnvironment` (9 tests preserved)
- Removed duplicate tests from `test_activities.py`

### README Updates
- Added "Playing via Temporal CLI" section with full walkthrough
- Updated architecture diagram, activity descriptions, config section
- Fixed stale file references (`workflows.py` → `workflow.py`)

## Prompt Inventory

| Prompt/Command | Action Taken | Outcome |
|---|---|---|
| Rename workflows.py, move dataclasses | File rename + import surgery | Clean, all 60 tests pass |
| Move ValidateGuessInput too | Same pattern for activities | 60 tests pass |
| No logic in API, word selection as activity | Plan + implement activity-based word selection | 64 tests pass, workflow owns word selection |
| Temporal-first design constraint | Saved memory, redesigned around CLI playability | Fundamental architecture improvement |
| Make word selection togglable (random mode) | Added `random_mode`, `workflow.random()`, `workflow.now()` | 65 tests, both modes work |
| Replace config with envconfig | Deleted config.py, used SDK built-in | 59 tests, standard env vars |
| Make calculate_feedback an activity | Wrapped as activity for observability | 63 tests, full event history visibility |
| Update README with CLI section | Full CLI walkthrough added | Complete CLI playability documented |
| Merge game_logic.py into activity | Inlined logic, deleted file, updated tests | 59 tests, one less file |

## Efficiency Insights

### What went well
- MCP server for Temporal docs was invaluable — found `@workflow.init`, `wait_condition` patterns, `workflow.random()` and `workflow.now()` APIs, update validator rules
- Parallel tool calls for reading multiple files and running grep searches
- `just check` as single gate (lint + typecheck + tests) caught issues fast

### What could have been more efficient
- Launched too many background pytest processes, creating zombie Temporal test servers that caused hangs — should have run tests in foreground exclusively
- The update-before-init race condition could have been anticipated from reading the Temporal docs upfront instead of discovering it through test failures
- Multiple rounds of `just format` needed — should format before lint every time

### Corrections
- `asyncio.sleep(0.5)` hack for test race → replaced with proper `wait_condition` in workflow after MCP research
- Initial test used hardcoded target words → switched to dynamic discovery via query after random mode made targets unpredictable

## Process Improvements
- Always run `just format` before `just check` to avoid lint round-trips
- Never run pytest in background — Temporal test servers are session-scoped fixtures that cause resource conflicts
- When adding async initialization to workflows (activities in `run()` before `wait_condition`), immediately add `wait_condition` guards in update handlers
- Use the Temporal docs MCP server proactively when designing patterns, not just reactively when debugging

## Observations
- The "playable via Temporal CLI" constraint was the single most impactful design decision — it forced all game logic into the workflow and made the architecture genuinely demonstrate Temporal concepts
- `temporalio.envconfig` is relatively new and not widely used in existing tutorials/samples yet — most examples still roll their own config
- The update validator pattern is well-documented but the update-before-init race is a common gotcha — the `@workflow.init` decorator and `wait_condition` pattern should be taught together
- Temporal's `workflow.random()` returns a standard `random.Random` instance, making it a drop-in replacement — very ergonomic
