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

from durable_wordle.config import load_settings
from durable_wordle.models import GameState, GuessResult, LetterFeedback
from durable_wordle.word_lists import get_daily_word
from durable_wordle.workflows import (
    MakeGuessInput,
    UserSessionWorkflow,
    WorkflowInput,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

KEYBOARD_ROWS: list[list[str]] = [
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
    ["Z", "X", "C", "V", "B", "N", "M"],
]


def _build_keyboard_state(
    guesses: list[GuessResult],
) -> dict[str, str]:
    """Build a mapping of each letter to its best-known feedback state.

    Priority: CORRECT > PRESENT > ABSENT. A letter that was CORRECT in any
    guess stays green even if it was ABSENT in another.

    :param guesses: The list of guess results so far.
    :returns: A dict mapping uppercase letters to CSS class names.
    """
    letter_states: dict[str, str] = {}
    priority = {"bg-green-500": 3, "bg-yellow-500": 2, "bg-gray-500": 1}
    feedback_to_css = {
        LetterFeedback.CORRECT: "bg-green-500",
        LetterFeedback.PRESENT: "bg-yellow-500",
        LetterFeedback.ABSENT: "bg-gray-500",
    }
    for guess in guesses:
        for letter_index, letter_feedback in enumerate(guess.feedback):
            letter = guess.word[letter_index]
            css_class = feedback_to_css[letter_feedback]
            current = letter_states.get(letter, "")
            if priority.get(css_class, 0) > priority.get(current, 0):
                letter_states[letter] = css_class
    return letter_states


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
    partial: bool = False,
) -> HTMLResponse:
    """Render the game board template with current state.

    Sets the session_id cookie on the response if this is a new session.
    When ``partial`` is True, renders only the board partial for HTMX swaps.

    :param templates: Jinja2 template engine.
    :param request: The incoming HTTP request.
    :param session_id: The player's session ID.
    :param is_new_session: Whether to set the session cookie on the response.
    :param game_state: Current game state, or None for an empty board.
    :param error_message: Optional error message to display.
    :param status_message: Optional status message to display.
    :param partial: If True, render only the board partial template.
    :returns: Rendered HTML response.
    """
    guesses = game_state.guesses if game_state else []
    status = game_state.status if game_state else "playing"

    if game_state and game_state.status == "won":
        status_message = status_message or "Congratulations! You won!"
    elif game_state and game_state.status == "lost":
        target = game_state.target_word
        status_message = status_message or f"Game over! The word was {target}."

    keyboard_state = _build_keyboard_state(guesses)
    max_guesses = game_state.max_guesses if game_state else 6
    target_word = game_state.target_word if game_state else ""

    template_name = "_board_partial.html" if partial else "index.html"
    context: dict[str, Any] = {
        "request": request,
        "guesses": guesses,
        "status": status,
        "max_guesses": max_guesses,
        "target_word": target_word,
        "error_message": error_message,
        "status_message": status_message,
        "keyboard_rows": KEYBOARD_ROWS,
        "keyboard_state": keyboard_state,
    }
    response = templates.TemplateResponse(
        request=request, name=template_name, context=context
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

        is_htmx = request.headers.get("HX-Request") == "true"
        return _render_board(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
            error_message=error_message,
            partial=is_htmx,
        )

    return app


def create_production_app() -> FastAPI:
    """Create the app using environment-based settings.

    Used as a uvicorn factory entry point via ``--factory``.

    :returns: A configured FastAPI application instance.
    """
    settings = load_settings()
    return create_app(
        temporal_url=settings.temporal_host,
        temporal_namespace=settings.temporal_namespace,
        task_queue=settings.temporal_task_queue,
    )
