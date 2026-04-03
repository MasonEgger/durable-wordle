# Demo Wordle - Implementation Checklist

## Step 1: Project Scaffolding
- [x] 1.1 Create pyproject.toml with dependencies and tool config
- [x] 1.2 Create directory structure with placeholder files
- [x] 1.3 Create justfile with recipes
- [x] 1.4 Create .gitignore
- [x] 1.5 Run `uv sync` to install dependencies
- [x] 1.6 Run `just check` to verify clean state

## Step 2: Data Models
- [ ] 2.1 RED: Write model tests in tests/test_models.py
- [ ] 2.2 GREEN: Implement models in src/demo_wordle/models.py
- [ ] 2.3 REFACTOR: Review model clarity
- [ ] 2.4 Run `just check`

## Step 3: Word Lists
- [ ] 3.1 RED: Write word list tests in tests/test_word_lists.py
- [ ] 3.2 GREEN: Populate word lists in src/demo_wordle/word_lists.py
- [ ] 3.3 Run `just check`

## Step 4: Game Logic (calculate_feedback)
- [ ] 4.1 RED: Write calculate_feedback tests in tests/test_game_logic.py
- [ ] 4.2 GREEN: Implement calculate_feedback in src/demo_wordle/game_logic.py
- [ ] 4.3 REFACTOR: Ensure two-pass algorithm clarity
- [ ] 4.4 Run `just check`

## Step 5: Activities (pick_word, validate_guess)
- [ ] 5.1 RED: Write activity tests in tests/test_activities.py
- [ ] 5.2 GREEN: Implement activities in src/demo_wordle/activities.py
- [ ] 5.3 REFACTOR: Review error messages
- [ ] 5.4 Run `just check`

## Step 6: User Session Workflow (Child)
- [ ] 6.1 RED: Write UserSessionWorkflow tests in tests/test_workflows.py
- [ ] 6.2 GREEN: Implement UserSessionWorkflow in src/demo_wordle/workflows.py
- [ ] 6.3 REFACTOR: Clean up update handler return shape
- [ ] 6.4 Run `just check`

## Step 7: Daily Game Workflow (Parent)
- [ ] 7.1 RED: Write DailyGameWorkflow tests in tests/test_workflows.py
- [ ] 7.2 GREEN: Implement DailyGameWorkflow in src/demo_wordle/workflows.py
- [ ] 7.3 REFACTOR: Verify result collection logic
- [ ] 7.4 Run `just check`

## Step 8: Temporal Worker
- [ ] 8.1 Create src/demo_wordle/worker.py
- [ ] 8.2 Create src/demo_wordle/start_game.py
- [ ] 8.3 Run `just check`

## Step 9: FastAPI API
- [ ] 9.1 RED: Write API tests in tests/test_api.py
- [ ] 9.2 GREEN: Implement FastAPI app in src/demo_wordle/api.py
- [ ] 9.3 REFACTOR: Error handling for workflow-not-found
- [ ] 9.4 Run `just check`

## Step 10: Frontend UI
- [ ] 10.1 Create templates/index.html with Tailwind + HTMX
- [ ] 10.2 Build game board (6x5 grid)
- [ ] 10.3 Build on-screen keyboard
- [ ] 10.4 Wire HTMX interactions
- [ ] 10.5 Implement embedded JavaScript (keyboard, animations)
- [ ] 10.6 Add CSS animations (flip, pop, shake)
- [ ] 10.7 Manual full-stack verification
- [ ] 10.8 Run `just check`

## Step 11: Integration & Polish
- [ ] 11.1 RED: Write integration edge-case tests
- [ ] 11.2 GREEN: Fix edge cases
- [ ] 11.3 Polish for presentation (Web UI, durability demo)
- [ ] 11.4 Run `just check`
- [ ] 11.5 Update README.md
