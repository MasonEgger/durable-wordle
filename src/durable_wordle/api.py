# ABOUTME: FastAPI web layer connecting browsers to Temporal workflows.
# Handles session cookies, game board rendering, and Temporal client lifecycle.
import datetime
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.service import RPCError

from durable_wordle.models import GameState
from durable_wordle.word_lists import get_daily_word
from durable_wordle.workflows import (
    MakeGuessInput,
    UserSessionWorkflow,
    WorkflowInput,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def get_workflow_id(game_date: datetime.date, session_id: str) -> str:
    """Build a deterministic workflow ID from date and session.

    :param game_date: The date of the game.
    :param session_id: The player's session UUID.
    :returns: A workflow ID in the format ``wordle-{date}-{session_id}``.
    """
    return f"wordle-{game_date.isoformat()}-{session_id}"


async def _query_existing_game(client: Client, workflow_id: str) -> GameState | None:
    """Query an existing workflow for its current game state.

    :param client: The Temporal client.
    :param workflow_id: The workflow ID to query.
    :returns: The game state if the workflow exists, otherwise None.
    """
    try:
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        if description.status in (
            WorkflowExecutionStatus.RUNNING,
            WorkflowExecutionStatus.COMPLETED,
        ):
            return await handle.query(UserSessionWorkflow.get_game_state)
    except RPCError:
        pass
    return None


async def _get_or_start_workflow(
    client: Client,
    workflow_id: str,
    target_word: str,
    session_id: str,
    task_queue: str,
) -> WorkflowHandle[UserSessionWorkflow, GameState]:
    """Get an existing workflow handle or start a new workflow.

    :param client: The Temporal client.
    :param workflow_id: The workflow ID.
    :param target_word: The target word for a new game.
    :param session_id: The session ID.
    :param task_queue: The task queue for the worker.
    :returns: A workflow handle.
    """
    try:
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        if description.status in (
            WorkflowExecutionStatus.RUNNING,
            WorkflowExecutionStatus.COMPLETED,
        ):
            return handle
    except RPCError:
        pass

    return await client.start_workflow(
        UserSessionWorkflow.run,
        WorkflowInput(target_word=target_word, session_id=session_id),
        id=workflow_id,
        task_queue=task_queue,
    )


def _render_board(
    templates: Jinja2Templates,
    request: Request,
    session_id: str,
    is_new_session: bool,
    game_state: GameState | None = None,
    error_message: str = "",
    status_message: str = "",
) -> HTMLResponse:
    """Render the game board template with current state.

    Sets the session_id cookie on the response if this is a new session.

    :param templates: Jinja2 template engine.
    :param request: The incoming HTTP request.
    :param session_id: The player's session ID.
    :param is_new_session: Whether to set the session cookie on the response.
    :param game_state: Current game state, or None for an empty board.
    :param error_message: Optional error message to display.
    :param status_message: Optional status message to display.
    :returns: Rendered HTML response.
    """
    guesses = game_state.guesses if game_state else []
    status = game_state.status if game_state else "playing"

    if game_state and game_state.status == "won":
        status_message = status_message or "Congratulations! You won!"
    elif game_state and game_state.status == "lost":
        target = game_state.target_word
        status_message = status_message or f"Game over! The word was {target}."

    context: dict[str, Any] = {
        "request": request,
        "guesses": guesses,
        "status": status,
        "error_message": error_message,
        "status_message": status_message,
    }
    response = templates.TemplateResponse(
        request=request, name="index.html", context=context
    )
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response


def create_app(
    temporal_url: str = "localhost:7233",
    temporal_namespace: str = "default",
    task_queue: str = "wordle-tasks",
    temporal_client: Client | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    :param temporal_url: Address of the Temporal server.
    :param temporal_namespace: Temporal namespace to use.
    :param task_queue: Task queue for the Temporal worker.
    :param temporal_client: Optional pre-connected Temporal client (for testing).
    :returns: A configured FastAPI application instance.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage Temporal client lifecycle."""
        if temporal_client is not None:
            app.state.temporal_client = temporal_client
        else:
            app.state.temporal_client = await Client.connect(
                temporal_url, namespace=temporal_namespace
            )
        app.state.task_queue = task_queue
        yield

    app = FastAPI(title="Durable Wordle", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Return application health status.

        :returns: A dict with ``status`` key.
        """
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """Render the game board page.

        Reads the session cookie and queries an existing workflow for state.
        If no workflow exists, renders an empty board.

        :param request: The incoming HTTP request.
        :returns: Rendered HTML game board.
        """
        existing_session = request.cookies.get("session_id")
        session_id = existing_session or str(uuid.uuid4())
        is_new_session = existing_session is None

        client: Client = app.state.temporal_client
        today = datetime.date.today()
        workflow_id = get_workflow_id(today, session_id)

        game_state = await _query_existing_game(client, workflow_id)

        return _render_board(
            templates, request, session_id, is_new_session, game_state=game_state
        )

    @app.post("/guess", response_class=HTMLResponse)
    async def submit_guess(request: Request, guess: str = Form(...)) -> HTMLResponse:
        """Process a guess submission.

        Starts a new workflow if needed, sends the guess as an Update,
        and returns the updated game board.

        :param request: The incoming HTTP request.
        :param guess: The guessed word from the form.
        :returns: Rendered HTML game board with updated state.
        """
        existing_session = request.cookies.get("session_id")
        session_id = existing_session or str(uuid.uuid4())
        is_new_session = existing_session is None

        client: Client = app.state.temporal_client
        queue: str = app.state.task_queue
        today = datetime.date.today()
        workflow_id = get_workflow_id(today, session_id)
        target_word = get_daily_word(today)

        handle = await _get_or_start_workflow(
            client, workflow_id, target_word, session_id, queue
        )

        # Send guess via Update
        error_message = ""
        try:
            await handle.execute_update(
                UserSessionWorkflow.make_guess,
                MakeGuessInput(guess=guess.strip().upper()),
            )
        except RPCError as rpc_err:
            error_message = str(rpc_err)
        except Exception as update_err:
            error_message = str(update_err)

        # Query current state for rendering
        game_state = await _query_existing_game(client, workflow_id)

        return _render_board(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
            error_message=error_message,
        )

    return app
