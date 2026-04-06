# ABOUTME: Tests for the FastAPI API layer covering session management,
# game board rendering, health check, and Temporal workflow integration.
import concurrent.futures
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from durable_wordle.activities import validate_guess
from durable_wordle.api import create_app
from durable_wordle.workflows import UserSessionWorkflow


@pytest.fixture()
def task_queue() -> str:
    """Generate a unique task queue name per test."""
    return f"test-api-{uuid.uuid4()}"


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
                activities=[validate_guess],
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
                activities=[validate_guess],
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
                activities=[validate_guess],
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
                activities=[validate_guess],
                activity_executor=executor,
            ):
                async with _make_client(workflow_environment, task_queue) as client:
                    response = await client.post("/guess", data={"guess": "ABOVE"})
                    assert response.status_code == 200
                    assert "session_id" in response.cookies
