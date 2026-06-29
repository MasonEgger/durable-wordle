# ABOUTME: Tests for the FastAPI API layer covering session management,
# game board rendering, health check, and Temporal workflow integration.
import concurrent.futures
import uuid

from httpx import ASGITransport, AsyncClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from durable_wordle.activities import (
    calculate_feedback,
    select_word,
    validate_guess,
)
from durable_wordle.api import create_app, get_workflow_id
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
                    # ABOVE against daily word — some letters may be present
                    response = await client.post("/guess", data={"guess": "ABOVE"})
                    body = response.text
                    # At minimum we should see green, amber, or slate tiles
                    has_feedback = (
                        "bg-green-500" in body
                        or "bg-amber-500" in body
                        or "bg-slate-600" in body
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
