# Demo Wordle - Technical Specification

## Project Overview

Demo Wordle is a lightweight Wordle clone designed for live interactive presentations and workshops teaching Temporal workflow concepts. Attendees experience a familiar game (Wordle) while the presenter demonstrates parent-child workflows, Updates, Queries, and Activities in a running system. The entire application is intentionally minimal — no database, no AI, no user accounts — so that every line of code either plays the game or demonstrates a Temporal pattern.

This is a teaching tool, not a production app. It prioritizes clarity and "aha moments" over completeness.

## Technology Stack

- **Backend**: Temporal Python SDK, FastAPI
- **Frontend**: Single-page HTMX + Tailwind CSS, embedded JavaScript for animations
- **Word Source**: Hardcoded word list (no external API or AI dependency)
- **Package Management**: uv
- **Development**: Temporal CLI dev server (`temporal server start-dev`)

## Architecture Overview

### Workflow Architecture

1. **Daily Game Workflow (Parent)**: Picks a word, spawns child workflows for each player, tracks basic stats
2. **User Session Workflow (Child)**: Handles one player's game via Updates and Queries

There is no database, no entity workflow, no scheduled triggers. The presenter starts the daily workflow manually (or via a simple script), which is actually better for demos — you control when things happen.

### How a Game Plays Out

1. Presenter starts a Daily Game Workflow (picks a word, begins accepting players)
2. Audience member opens the web page, types their first guess
3. FastAPI sends an Update to the parent workflow: "create a session for this player"
4. Parent spawns a child workflow, returns the child workflow ID
5. API stores child workflow ID in a cookie
6. Each subsequent guess is an Update to the child workflow
7. Child returns feedback (green/yellow/gray) via the Update response
8. When the game ends (win or 6 guesses), child workflow completes and returns the result to the parent
9. Parent aggregates stats (viewable via Query)

### Temporal Patterns Demonstrated

| Pattern | Where | Why It Matters |
|---------|-------|---------------|
| Parent-child workflows | Daily spawns per-user sessions | Workflow composition and lifecycle |
| Updates (request-response) | Guess submission + session creation | Synchronous interaction with workflows |
| Queries (read-only) | Game state + daily statistics | Non-mutating state access |
| Activities | Word selection, guess validation | Side effects isolated from workflow logic |
| `wait_condition(all_handlers_finished)` | Parent shutdown | Graceful workflow completion |
| `parent_close_policy=ABANDON` | Child workflows | Children survive parent completion |
| Workflow return values | Child returns GameResult to parent | Data flow between workflows |

## Detailed Requirements

### Word Selection

**Activity: `pick_word`**
- Selects a random word from a hardcoded list of common 5-letter English words
- List lives in the activity module (~200-300 words is plenty)
- Must be in an Activity (not inline in workflow) because `random` is non-deterministic
- Word length is always 5 (not configurable — keeps things simple)
- Returns the word as a lowercase string

### Guess Validation

**Activity: `validate_guess`**
- Checks that a guess is exactly 5 letters, alphabetic, and in the valid word list
- The valid word list is larger than the answer list (standard Wordle pattern) — ~2000-3000 common words
- Returns a boolean (valid/invalid) plus an error message if invalid
- Word lists are module-level constants (no file I/O)

### Feedback Calculation

**Pure function: `calculate_feedback`**
- Standard Wordle algorithm with GREEN-first processing:
  1. First pass: mark exact position matches as GREEN, consume from letter pool
  2. Second pass: check remaining pool for YELLOW, else GRAY
- This is pure game logic, not an Activity — it runs inside the workflow
- Returns ordered list of `(letter, color)` feedback per position

### Daily Game Workflow (Parent)

**Workflow ID**: `demo-wordle-{date}` (e.g., `demo-wordle-2025-01-15`)

**Initialization**:
- Execute `pick_word` Activity to select the day's word
- Set `_initialized` flag for Update handler guard
- Initialize empty stats (wins by guess count, losses, active sessions)

**Update Handler: `create_session`**:
- Input: none (just a request for a new session)
- Spawns a `UserSessionWorkflow` as a child workflow
- Child workflow ID: `demo-wordle-{date}-{uuid}`
- `parent_close_policy=ABANDON` so children survive parent shutdown
- Returns child workflow ID to caller
- Tracks child workflow handle for result collection

**Query Handler: `get_statistics`**:
- Returns current day stats: total players, wins by guess count (1-6), losses, games in progress

**Result Collection**:
- As child workflows complete, parent collects `GameResult` return values
- Updates running statistics in workflow state
- Uses fire-and-forget async tasks to await child results

**Shutdown**:
- The workflow runs until explicitly told to end (via a signal or a simple boolean flag)
- `await workflow.wait_condition(workflow.all_handlers_finished)` before completing
- No automatic day boundary logic — presenter controls when the day ends
- On shutdown, count any still-active children as abandoned

### User Session Workflow (Child)

**Initialization**:
- Receives the target word as input from parent
- Creates `GameState` with empty guess history
- Sets `_initialized` flag

**Update Handler: `submit_guess`**:
- Guard: `await workflow.wait_condition(lambda: self._initialized)`
- Input: the guessed word (string)
- Executes `validate_guess` Activity
- If invalid: returns error response (game state unchanged)
- If valid: runs `calculate_feedback`, updates game state
- If game over (correct guess or 6th guess): marks game complete
- Returns: guess feedback + current game state + completion status

**Query Handler: `get_game_state`**:
- Returns current game state (all guesses with feedback, game status)
- Used by frontend on page reload to restore UI

**Completion**:
- When game ends, workflow returns `GameResult` (win/loss, number of guesses)
- This return value is collected by the parent workflow

### Game State Model

```
GameState:
  target_word: str
  guesses: list[GuessResult]
  status: "playing" | "won" | "lost"
  max_guesses: 6

GuessResult:
  word: str
  feedback: list[LetterFeedback]

LetterFeedback:
  letter: str
  status: "correct" | "present" | "absent"  # green, yellow, gray

GameResult:
  won: bool
  num_guesses: int
```

### Web API (FastAPI)

**Single router, minimal endpoints:**

`POST /guess`
- Body: `{"guess": "crane"}`
- Cookie: `session_workflow_id` (may be absent)
- If no cookie: send `create_session` Update to daily parent, get child ID, set cookie
- Send `submit_guess` Update to child workflow
- Return: HTMX partial (game board HTML fragment)

`GET /`
- Serves the single-page game UI
- If cookie exists: Query child workflow for current game state, render board with existing guesses
- If no cookie: render empty board

`GET /stats`
- Query daily parent workflow for statistics
- Return: HTMX partial (stats display)
- This is for the presenter to show on screen during the demo

**Configuration**:
- Daily workflow ID is set via environment variable or hardcoded default
- Temporal server address: `localhost:7233` (dev server default)
- No `.env` file needed — sensible defaults for everything

### User Interface

**Single HTML page with HTMX + Tailwind CSS + embedded JS:**

**Layout**:
- "Demo Wordle" title/branding
- 6x5 game board grid (standard Wordle layout)
- On-screen keyboard with color state tracking
- Simple stats display (visible to presenter, maybe toggled)
- Clean, minimal — looks like Wordle, not like a dev tool

**HTMX Interactions**:
- Guess submission: `hx-post="/guess"` on form submit
- Server returns updated board HTML (partial swap)
- Stats refresh: `hx-get="/stats"` with polling or manual trigger

**Embedded JavaScript (minimal, for UX only)**:
- Tile flip animation on guess feedback (CSS transitions + JS trigger)
- Tile pop animation on letter entry
- Shake animation on invalid guess
- Keyboard letter entry and backspace handling
- Color state tracking on virtual keyboard (green > yellow > gray priority)
- Win/loss celebration or message display

**Styling**:
- Tailwind CSS via CDN (no build step)
- Dark mode by default (matches classic Wordle aesthetic)
- Responsive but optimized for desktop (presentation context)
- Color scheme: standard Wordle green/yellow/gray (hardcoded, not configurable)

**No JavaScript framework**. No build step. One HTML file with embedded `<script>` tags is ideal. HTMX handles server communication, JS handles animations and keyboard input.

## Technical Implementation

### Project Structure

```
demo-wordle/
├── src/demo_wordle/
│   ├── __init__.py
│   ├── workflows.py        # DailyGameWorkflow + UserSessionWorkflow
│   ├── activities.py       # pick_word, validate_guess
│   ├── models.py           # GameState, GuessResult, LetterFeedback, GameResult
│   ├── game_logic.py       # calculate_feedback (pure function)
│   ├── api.py              # FastAPI app, routes, template rendering
│   ├── worker.py           # Temporal worker startup
│   └── word_lists.py       # Hardcoded answer + valid guess word lists
├── templates/
│   └── index.html          # Single-page HTMX/Tailwind UI
├── tests/
│   ├── test_game_logic.py  # calculate_feedback tests
│   ├── test_activities.py  # Activity tests with ActivityEnvironment
│   └── test_workflows.py   # Workflow tests with WorkflowEnvironment
├── pyproject.toml
├── justfile
└── README.md
```

### Development Workflow

```bash
# Setup
just install              # uv sync
temporal server start-dev # Start Temporal dev server (separate terminal)

# Run
just worker               # Start Temporal worker
just api                  # Start FastAPI server
just start-game           # Start a Daily Game Workflow (or script it)

# Development
just check                # format + lint + typecheck + test
just test                 # pytest with coverage
```

### Testing Strategy

**Game Logic** (pure unit tests):
- `calculate_feedback` edge cases: duplicate letters, all green, all gray, mixed
- No Temporal involvement

**Activities** (`ActivityEnvironment`):
- `pick_word` returns a valid 5-letter word
- `validate_guess` accepts valid words, rejects invalid ones
- Mock `random.choice` if determinism needed in tests

**Workflows** (`WorkflowEnvironment.start_local()`):
- Mock all activities with `@activity.defn(name="original_name")` functions
- Test parent creates child on `create_session` Update
- Test child processes guesses and returns correct feedback
- Test child completes and returns `GameResult`
- Test parent aggregates statistics correctly
- Test parent `get_statistics` Query returns expected data
- Use `pydantic_data_converter` for Pydantic model serialization in tests

### Presenter Workflow

During the demo/presentation:
1. Start Temporal dev server and worker (pre-demo setup)
2. Start FastAPI server
3. Run `just start-game` to create the daily workflow
4. Share the URL with the audience (or show on screen)
5. Play a game live, showing the Temporal Web UI alongside
6. Show workflow history, child workflows, Update/Query in the UI
7. Query stats to show aggregation
8. Optionally: kill the worker mid-game, restart it, show the game resumes (durability!)

## Success Criteria

- A player can open the page and play a complete game of Wordle
- The game board animates tile flips and keyboard color updates
- Game state survives page refresh (cookie + Query restores state)
- Game state survives worker restart (durability demo)
- Presenter can show Temporal Web UI with clear parent-child workflow hierarchy
- Stats Query shows live game statistics
- The entire codebase fits in a single presentation's worth of explanation (~500 lines of application code)
- Zero external service dependencies (no database, no AI API, no cloud services)
- Setup is: install deps, start temporal, start worker, start API, play
