# Durable Wordle — Todo Checklist

## Step 1: Project Scaffolding & Configuration
- [x] 1. Create pyproject.toml with dependencies and tooling config
- [x] 2. Create justfile with dev recipes (worker, server, test, lint, format, typecheck, check)
- [x] 3. Create package structure (src/durable_wordle/__init__.py, tests/__init__.py)
- [x] 4. RED: Write configuration tests (tests/test_config.py)
- [x] 5. GREEN: Create src/durable_wordle/config.py with Settings and env var loading
- [x] 6. Run `uv sync` and `just check`

## Step 2: Models & Game Logic
- [x] 1. RED: Write model construction tests (tests/test_models.py)
- [x] 2. GREEN: Create src/durable_wordle/models.py (LetterFeedback, GuessResult, GameState)
- [x] 3. RED: Write calculate_feedback tests (tests/test_game_logic.py)
- [x] 4. GREEN: Create src/durable_wordle/game_logic.py (calculate_feedback)
- [x] 5. REFACTOR: Review calculate_feedback for clarity
- [x] 6. Run `just check`

## Step 3: Word Lists & Validation Activity
- [x] 1. RED: Write word list tests (tests/test_word_lists.py)
- [x] 2. GREEN: Create src/durable_wordle/word_lists.py (ANSWER_LIST, VALID_GUESSES, get_daily_word, is_valid_guess)
- [x] 3. RED: Write validate_guess activity tests (tests/test_activities.py)
- [x] 4. GREEN: Create src/durable_wordle/activities.py (validate_guess activity)
- [x] 5. REFACTOR: Ensure word lists use frozensets for O(1) lookup
- [x] 6. Run `just check`

## Step 4: UserSessionWorkflow
- [ ] 1. RED: Write workflow tests (tests/test_workflows.py)
- [ ] 2. GREEN: Create src/durable_wordle/workflows.py (UserSessionWorkflow)
- [ ] 3. REFACTOR: Clean workflow logic
- [ ] 4. Run `just check`

## Step 5: FastAPI API & Session Management
- [ ] 1. Create minimal templates/index.html placeholder
- [ ] 2. RED: Write API tests (tests/test_api.py, tests/conftest.py)
- [ ] 3. GREEN: Create src/durable_wordle/api.py (routes, session management, Temporal client)
- [ ] 4. REFACTOR: Extract Temporal client lifecycle
- [ ] 5. Run `just check`

## Step 6: Frontend Template (HTMX/Tailwind)
- [ ] 1. RED: Write template rendering tests (add to tests/test_api.py)
- [ ] 2. GREEN: Create full templates/index.html (HTMX/Tailwind game UI)
- [ ] 3. Add HTMX partial response support to api.py
- [ ] 4. REFACTOR: Clean up template, accessibility
- [ ] 5. Run `just check`

## Step 7: Worker & Docker Compose
- [ ] 1. Create src/durable_wordle/worker.py
- [ ] 2. Create Dockerfile
- [ ] 3. Create docker-compose.yml
- [ ] 4. Test locally with docker-compose up
- [ ] 5. Run `just check` final verification
