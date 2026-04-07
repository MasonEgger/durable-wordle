# Lessons Learned

## Recent
<!-- 10 most recent lessons, newest first -->

- When using `random.seed()` in code that runs in a `ThreadPoolExecutor`, use `random.Random(seed)` for an isolated instance instead — global state mutation causes race conditions between concurrent threads (2026-04-07)
- After a successful Temporal `execute_update`, query the handle directly instead of going through a describe+query helper — the workflow is known to be running, so the extra describe RPC is redundant (2026-04-07)
- When a two-pass algorithm guarantees all positions are filled, initialize with a real default value (e.g., `ABSENT`) instead of `None` — avoids misleading `| None` types and dead filter code (2026-04-07)
- When `/simplify` identifies multiple changes, explain all findings and get approval before editing — don't jump straight to edits (2026-04-07)
- When cookies are set with `httponly=True`, JS cannot read or clear them — use a server route for cookie management (e.g., `/new-game` that sets/deletes cookies and redirects) (2026-04-06)
- For `temporalio.envconfig`, use `ClientConfigProfile.load(config_source=Path(...))` with a project-local TOML file — `str` arg is treated as TOML content, `Path` as a file path (2026-04-06)
- When mocking external APIs in tests with a session-scoped Temporal test server, use `autouse=True` fixture in `conftest.py` to avoid rate limiting across all test files (2026-04-06)
- For HTMX error handling, return 422 + `HX-Trigger` header instead of swapping the board — client JS catches the event for toasts/shake without losing current state (2026-04-06)
- `WorkflowUpdateFailedError` wraps the real error in `__cause__` — use `str(err.__cause__)` not `str(err)` to get the actual message (2026-04-06)
- When promoting a pure function to a Temporal activity, inline the logic rather than wrapping — avoids an extra file and indirection layer (2026-04-06)

## Categories
<!-- Lessons organized by topic -->

### Temporal
- After a successful `execute_update`, query the handle directly — the workflow is known running, so a describe+query roundtrip is redundant (2026-04-07)
- Use dataclasses (not Pydantic) for Temporal workflow/activity data types to avoid needing pydantic_data_converter setup — simpler serialization out of the box (2026-04-03)
- For educational demos, wrap pure functions as activities when you want each step visible in event history — observability outweighs minor overhead (2026-04-06)
- When planning TDD steps for Temporal projects, mock activities using `@activity.defn(name="original_name")` in workflow tests to avoid needing a running Temporal server (2026-04-03)
- For Temporal Python workflow tests, use WorkflowEnvironment.start_local() with real activities (not mocks) when the activity is lightweight — simpler and tests the real integration (2026-04-04)
- For sync Temporal activities, `ActivityEnvironment.run()` returns directly — don't use `async def` test methods or `await` (2026-04-05)
- Sending an update to a completed workflow raises `RPCError` (not `WorkflowUpdateFailedError`) — catch both when testing post-completion behavior (2026-04-06)
- When a workflow has async init (activity in `run()`), add `wait_condition` guards in update handlers to prevent update-before-init races (2026-04-06)
- Update validators cannot be async, cannot mutate state, cannot call activities — they're read-only guards that reject by raising exceptions (2026-04-06)
- Use `workflow.random()` for deterministic random and `workflow.now()` for deterministic time — both are replay-safe (2026-04-06)
- For `temporalio.envconfig`, use `ClientConfigProfile.load(config_source=Path(...))` with a project-local TOML file — `str` is TOML content, `Path` is a file path (2026-04-06)
- `WorkflowUpdateFailedError` wraps the real error in `__cause__` — use `str(err.__cause__)` to get the actual message (2026-04-06)

### FastAPI / Testing
- When testing FastAPI with `httpx.ASGITransport`, set `app.state` directly — the lifespan context manager is NOT triggered by ASGITransport (2026-04-06)
- For Temporal API integration tests, use inline Workers per test (not async fixture workers) — ASGITransport and fixture-based workers have event loop cooperation issues that cause hanging (2026-04-06)
- When setting cookies in FastAPI handlers, set them on the actual returned response (e.g. `TemplateResponse`), not on a temporary `Response()` object that's discarded (2026-04-06)

### Tooling
- Run `just format` after writing new files before `just check` — auto-fixes line-length issues and saves a manual edit round-trip (2026-04-04)
- Use `[dependency-groups]` not `[project.optional-dependencies]` for dev deps with uv — optional deps require explicit `uv sync --extra dev` (2026-04-03)
- The correct hatchling build backend is `hatchling.build`, not `hatchling.backends` (2026-04-03)

### Workflow
- When `/simplify` identifies multiple changes, explain all findings and get user approval before editing — don't jump straight to edits (2026-04-07)
- Always check if a file exists before creating it — don't overwrite existing files that aren't in scope of the current task (2026-04-03)
- When replacing a single item in a list, match replacement count exactly — don't expand the list as a side effect (2026-04-05)
- When generating large hardcoded word lists by hand, validate every entry's length programmatically — typos are easy to miss and cause multiple fix cycles (2026-04-05)

### Planning
- When writing implementation plans that describe Python code, always read the user's language-specific rules files first — path-gated rules won't auto-load for markdown files (2026-04-04)
- Add a "Global Code Rules" section at the top of plan.md so execute-plan sees coding standards before any step (2026-04-04)

### Architecture
- For teaching/demo projects, prioritize code clarity and minimal dependencies over production patterns — no factories, registries, or complex abstractions (2026-04-03)
- When promoting a pure function to a Temporal activity, inline the logic rather than wrapping — avoids an extra file and indirection (2026-04-06)

### Testing
- Use `pytest_asyncio.fixture(scope="session")` with manual `shutdown()` for sharing a Temporal test environment — set `asyncio_default_fixture_loop_scope = "session"` in pyproject.toml (2026-04-06)
- When Temporal workflow tests hang, run with `-s` and check worker stderr for "Failing workflow task" messages — the real error is often a serialization failure, not a deadlock (2026-04-06)
- When testing HTML for element counts by class name, use `class="guess-row` not just `guess-row` — the bare string also matches JS/CSS references (2026-04-06)
- Never run multiple pytest processes in background when tests use a session-scoped Temporal test server — zombie servers cause all subsequent runs to hang (2026-04-06)
- When mocking external APIs in tests with session-scoped fixtures, use `autouse=True` in `conftest.py` — prevents rate limiting across all test files (2026-04-06)

### Frontend
- For HTMX partial templates, use `{% include %}` in the full template and render the included file directly for partial responses — cleaner than Jinja2 block inheritance (2026-04-06)
- For HTMX error handling, return 422 + `HX-Trigger` header — client JS catches the event for toasts/shake without replacing the board (2026-04-06)
- When cookies are `httponly=True`, JS cannot modify them — use a server route (e.g., `/new-game`) to set/delete cookies and redirect (2026-04-06)

### Docker
- `temporalio/auto-setup` is deprecated — use `temporalio/temporal:latest` with command `server start-dev --ip 0.0.0.0` (the image entrypoint is already `temporal`, don't repeat it) (2026-04-06)
- When bind-mounting a file into Docker that may not exist yet, Docker creates a directory instead — use named volumes or `touch` the file first (2026-04-06)

### Python
- When using `random.seed()` in threaded code, use `random.Random(seed)` for an isolated instance — global state mutation causes race conditions between concurrent threads (2026-04-07)
- When a two-pass algorithm guarantees all positions are filled, initialize with a real default instead of `None` — avoids misleading `| None` types and dead filter code (2026-04-07)
- Use `@dataclass(frozen=True)` for config/settings objects — immutability enforced at language level, not convention (2026-04-04)
