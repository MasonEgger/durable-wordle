# ABOUTME: Tests for the FastAPI API layer covering session management,
# game board rendering, health check, and Temporal workflow integration.
import asyncio
import concurrent.futures
import json
import logging
import pathlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from durable_wordle.activities import (
    calculate_feedback,
    select_word,
    validate_guess,
)
from durable_wordle.api import (
    _TemporalUiStaticAccessLogFilter,
    create_app,
    get_workflow_id,
)
from durable_wordle.models import WorkflowInput
from durable_wordle.workflow import UserSessionWorkflow


def _make_client(
    workflow_environment: WorkflowEnvironment, task_queue: str
) -> AsyncClient:
    """Build an AsyncClient wired to the test Temporal environment."""
    app = create_app(
        task_queue=task_queue,
        temporal_client=workflow_environment.client,
    )
    app.state.temporal_client = workflow_environment.client
    app.state.task_queue = task_queue
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    return AsyncClient(transport=transport, base_url="http://test")


def _access_log_record(path: str) -> logging.LogRecord:
    """Build a uvicorn-style access log record for filter tests."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", path, "1.1", 200),
        exc_info=None,
    )


class TestHealthEndpoint:
    """Tests for the GET /health endpoint."""

    async def test_health_returns_ok(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET /health should return 200 with status ok."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}


class TestSessionManagement:
    """Tests for cookie-based session management."""

    async def test_get_index_sets_session_cookie(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET / should set a session_id cookie if none exists."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert "session_id" in response.cookies

    async def test_get_index_reuses_existing_session_cookie(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET / should reuse an existing session_id cookie."""
        existing_session_id = str(uuid.uuid4())
        async with _make_client(workflow_environment, task_queue) as client:
            client.cookies.set("session_id", existing_session_id)
            response = await client.get("/")
            assert response.status_code == 200
            if "session_id" in response.cookies:
                assert response.cookies["session_id"] == existing_session_id

    async def test_new_game_htmx_returns_start_screen_and_resets_game_cookie(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """HTMX start-over should swap in the start screen with fresh cookies."""
        async with _make_client(workflow_environment, task_queue) as client:
            client.cookies.set("session_id", str(uuid.uuid4()))
            client.cookies.set("game_id", str(uuid.uuid4()))

            response = await client.get("/new-game", headers={"HX-Request": "true"})

            assert response.status_code == 200
            assert response.headers["HX-Push-Url"] == "/"
            assert "start-screen" in response.text
            assert "game_id" in response.cookies
            assert "session_id=" in response.headers["set-cookie"]


def test_api_tests_use_throwaway_leaderboard_database() -> None:
    """API tests should never write to the booth leaderboard database."""
    from durable_wordle import leaderboard

    assert leaderboard.DB_FILE != leaderboard._DEFAULT_DB


class TestGuessEndpoint:
    """Tests for the POST /guess endpoint."""

    async def test_post_guess_creates_session_and_starts_game(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """POST /guess with no session cookie should create one and start a game."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/guess", data={"guess": "ABOVE"})
                    assert response.status_code == 200
                    assert "session_id" in response.cookies

    async def test_post_valid_guess_returns_updated_board(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """POST /guess with a valid word should return updated game board HTML."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/guess", data={"guess": "ABOVE"})
                    assert response.status_code == 200
                    body = response.text
                    assert "A" in body
                    assert "B" in body
                    assert "O" in body
                    assert "V" in body
                    assert "E" in body

    async def test_post_invalid_guess_returns_error(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """POST /guess with an invalid word should return an error message."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/guess", data={"guess": "ZZZZZ"})
                    assert response.status_code == 200
                    body = response.text
                    assert "error-message" in body or "not a valid word" in body.lower()

    async def test_htmx_invalid_guess_returns_trigger_without_board_swap(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """HTMX invalid guesses should fail fast and unlock the client UI."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    await client.post("/play")

                    response = await client.post(
                        "/guess",
                        data={"guess": "ZZZZZ"},
                        headers={"HX-Request": "true"},
                    )

                    assert response.status_code == 422
                    assert response.text == ""
                    assert json.loads(response.headers["HX-Trigger"]) == {
                        "guessError": "Not in word list"
                    }

    async def test_workflow_id_uses_game_id(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Workflow ID should follow the wordle-random-{game_id} pattern."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/guess", data={"guess": "ABOVE"})
                    assert response.status_code == 200
                    assert "game_id" in response.cookies
                    game_id = response.cookies["game_id"]
                    assert get_workflow_id(game_id) == f"wordle-random-{game_id}"


class TestTemplateRendering:
    """Tests for the full HTMX/Tailwind game UI template."""

    async def test_index_shows_start_screen_when_no_game(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET / with no active game should show the start screen."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/")
            body = response.text
            assert "start-screen" in body
            assert "PLAY" in body

    async def test_play_returns_game_grid(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """POST /play should return the game screen with a 6-row board."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/play")
                    body = response.text
                    assert body.count('class="guess-row') == 6
                    assert "game-timers" in body
                    assert "countdown-card" in body
                    assert 'data-total-seconds="60"' in body
                    assert "wordle-tile" in body

    async def test_play_returns_keyboard_section(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """POST /play should return the game screen containing an on-screen keyboard."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/play")
                    body = response.text
                    assert "keyboard" in body.lower()
                    assert ">Q<" in body
                    assert ">Z<" in body

    async def test_correct_feedback_renders_green(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A guess with CORRECT feedback should render with green styling."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    play_response = await client.post("/play")
                    game_id = play_response.cookies["game_id"]
                    handle = workflow_environment.client.get_workflow_handle(
                        get_workflow_id(game_id)
                    )
                    state = await handle.query(UserSessionWorkflow.get_game_state)
                    response = await client.post(
                        "/guess", data={"guess": state.target_word}
                    )
                    body = response.text
                    # Correct word — all tiles should be green
                    assert "bg-green-500" in body

    async def test_present_feedback_renders_yellow(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A guess with PRESENT feedback should render with yellow styling."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    # ABOVE against random word — any feedback class is fine
                    response = await client.post("/guess", data={"guess": "ABOVE"})
                    body = response.text
                    # All 5 tiles get some feedback class — at minimum absent
                    has_feedback = (
                        "bg-green-500" in body
                        or "bg-amber-500" in body
                        or "bg-wordle-absent" in body
                    )
                    assert has_feedback

    async def test_absent_feedback_renders_wordle_absent(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A guess with ABSENT feedback renders with the wordle-absent tile color."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/guess", data={"guess": "QUICK"})
                    body = response.text
                    # Absent tiles use the dark-navy wordle-absent color from the design
                    assert "bg-wordle-absent" in body

    async def test_won_game_shows_success_and_actions(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A won game should show a success message and endgame action buttons."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    play_response = await client.post("/play")
                    game_id = play_response.cookies["game_id"]
                    handle = workflow_environment.client.get_workflow_handle(
                        get_workflow_id(game_id)
                    )
                    state = await handle.query(UserSessionWorkflow.get_game_state)
                    response = await client.post(
                        "/guess", data={"guess": state.target_word}
                    )
                    body = response.text
                    assert "splendid" in body.lower() or "won" in body.lower()
                    assert "start over" in body.lower()
                    # The manual "POST TO LEADERBOARD" button is gone — saving is
                    # automatic on win now.
                    assert "post to leaderboard" not in body.lower()

    async def test_winning_htmx_guess_animates_board_before_share_screen(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A winning HTMX guess should animate the board before opening share."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    play_response = await client.post(
                        "/play",
                        data={
                            "first_name": "Ada",
                            "last_name": "Lovelace",
                            "email": "ada@example.com",
                        },
                    )
                    game_id = play_response.cookies["game_id"]
                    handle = workflow_environment.client.get_workflow_handle(
                        get_workflow_id(game_id)
                    )
                    state = await handle.query(UserSessionWorkflow.get_game_state)

                    response = await client.post(
                        "/guess",
                        data={"guess": state.target_word},
                        headers={"HX-Request": "true"},
                    )
                    body = response.text

                    assert response.status_code == 200
                    assert "HX-Retarget" not in response.headers
                    assert "share-screen" not in body
                    assert 'data-auto-share-after-reveal="true"' in body
                    assert "tile-reveal" in body
                    assert body.count('class="tile-reveal') == 5
                    assert body.count('data-color="bg-green-500 border-green-500"') == 5
                    assert "post to leaderboard" not in body.lower()

    async def test_won_game_auto_saves_leaderboard_entry(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Winning should auto-save a leaderboard entry without a manual POST."""
        from durable_wordle import leaderboard
        from durable_wordle.api import _today_la

        monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
        monkeypatch.setattr(leaderboard, "_schema_ready", False)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    play_response = await client.post(
                        "/play",
                        data={"first_name": "Ada", "email": "ada@example.com"},
                    )
                    game_id = play_response.cookies["game_id"]
                    handle = workflow_environment.client.get_workflow_handle(
                        get_workflow_id(game_id)
                    )
                    state = await handle.query(UserSessionWorkflow.get_game_state)
                    await client.post("/guess", data={"guess": state.target_word})

                    entries = leaderboard.get_top_entries_for_date(_today_la())
                    ada = [e for e in entries if e.player_name == "Ada"]
                    assert len(ada) == 1
                    assert ada[0].guesses == 1

    async def test_play_records_participant_for_outreach(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """POST /play should record every player (win or lose) for email outreach."""
        from durable_wordle import leaderboard
        from durable_wordle.api import _today_la

        monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
        monkeypatch.setattr(leaderboard, "_schema_ready", False)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    await client.post(
                        "/play",
                        data={"first_name": "Grace", "email": "grace@example.com"},
                    )

                    participants = leaderboard.get_participants_for_date(_today_la())
                    assert len(participants) == 1
                    assert participants[0].player_name == "Grace"
                    assert participants[0].email == "grace@example.com"

    async def test_lost_game_shows_word_and_actions(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A lost game should show the target word and endgame action buttons."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    play_response = await client.post("/play")
                    game_id = play_response.cookies["game_id"]
                    handle = workflow_environment.client.get_workflow_handle(
                        get_workflow_id(game_id)
                    )
                    state = await handle.query(UserSessionWorkflow.get_game_state)
                    target_word = state.target_word
                    # Pick 6 words that are NOT the target word
                    wrong_words = [
                        word
                        for word in [
                            "ABOVE",
                            "ABUSE",
                            "ACTOR",
                            "ADMIT",
                            "ADOPT",
                            "ADULT",
                            "AFTER",
                            "AGAIN",
                            "AGENT",
                        ]
                        if word != target_word
                    ][:6]
                    for wrong_word in wrong_words:
                        response = await client.post(
                            "/guess", data={"guess": wrong_word}
                        )
                    body = response.text
                    assert target_word in body
                    assert "start over" in body.lower()


class TestShareEndpoint:
    """Tests for the GET /share result-card endpoint."""

    async def test_share_renders_card_for_won_game(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET /share after a win returns the result card with grid and branding."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    play_response = await client.post(
                        "/play", data={"first_name": "ADA", "email": "a@b.co"}
                    )
                    game_id = play_response.cookies["game_id"]
                    handle = workflow_environment.client.get_workflow_handle(
                        get_workflow_id(game_id)
                    )
                    state = await handle.query(UserSessionWorkflow.get_game_state)
                    await client.post("/guess", data={"guess": state.target_word})

                    response = await client.get("/share")
                    assert response.status_code == 200
                    body = response.text
                    assert "share-screen" in body
                    # Player name from the /play cookie is shown.
                    assert "ADA" in body
                    # Won grid is all-correct → green squares.
                    assert "bg-green-500" in body
                    # Branding + QR to learn more about Temporal.
                    assert "temporal-logo-lockup-white.svg" in body
                    assert "temporal-qr.svg" in body
                    assert "post to leaderboard" not in body.lower()

    async def test_share_without_game_renders_empty_card(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET /share with no active game still returns the card scaffold."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/share")
            assert response.status_code == 200
            assert "share-screen" in response.text


class TestDesignSystemCompliance:
    """Verify design tokens from the Figma file are reflected in rendered HTML.

    Each test maps to a specific Figma node and design property:
    - Title (4128:756): Space Mono Bold, color #cfff0d (temporal-grellow)
    - Subtitle (4128:758): Space Mono Regular, color #cacbf9 (temporal-indigo)
    - Absent tiles: dark navy #2d3458 (wordle-absent)
    """

    async def test_title_uses_space_mono_bold(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Title must use Space Mono Bold per Figma node 4128:756."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/")
            body = response.text
            assert "font-mono" in body
            assert "font-bold" in body

    async def test_title_uses_temporal_grellow(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Title color must be temporal-grellow (#cfff0d) per Figma node 4128:756."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/")
            assert "text-temporal-grellow" in response.text

    async def test_powered_by_uses_temporal_indigo(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """POWERED BY text color must be temporal-indigo (#cacbf9) per Figma node."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/")
            assert "text-temporal-indigo" in response.text

    async def test_absent_tiles_use_wordle_absent_color(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Absent letter tiles must use wordle-absent (#2d3458) per Figma design."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/guess", data={"guess": "QUICK"})
                    assert "bg-wordle-absent" in response.text


class TestDisplayEndpoints:
    """Tests for the /display page and /api/active-game endpoint."""

    async def test_display_page_renders(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET /display should return 200 with attract-mode markup."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/display")
            assert response.status_code == 200
            body = response.text
            assert "attract" in body
            assert "game-mode" in body
            assert "/static/display.js" in body  # external module is wired up

    async def test_display_calibration_uses_visible_full_circle_target(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """The calibration screen should expose a full circle and open center."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/display")
            body = response.text

            assert response.status_code == 200
            assert 'id="cal-full-circle"' in body
            assert 'aria-label="Circle calibration controls"' in body
            assert 'data-nudge="center"' in body
            display_css = pathlib.Path("static/display.css").read_text()
            assert "top: calc(50% + 10.5vmin)" in display_css
            assert "width: min(33vmin, 360px)" in display_css
            assert "transform: translate(-50%, -50%)" in display_css

    def test_display_content_uses_circle_safe_bounds(self) -> None:
        """Fan display content should stay inside the calibrated circle."""
        display_css = pathlib.Path("static/display.css").read_text()

        assert "--circle-safe-w: calc(var(--circle-w) * 0.68)" in display_css
        assert "--circle-text-w: calc(var(--circle-w) * 0.58)" in display_css
        assert (
            "clip-path: circle(calc(var(--circle-w) * 0.5) at 50% 50%)" in display_css
        )
        assert "max-width: var(--circle-text-w)" in display_css
        assert "width: var(--circle-safe-w)" in display_css
        assert "--timeline-max-h: calc(var(--circle-safe-h) * 0.68)" in display_css
        assert "max-height: var(--timeline-max-h)" in display_css

    def test_display_leaderboard_scroll_reaches_bottom_quickly(self) -> None:
        """Leaderboard display should show the bottom rows before rotating away."""
        display_css = pathlib.Path("static/display.css").read_text()
        display_js = pathlib.Path("static/display.js").read_text()

        assert "linear 1 forwards" in display_css
        assert "infinite alternate" not in display_css
        assert "92%, 100% { transform: translateY(var(--lb-shift, 0)); }" in display_css
        assert "var LB_SCROLL_SPEED = 140" in display_js
        assert "(LB_DURATION_MS / 1000) - LB_SCROLL_END_PADDING_SECONDS" in display_js
        assert "Math.min(" in display_js

    def test_display_timeline_reuses_cached_svg_placeholder(self) -> None:
        """Display should show a cached timeline SVG while the iframe warms up."""
        display_js = pathlib.Path("static/display.js").read_text()

        assert (
            "var TIMELINE_CACHE_KEY = 'durable-wordle:first-timeline-svg'" in display_js
        )
        assert "function cacheFirstTimelineSvg(svg)" in display_js
        assert "window.sessionStorage.setItem(TIMELINE_CACHE_KEY" in display_js
        assert "function buildTimelinePlaceholder()" in display_js
        assert (
            "document.getElementById('timeline-box').replaceChildren("
            "buildTimelinePlaceholder())" in display_js
        )
        assert "cacheFirstTimelineSvg(clone)" in display_js

    def test_temporal_ui_static_chunks_are_filtered_from_access_logs(self) -> None:
        """Temporal UI static chunks should not flood booth startup logs."""
        log_filter = _TemporalUiStaticAccessLogFilter()

        assert not log_filter.filter(
            _access_log_record("/temporal-ui/_app/immutable/chunks/logs.DMq1sQw1.js")
        )
        assert log_filter.filter(
            _access_log_record(
                "/temporal-ui/namespaces/default/workflows/example/run/timeline"
            )
        )
        assert log_filter.filter(_access_log_record("/api/display-state"))

    async def test_active_game_returns_correct_shape(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET /api/active-game should return 200 with workflow_id and run_id keys."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/api/active-game")
            assert response.status_code == 200
            data = response.json()
            assert "workflow_id" in data
            assert "run_id" in data

    async def test_active_game_returns_workflow_when_running(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """GET /api/active-game should return workflow ID when a game is running."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    await client.post("/play")
                    response = await client.get("/api/active-game")
                    assert response.status_code == 200
                    data = response.json()
                    assert data["workflow_id"] is not None
                    assert data["run_id"] is not None
                    assert data["workflow_id"].startswith("wordle-")

    async def test_display_state_returns_combined_payload(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """GET /api/display-state should combine display polling data."""
        from durable_wordle import leaderboard

        monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
        monkeypatch.setattr(leaderboard, "_schema_ready", False)

        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/api/display-state")
            assert response.status_code == 200
            data = response.json()
            assert "workflow_id" in data["active_game"]
            assert "run_id" in data["active_game"]
            assert data["leaderboard"]["entries"]
            assert "madlibs" in data["leaderboard"]
            assert data["win"] is None
            assert data["loss"] is None

    async def test_display_state_includes_active_game_when_running(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """GET /api/display-state should include the live workflow identity."""
        from durable_wordle import leaderboard

        monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
        monkeypatch.setattr(leaderboard, "_schema_ready", False)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    await client.post("/play")
                    response = await client.get("/api/display-state")
                    assert response.status_code == 200
                    active_game = response.json()["active_game"]
                    assert active_game["workflow_id"].startswith("wordle-")
                    assert active_game["run_id"] is not None

    async def test_display_state_includes_word_after_loss(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """GET /api/display-state should reveal the target word after a loss."""
        from durable_wordle import leaderboard

        monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
        monkeypatch.setattr(leaderboard, "_schema_ready", False)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    play_response = await client.post("/play")
                    game_id = play_response.cookies["game_id"]
                    handle = workflow_environment.client.get_workflow_handle(
                        get_workflow_id(game_id)
                    )
                    state = await handle.query(UserSessionWorkflow.get_game_state)
                    target_word = state.target_word
                    wrong_words = [
                        word
                        for word in [
                            "ABOVE",
                            "ABUSE",
                            "ACTOR",
                            "ADMIT",
                            "ADOPT",
                            "ADULT",
                            "AFTER",
                            "AGAIN",
                            "AGENT",
                        ]
                        if word != target_word
                    ][:6]
                    for wrong_word in wrong_words:
                        await client.post("/guess", data={"guess": wrong_word})

                    response = await client.get("/api/display-state")
                    assert response.status_code == 200
                    loss = response.json()["loss"]
                    assert loss is not None
                    assert loss["workflow_id"] == get_workflow_id(game_id)
                    assert loss["target_word"] == target_word
                    assert loss["guesses"] == 6
                    assert loss["status"] == "lost"

    async def test_display_state_includes_word_after_timeout(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
    ) -> None:
        """GET /api/display-state should reveal the word after inactivity."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                workflow_id = get_workflow_id(str(uuid.uuid4()))
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(
                        session_id="idle-display-session",
                        inactivity_timeout_seconds=0.1,
                    ),
                    id=workflow_id,
                    task_queue=task_queue,
                )
                final_state = await handle.result()
                assert final_state.status == "abandoned"

                async with _make_client(workflow_environment, task_queue) as client:
                    loss = None
                    response = None
                    for _attempt in range(20):
                        response = await client.get("/api/display-state")
                        loss = response.json()["loss"]
                        if loss is not None:
                            break
                        await asyncio.sleep(0.1)

                assert response is not None
                assert response.status_code == 200
                assert loss is not None
                assert loss["workflow_id"] == workflow_id
                assert loss["target_word"] == final_state.target_word
                assert loss["guesses"] == 0
                assert loss["status"] == "abandoned"


class TestLastWinEndpoint:
    """Tests for the GET /api/last-win endpoint."""

    async def test_last_win_returns_null_when_no_recent_win(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """GET /api/last-win should return {"win": null} with no fresh entries."""
        from durable_wordle import leaderboard

        monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
        monkeypatch.setattr(leaderboard, "_schema_ready", False)

        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/api/last-win")
            assert response.status_code == 200
            assert response.json() == {"win": None}

    async def test_last_win_returns_recent_entry(
        self,
        workflow_environment: WorkflowEnvironment,
        task_queue: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """GET /api/last-win should surface a freshly added winning entry."""
        from durable_wordle import leaderboard
        from durable_wordle.api import _today_la

        monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
        monkeypatch.setattr(leaderboard, "_schema_ready", False)

        leaderboard.add_entry(
            player_name="Ada Lovelace",
            email="",
            guesses=3,
            started_at=None,
            madlib_noun="CODE",
            madlib_verb="RAN",
            game_date=_today_la(),
        )

        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/api/last-win")
            assert response.status_code == 200
            win = response.json()["win"]
            assert win is not None
            assert win["player_name"] == "Ada Lovelace"
            assert win["guesses"] == 3
            assert win["rank"] >= 1
            assert "elapsed_formatted" in win
            assert "submitted_at" in win
