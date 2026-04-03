# Demo Wordle - Implementation Plan

## Current Status

| Step | Description | Status |
|------|-------------|--------|
| 1 | Project Scaffolding | Not Started |
| 2 | Data Models | Not Started |
| 3 | Word Lists | Not Started |
| 4 | Game Logic (calculate_feedback) | Not Started |
| 5 | Activities (pick_word, validate_guess) | Not Started |
| 6 | User Session Workflow (Child) | Not Started |
| 7 | Daily Game Workflow (Parent) | Not Started |
| 8 | Temporal Worker | Not Started |
| 9 | FastAPI API | Not Started |
| 10 | Frontend UI (HTMX + Tailwind) | Not Started |
| 11 | Integration & Polish | Not Started |

---

## Step 1: Project Scaffolding

Set up the project structure, dependencies, and development tooling. This is the foundation everything else builds on.

```text
1. Create pyproject.toml:
   - Project name: demo-wordle
   - Python >= 3.12
   - Dependencies: temporalio, fastapi, uvicorn, jinja2, python-multipart
   - Dev dependencies: pytest, pytest-asyncio, ruff, pyright
   - Configure ruff (line-length 88, target py312)
   - Configure pyright (strict mode)
   - Configure pytest (asyncio_mode = "auto")
   - Package source in src/demo_wordle/

2. Create directory structure:
   - src/demo_wordle/__init__.py (empty)
   - src/demo_wordle/models.py (placeholder with ABOUTME comment)
   - src/demo_wordle/game_logic.py (placeholder with ABOUTME comment)
   - src/demo_wordle/word_lists.py (placeholder with ABOUTME comment)
   - src/demo_wordle/activities.py (placeholder with ABOUTME comment)
   - src/demo_wordle/workflows.py (placeholder with ABOUTME comment)
   - src/demo_wordle/api.py (placeholder with ABOUTME comment)
   - src/demo_wordle/worker.py (placeholder with ABOUTME comment)
   - templates/ (empty directory, add .gitkeep)
   - tests/__init__.py (empty)
   - tests/test_game_logic.py (placeholder)
   - tests/test_activities.py (placeholder)
   - tests/test_workflows.py (placeholder)

3. Create justfile with recipes:
   - install: uv sync
   - check: ruff format --check + ruff check + pyright + pytest
   - fmt: ruff format + ruff check --fix
   - test: pytest -v
   - worker: uv run python -m demo_wordle.worker
   - api: uv run uvicorn demo_wordle.api:app --reload
   - start-game: uv run python -m demo_wordle.start_game

4. Create .gitignore:
   - Standard Python ignores (__pycache__, *.pyc, .venv, dist, etc.)
   - commit-msg.md
   - .ai-sessions/

5. Run `uv sync` to install dependencies and verify the environment works.

6. Run `just check` — expect tests to pass (no tests yet) and no lint errors.
```

---

## Step 2: Data Models

Define the Pydantic/dataclass models that represent game state. These are used by every other module.

```text
**NOTE**: Project scaffolding from Step 1 is in place. Dependencies are installed.

1. RED: Write model validation tests first:
   - Create tests/test_models.py:
     - Test that LetterFeedback stores letter and status correctly
     - Test that GuessResult stores word and list of LetterFeedback
     - Test that GameState initializes with empty guesses and "playing" status
     - Test that GameState.is_game_over returns True when status is "won" or "lost"
     - Test that GameState.is_game_over returns False when status is "playing"
     - Test that GameResult stores won (bool) and num_guesses (int)

2. GREEN: Write minimal models to make tests pass:
   - Create src/demo_wordle/models.py:
     - Use dataclasses (decorated with @dataclass for Temporal serialization compatibility)
     - LetterFeedback: letter (str), status (Literal["correct", "present", "absent"])
     - GuessResult: word (str), feedback (list[LetterFeedback])
     - GameState: target_word (str), guesses (list[GuessResult]), status (Literal["playing", "won", "lost"]), max_guesses (int = 6), with is_game_over property
     - GameResult: won (bool), num_guesses (int)
     - DailyStats: total_players (int), wins_by_guess (dict[int, int]), losses (int), active_games (int)

3. REFACTOR: Review model field names and types for clarity.

4. Run `just check` — all tests pass, no lint errors.
```

---

## Step 3: Word Lists

Hardcoded word lists for answers and valid guesses. No file I/O.

```text
**NOTE**: Models from Step 2 exist in src/demo_wordle/models.py.

1. RED: Write word list tests:
   - Create tests/test_word_lists.py:
     - Test that ANSWER_WORDS is a non-empty list of strings
     - Test that all words in ANSWER_WORDS are exactly 5 lowercase alphabetic characters
     - Test that VALID_GUESSES is a non-empty list of strings
     - Test that all words in VALID_GUESSES are exactly 5 lowercase alphabetic characters
     - Test that every word in ANSWER_WORDS is also in VALID_GUESSES (answers are a subset)
     - Test that VALID_GUESSES has more words than ANSWER_WORDS

2. GREEN: Populate the word lists:
   - Update src/demo_wordle/word_lists.py:
     - ANSWER_WORDS: ~200-300 common 5-letter words (module-level tuple)
     - VALID_GUESSES: ~2000-3000 valid 5-letter words including all answer words (module-level tuple)
     - Use tuples (immutable) not lists

3. Run `just check` — all tests pass.
```

---

## Step 4: Game Logic (calculate_feedback)

The core Wordle algorithm. Pure function, no Temporal involvement. This is the most important piece to get right.

```text
**NOTE**: Models exist in src/demo_wordle/models.py. Word lists exist in src/demo_wordle/word_lists.py.

1. RED: Write calculate_feedback tests:
   - Create tests/test_game_logic.py:
     - Test all correct (guess == target): all letters are "correct"
     - Test all absent (no letters match): all letters are "absent"
     - Test exact position match: letter in right spot is "correct"
     - Test present but wrong position: letter in wrong spot is "present"
     - Test mixed feedback: combination of correct, present, absent
     - Test duplicate letter in guess, one in correct position: correct one is "correct", extra is "absent" (if target has only one)
     - Test duplicate letter in guess, target has two: both should get appropriate feedback
     - Test duplicate letter in guess, neither in correct position but target has one: first gets "present", second gets "absent"
     - Test all same letter guess against target with some of that letter
     - Test case insensitivity (if applicable — spec says lowercase)

   Concrete test cases:
     - target="apple", guess="apple" -> all correct
     - target="apple", guess="brick" -> all absent
     - target="apple", guess="aptly" -> [correct, present, absent, correct, absent]
     - target="abbey", guess="babes" -> [present, present, present, absent, absent]
     - target="steel", guess="geese" -> [absent, present, absent, absent, present]

2. GREEN: Implement calculate_feedback:
   - Update src/demo_wordle/game_logic.py:
     - Function signature: calculate_feedback(target: str, guess: str) -> list[LetterFeedback]
     - First pass: mark exact matches as "correct", remove from available pool
     - Second pass: for remaining letters, check if in pool — "present" if yes (consume from pool), "absent" if no
     - Returns ordered list of LetterFeedback for each position

3. REFACTOR: Ensure the two-pass algorithm is clear and well-structured.

4. Run `just check` — all tests pass.
```

---

## Step 5: Activities (pick_word, validate_guess)

Temporal activities for word selection and guess validation.

```text
**NOTE**: Word lists in src/demo_wordle/word_lists.py. Models in src/demo_wordle/models.py.

1. RED: Write activity tests using Temporal's ActivityEnvironment:
   - Create tests/test_activities.py:
     - Test pick_word returns a string that is in ANSWER_WORDS
     - Test pick_word returns a 5-letter lowercase string
     - Test validate_guess accepts a valid 5-letter word from VALID_GUESSES (returns valid=True, message=None)
     - Test validate_guess rejects a word not in VALID_GUESSES (returns valid=False, message describes why)
     - Test validate_guess rejects a word that is not 5 letters (returns valid=False)
     - Test validate_guess rejects a word with non-alphabetic characters (returns valid=False)
     - Test validate_guess rejects an empty string (returns valid=False)
     - Test validate_guess handles uppercase input by lowercasing it

   Use ActivityEnvironment to run each activity.

2. GREEN: Implement activities:
   - Update src/demo_wordle/activities.py:
     - @activity.defn pick_word() -> str: random.choice from ANSWER_WORDS
     - Define a dataclass ValidateGuessResult with fields: valid (bool), message (str | None)
     - @activity.defn validate_guess(guess: str) -> ValidateGuessResult:
       - Lowercase the input
       - Check length == 5, return error if not
       - Check all alphabetic, return error if not
       - Check in VALID_GUESSES set, return error if not
       - Return valid=True if all checks pass

3. REFACTOR: Ensure error messages are user-friendly.

4. Run `just check` — all tests pass.
```

---

## Step 6: User Session Workflow (Child)

The per-player workflow that handles guesses via Updates and exposes state via Queries.

```text
**NOTE**: Models in models.py, game logic in game_logic.py, activities in activities.py are all implemented and tested.

1. RED: Write UserSessionWorkflow tests using WorkflowEnvironment:
   - Create tests/test_workflows.py:
     - Mock activities: create mock pick_word and validate_guess with @activity.defn(name="pick_word") / @activity.defn(name="validate_guess") decorators
     - Test: submit a valid correct guess on first try — workflow returns GameResult(won=True, num_guesses=1)
     - Test: submit a valid guess that is wrong — game state shows guess with feedback, status still "playing"
     - Test: query get_game_state returns current state with all guesses and feedback
     - Test: submit 6 wrong guesses — workflow returns GameResult(won=False, num_guesses=6)
     - Test: submit an invalid guess (validate_guess returns invalid) — game state unchanged, error returned
     - Test: cannot submit guess after game is over — returns error
     - Test: get_game_state query after win shows status "won"

   Use WorkflowEnvironment.start_local() for all tests.
   Use temporalio.common.pydantic_data_converter if needed for serialization.

2. GREEN: Implement UserSessionWorkflow:
   - Update src/demo_wordle/workflows.py:
     - @workflow.defn class UserSessionWorkflow:
       - Input: target_word (str)
       - State: GameState, _initialized flag
       - @workflow.init: initialize GameState with target_word, set _initialized = True
       - @workflow.update submit_guess(guess: str) -> dict:
         - Guard: await workflow.wait_condition(lambda: self._initialized)
         - Execute validate_guess activity
         - If invalid: return error dict (no state change)
         - If valid: call calculate_feedback, append GuessResult to state
         - If correct or 6th guess: update status to "won"/"lost"
         - Return dict with feedback, game state summary, completion status
       - @workflow.query get_game_state -> GameState: return current state
       - Workflow run(): wait until game is over, return GameResult

3. REFACTOR: Clean up the update handler return shape for frontend consumption.

4. Run `just check` — all tests pass.
```

---

## Step 7: Daily Game Workflow (Parent)

The parent workflow that picks a word, spawns child sessions, and tracks stats.

```text
**NOTE**: UserSessionWorkflow is implemented and tested in src/demo_wordle/workflows.py.

1. RED: Write DailyGameWorkflow tests:
   - Add to tests/test_workflows.py:
     - Mock pick_word activity to return a known word
     - Test: create_session update returns a child workflow ID string
     - Test: create_session can be called multiple times (multiple players)
     - Test: get_statistics query returns DailyStats with correct total_players count
     - Test: after a child completes with a win, stats reflect the win
     - Test: after a child completes with a loss, stats reflect the loss
     - Test: end_game signal/update causes workflow to complete

2. GREEN: Implement DailyGameWorkflow:
   - Update src/demo_wordle/workflows.py:
     - @workflow.defn class DailyGameWorkflow:
       - Input: none (or optional date string)
       - State: target_word, DailyStats, list of child handles, _initialized flag, _shutdown flag
       - @workflow.init: execute pick_word activity, initialize stats, set _initialized
       - @workflow.update create_session() -> str:
         - Guard: await workflow.wait_condition(lambda: self._initialized)
         - Generate child workflow ID: f"demo-wordle-{date}-{uuid}"
         - Start UserSessionWorkflow as child with target_word
         - parent_close_policy = ABANDON
         - Increment total_players and active_games in stats
         - Start async task to await child result and update stats
         - Return child workflow ID
       - @workflow.query get_statistics() -> DailyStats: return stats
       - @workflow.signal end_game(): set _shutdown = True
       - Workflow run(): wait until _shutdown, then await workflow.wait_condition(workflow.all_handlers_finished), return stats

3. REFACTOR: Ensure result collection from children correctly updates wins_by_guess and losses.

4. Run `just check` — all tests pass.
```

---

## Step 8: Temporal Worker

Worker setup that registers workflows and activities.

```text
**NOTE**: Workflows and activities are fully implemented and tested.

1. Create src/demo_wordle/worker.py:
   - Connect to Temporal at localhost:7233 (configurable via env var TEMPORAL_ADDRESS)
   - Register DailyGameWorkflow, UserSessionWorkflow
   - Register pick_word, validate_guess activities
   - Task queue: "demo-wordle"
   - Use pydantic_data_converter if models use Pydantic
   - Run worker with asyncio

2. Create src/demo_wordle/start_game.py:
   - Connect to Temporal
   - Start DailyGameWorkflow with ID f"demo-wordle-{today's date}"
   - Print workflow ID to console

3. No new tests needed — this is pure wiring. Verify by running `just worker` and `just start-game` against a local Temporal dev server (manual verification step).

4. Run `just check` — all existing tests still pass, no lint errors.
```

---

## Step 9: FastAPI API

HTTP layer that bridges the browser to Temporal workflows.

```text
**NOTE**: All Temporal components (workflows, activities, worker) are implemented. Worker registers on task queue "demo-wordle".

1. RED: Write API tests:
   - Create tests/test_api.py:
     - Use FastAPI TestClient
     - Mock the Temporal client (don't need a running server for API tests)
     - Test GET / returns 200 with HTML containing game board
     - Test POST /guess without cookie calls create_session update, then submit_guess update, returns HTML
     - Test POST /guess with existing cookie skips create_session, calls submit_guess directly
     - Test POST /guess with invalid guess returns HTML with error/shake indicator
     - Test GET /stats returns HTML with statistics
     - Test GET / with cookie queries child workflow for existing game state

2. GREEN: Implement FastAPI app:
   - Update src/demo_wordle/api.py:
     - Create FastAPI app
     - Jinja2 template setup pointing to templates/
     - Temporal client as app lifespan dependency
     - Daily workflow ID from env var DAILY_WORKFLOW_ID with default f"demo-wordle-{today}"
     - GET /: render index.html template with game state (query child if cookie exists)
     - POST /guess: accept JSON body with "guess" field
       - Read session_workflow_id from cookie
       - If no cookie: send create_session update to daily parent, set cookie with child ID
       - Send submit_guess update to child workflow
       - Return rendered HTML partial (game board rows)
     - GET /stats: query daily parent for statistics, return HTML partial
     - Cookie: session_workflow_id, httponly, samesite=lax

3. REFACTOR: Ensure error handling covers workflow-not-found scenarios gracefully.

4. Run `just check` — all tests pass.
```

---

## Step 10: Frontend UI (HTMX + Tailwind)

Single HTML page with game board, keyboard, and animations.

```text
**NOTE**: API endpoints are implemented. GET / serves the template. POST /guess returns HTML partials. GET /stats returns stats HTML.

1. Create templates/index.html:
   - HTML5 document with Tailwind CSS via CDN and HTMX via CDN
   - Dark mode default (bg-gray-900, text-white)
   - Title: "Demo Wordle"

2. Build the game board:
   - 6x5 grid of tile cells
   - Each row is a div; each tile shows a letter with background color
   - Server renders completed rows with correct colors (correct=green-600, present=yellow-500, absent=gray-700)
   - Current input row is handled by JS (letters appear as typed)
   - Empty rows below are blank tiles

3. Build the on-screen keyboard:
   - 3-row QWERTY layout
   - Each key is a button
   - Keys track color state: green > yellow > gray priority (once green, stays green)
   - Enter key submits guess, Backspace key deletes last letter
   - Keyboard state rendered server-side on page load (from existing guesses), updated client-side after each guess

4. HTMX wiring:
   - Guess form: hidden form with hx-post="/guess", hx-target="#game-board", hx-swap="innerHTML"
   - JS captures keyboard input, builds guess string, submits form on Enter
   - Stats: hx-get="/stats" with hx-trigger="click" on a stats button (for presenter)
   - On page load with cookie: server renders existing guesses into the board

5. Embedded JavaScript (minimal):
   - Keyboard input handler: capture keydown and virtual keyboard clicks
   - Current guess tracking (array of up to 5 letters)
   - Letter entry: pop animation on tile (CSS class toggle)
   - Guess submission: trigger HTMX form submit
   - After HTMX swap: trigger tile flip animation (CSS transitions with staggered delays)
   - Shake animation on invalid guess (HTMX response includes error indicator)
   - Win celebration (simple message or animation)
   - Update keyboard key colors from server response data attributes

6. CSS animations (Tailwind + custom):
   - Tile flip: rotateX transform with 0.3s transition, staggered per tile
   - Tile pop: scale transform on letter entry
   - Row shake: translateX keyframe animation
   - Keep it simple — CSS transitions, not a full animation library

7. Verify by running the full stack manually:
   - `temporal server start-dev`
   - `just worker`
   - `just api`
   - `just start-game`
   - Open browser, play a game

8. Run `just check` — all tests still pass.
```

---

## Step 11: Integration & Polish

Final wiring, edge cases, and presentation readiness.

```text
**NOTE**: All components are implemented. The game is playable end-to-end.

1. RED: Write integration edge-case tests:
   - Test page refresh restores game state (cookie + query)
   - Test submitting guess after game is won returns appropriate message
   - Test submitting guess after game is lost returns appropriate message
   - Test stats endpoint reflects correct counts after multiple games

2. GREEN: Fix any edge cases found:
   - Ensure cookie is cleared or game-over state is handled when game ends
   - Ensure stats page renders cleanly with zero players
   - Ensure keyboard disables after game over

3. Polish for presentation:
   - Verify Temporal Web UI shows clear parent-child hierarchy
   - Verify workflow IDs are readable in the UI
   - Test the durability demo: kill worker mid-game, restart, verify game resumes
   - Ensure the game looks good on a projector (large fonts, high contrast)

4. Run `just check` — all tests pass, clean lint, clean types.

5. Update README.md with:
   - Brief description
   - Setup instructions (5 commands)
   - Presenter workflow notes
```

---

## Implementation Guidelines

- **TDD throughout**: Every step writes tests before implementation code.
- **No premature abstraction**: Keep things simple. One file per concern, no factories or registries.
- **Temporal patterns**: Use `@workflow.defn`, `@workflow.update`, `@workflow.query`, `@workflow.signal`, `@activity.defn` decorators. Use `workflow.execute_activity` to call activities.
- **Serialization**: Use dataclasses for all Temporal-facing data types. If Pydantic is needed, use `pydantic_data_converter`.
- **Task queue**: Always "demo-wordle".
- **Error handling**: Validate at boundaries (API input, activity input). Let Temporal handle retries and failures.
- **Testing**: Use `ActivityEnvironment` for activity tests, `WorkflowEnvironment.start_local()` for workflow tests, `TestClient` for API tests.

## Success Metrics

- [ ] Player can open page, play a full game of Wordle
- [ ] Tile flip animations and keyboard color tracking work
- [ ] Game state survives page refresh
- [ ] Game state survives worker restart
- [ ] Temporal Web UI shows parent-child workflow hierarchy
- [ ] Stats query shows live statistics
- [ ] `just check` passes (format, lint, types, tests)
- [ ] Codebase is ~500 lines of application code
- [ ] Zero external dependencies (no DB, no AI, no cloud)
- [ ] Setup is 5 commands: install, start temporal, start worker, start api, start game
