# Lessons Learned

## Recent
<!-- 10 most recent lessons, newest first -->

- When testing HTML for element counts by class name, use `class="guess-row` not just `guess-row` — the bare string also matches JS/CSS references (2026-04-06)
- For HTMX partial templates, use `{% include %}` in the full template and render the included file directly for partial responses — cleaner than Jinja2 block inheritance (2026-04-06)
- When testing FastAPI with `httpx.ASGITransport`, set `app.state` directly — the lifespan context manager is NOT triggered by ASGITransport (2026-04-06)
- For Temporal API integration tests, use inline Workers per test (not async fixture workers) — ASGITransport and fixture-based workers have event loop cooperation issues that cause hanging (2026-04-06)
- When setting cookies in FastAPI handlers, set them on the actual returned response (e.g. `TemplateResponse`), not on a temporary `Response()` object that's discarded (2026-04-06)
- When Temporal workflow tests hang, run with `-s` and check worker stderr for "Failing workflow task" messages — the real error is often a serialization failure, not a deadlock (2026-04-06)
- Use `pytest_asyncio.fixture(scope="session")` with manual `shutdown()` for sharing a Temporal test environment — set `asyncio_default_fixture_loop_scope = "session"` in pyproject.toml (2026-04-06)
- Sending an update to a completed workflow raises `RPCError` (not `WorkflowUpdateFailedError`) — catch both when testing post-completion behavior (2026-04-06)
- For sync Temporal activities, `ActivityEnvironment.run()` returns directly — don't use `async def` test methods or `await` (2026-04-05)

## Categories
<!-- Lessons organized by topic -->

### Temporal
- Use dataclasses (not Pydantic) for Temporal workflow/activity data types to avoid needing pydantic_data_converter setup — simpler serialization out of the box (2026-04-03)
- For Temporal demo projects, keep calculate_feedback as a pure function inside the workflow rather than an activity — it's deterministic and doesn't need activity overhead (2026-04-03)
- When planning TDD steps for Temporal projects, mock activities using `@activity.defn(name="original_name")` in workflow tests to avoid needing a running Temporal server (2026-04-03)
- For Temporal Python workflow tests, use WorkflowEnvironment.start_local() with real activities (not mocks) when the activity is lightweight — simpler and tests the real integration (2026-04-04)
- For sync Temporal activities, `ActivityEnvironment.run()` returns directly — don't use `async def` test methods or `await` (2026-04-05)
- Sending an update to a completed workflow raises `RPCError` (not `WorkflowUpdateFailedError`) — catch both when testing post-completion behavior (2026-04-06)

### FastAPI / Testing
- When testing FastAPI with `httpx.ASGITransport`, set `app.state` directly — the lifespan context manager is NOT triggered by ASGITransport (2026-04-06)
- For Temporal API integration tests, use inline Workers per test (not async fixture workers) — ASGITransport and fixture-based workers have event loop cooperation issues that cause hanging (2026-04-06)
- When setting cookies in FastAPI handlers, set them on the actual returned response (e.g. `TemplateResponse`), not on a temporary `Response()` object that's discarded (2026-04-06)

### Tooling
- Run `just format` after writing new files before `just check` — auto-fixes line-length issues and saves a manual edit round-trip (2026-04-04)
- Use `[dependency-groups]` not `[project.optional-dependencies]` for dev deps with uv — optional deps require explicit `uv sync --extra dev` (2026-04-03)
- The correct hatchling build backend is `hatchling.build`, not `hatchling.backends` (2026-04-03)

### Workflow
- Always check if a file exists before creating it — don't overwrite existing files that aren't in scope of the current task (2026-04-03)
- When replacing a single item in a list, match replacement count exactly — don't expand the list as a side effect (2026-04-05)
- When generating large hardcoded word lists by hand, validate every entry's length programmatically — typos are easy to miss and cause multiple fix cycles (2026-04-05)

### Planning
- When writing implementation plans that describe Python code, always read the user's language-specific rules files first — path-gated rules won't auto-load for markdown files (2026-04-04)
- Add a "Global Code Rules" section at the top of plan.md so execute-plan sees coding standards before any step (2026-04-04)

### Architecture
- For teaching/demo projects, prioritize code clarity and minimal dependencies over production patterns — no factories, registries, or complex abstractions (2026-04-03)

### Testing
- Use `pytest_asyncio.fixture(scope="session")` with manual `shutdown()` for sharing a Temporal test environment — set `asyncio_default_fixture_loop_scope = "session"` in pyproject.toml (2026-04-06)
- When Temporal workflow tests hang, run with `-s` and check worker stderr for "Failing workflow task" messages — the real error is often a serialization failure, not a deadlock (2026-04-06)
- When testing HTML for element counts by class name, use `class="guess-row` not just `guess-row` — the bare string also matches JS/CSS references (2026-04-06)

### Frontend
- For HTMX partial templates, use `{% include %}` in the full template and render the included file directly for partial responses — cleaner than Jinja2 block inheritance (2026-04-06)

### Python
- Use `@dataclass(frozen=True)` for config/settings objects — immutability enforced at language level, not convention (2026-04-04)
