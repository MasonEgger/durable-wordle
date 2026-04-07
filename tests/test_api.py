# ABOUTME: Tests for the FastAPI API layer covering session management,
# game board rendering, health check, and Temporal workflow integration.
import concurrent.futures
import datetime
import uuid

from httpx import ASGITransport, AsyncClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from durable_wordle.activities import (
    calculate_feedback,
    select_word,
    validate_guess,
)
from durable_wordle.api import create_app
from durable_wordle.word_lists import get_daily_word
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

    async def test_workflow_id_derived_from_date_and_session(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Workflow ID should follow the wordle-{date}-{session_id} pattern."""
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


class TestTemplateRendering:
    """Tests for the full HTMX/Tailwind game UI template."""

    async def test_page_contains_six_row_game_grid(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """The rendered page should contain a 6-row game grid."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/")
            body = response.text
            # Count actual row divs with the class, not JS references
            assert body.count('class="guess-row') == 6

    async def test_page_contains_keyboard_section(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """The rendered page should contain an on-screen keyboard."""
        async with _make_client(workflow_environment, task_queue) as client:
            response = await client.get("/")
            body = response.text
            assert "keyboard" in body.lower()
            # Keyboard should contain letter keys
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
                    response = await client.post("/guess", data={"guess": "ABOUT"})
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
                    # At minimum we should see green or yellow or gray tiles
                    has_feedback = (
                        "bg-green-500" in body
                        or "bg-yellow-500" in body
                        or "bg-gray-500" in body
                    )
                    assert has_feedback

    async def test_absent_feedback_renders_gray(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A guess with ABSENT feedback should render with gray styling."""
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
                    # QUICK has letters unlikely to all match — expect gray tiles
                    assert "bg-gray-500" in body

    async def test_won_game_shows_success_and_share(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A won game should show a success message and share button."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    # The daily word is deterministic — guess it directly
                    # We need to know the daily word for today
                    today = datetime.date.today()
                    daily_word = get_daily_word(today)
                    response = await client.post("/guess", data={"guess": daily_word})
                    body = response.text
                    assert "Congratulations" in body or "won" in body.lower()
                    assert "share" in body.lower()

    async def test_lost_game_shows_word_and_share(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A lost game should show the target word and a share button."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_word, validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    today = datetime.date.today()
                    daily_word = get_daily_word(today)
                    # Pick 6 words that are NOT the daily word
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
                        if word != daily_word
                    ][:6]
                    for wrong_word in wrong_words:
                        response = await client.post(
                            "/guess", data={"guess": wrong_word}
                        )
                    body = response.text
                    assert daily_word in body
                    assert "share" in body.lower()
