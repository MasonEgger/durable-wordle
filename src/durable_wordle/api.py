# ABOUTME: FastAPI web layer connecting browsers to Temporal workflows.
# Handles session cookies, game board rendering, and Temporal client lifecycle.
import datetime
import json
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.service import RPCError

from durable_wordle.models import (
    GameState,
    GuessResult,
    LetterFeedback,
    MakeGuessInput,
    WorkflowInput,
)
from durable_wordle.workflow import UserSessionWorkflow

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

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
    priority = {"bg-green-500": 3, "bg-yellow-500": 2, "bg-gray-900": 1}
    feedback_to_css = {
        LetterFeedback.CORRECT: "bg-green-500",
        LetterFeedback.PRESENT: "bg-yellow-500",
        LetterFeedback.ABSENT: "bg-gray-900",
    }
    for guess in guesses:
        for letter, letter_feedback in zip(guess.word, guess.feedback):
            css_class = feedback_to_css[letter_feedback]
            current = letter_states.get(letter, "")
            if priority.get(css_class, 0) > priority.get(current, 0):
                letter_states[letter] = css_class
    return letter_states


def _friendly_error(raw_error: str) -> str:
    """Convert raw Temporal error messages into user-friendly text.

    :param raw_error: The raw error string from an RPC or update error.
    :returns: A user-friendly error message.
    """
    lower = raw_error.lower()
    if "not a valid word" in lower or "invalidword" in lower:
        return "Not in word list"
    if "game is already over" in lower or "gameover" in lower:
        return "Game is already over"
    if "must be exactly 5 letters" in lower or "invalidformat" in lower:
        return "Word must be 5 letters"
    if "must contain only letters" in lower:
        return "Letters only"
    return "Something went wrong — try again"


def get_workflow_id(
    session_id: str,
    game_date: datetime.date | None = None,
    game_id: str | None = None,
) -> str:
    """Build a deterministic workflow ID from session and game context.

    :param session_id: The player's session UUID.
    :param game_date: The date of the game (daily mode).
    :param game_id: Unique game identifier (random mode).
    :returns: A workflow ID string.
    """
    if game_id:
        return f"wordle-random-{game_id}"
    return (
        f"wordle-{game_date.isoformat()}-{session_id}"
        if game_date
        else f"wordle-{session_id}"
    )


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
    session_id: str,
    task_queue: str,
    random_mode: bool = False,
) -> WorkflowHandle[UserSessionWorkflow, GameState]:
    """Get an existing workflow handle or start a new workflow.

    :param client: The Temporal client.
    :param workflow_id: The workflow ID.
    :param session_id: The session ID.
    :param task_queue: The task queue for the worker.
    :param random_mode: If True, pick a random word instead of daily.
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
        WorkflowInput(session_id=session_id, random_mode=random_mode),
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
    random_mode: bool = False,
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
    has_started = len(guesses) > 0
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
        "random_mode": random_mode,
        "has_started": has_started,
        "animate": partial,
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
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Return application health status.

        :returns: A dict with ``status`` key.
        """
        return {"status": "ok"}

    @app.get("/new-game")
    async def new_game() -> RedirectResponse:
        """Start a new random game by clearing cookies and redirecting.

        Sets a fresh game_id and clears the session_id so the player
        gets a completely new workflow.

        :returns: A redirect to the home page with fresh cookies.
        """
        response = RedirectResponse(url="/", status_code=302)
        new_game_id = str(uuid.uuid4())
        response.set_cookie(key="game_id", value=new_game_id, httponly=True)
        response.delete_cookie(key="session_id")
        return response

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
        game_id = request.cookies.get("game_id")

        client: Client = app.state.temporal_client
        today = datetime.date.today()
        workflow_id = get_workflow_id(session_id, game_date=today, game_id=game_id)

        game_state = await _query_existing_game(client, workflow_id)

        return _render_board(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
            random_mode=game_id is not None,
        )

    @app.post("/guess", response_class=HTMLResponse)
    async def submit_guess(
        request: Request,
        guess: str = Form(...),
        random_mode: bool = Form(default=False),
    ) -> HTMLResponse:
        """Process a guess submission.

        Starts a new workflow if needed, sends the guess as an Update,
        and returns the updated game board.

        :param request: The incoming HTTP request.
        :param guess: The guessed word from the form.
        :param random_mode: Whether to use random word selection.
        :returns: Rendered HTML game board with updated state.
        """
        existing_session = request.cookies.get("session_id")
        session_id = existing_session or str(uuid.uuid4())
        is_new_session = existing_session is None

        client: Client = app.state.temporal_client
        queue: str = app.state.task_queue
        today = datetime.date.today()

        # For random mode, use game_id cookie; for daily, use date
        game_id = request.cookies.get("game_id")
        if random_mode and not game_id:
            game_id = str(uuid.uuid4())

        workflow_id = get_workflow_id(
            session_id,
            game_date=None if random_mode else today,
            game_id=game_id if random_mode else None,
        )
        handle = await _get_or_start_workflow(
            client, workflow_id, session_id, queue, random_mode=random_mode
        )

        # Send guess via Update
        error_message = ""
        try:
            await handle.execute_update(
                UserSessionWorkflow.make_guess,
                MakeGuessInput(guess=guess.strip().upper()),
            )
        except RPCError as rpc_err:
            error_message = _friendly_error(str(rpc_err))
        except Exception as update_err:
            # WorkflowUpdateFailedError wraps the cause — dig it out
            cause = update_err.__cause__ or update_err
            error_message = _friendly_error(str(cause))

        is_htmx = request.headers.get("HX-Request") == "true"

        # On error for HTMX requests, return 422 with error trigger
        # so the client can show a toast without replacing the board
        if error_message and is_htmx:
            error_response = HTMLResponse(content="", status_code=422)
            error_response.headers["HX-Trigger"] = json.dumps(
                {"guessError": error_message}
            )
            if is_new_session:
                error_response.set_cookie(
                    key="session_id", value=session_id, httponly=True
                )
            if random_mode and game_id:
                error_response.set_cookie(key="game_id", value=game_id, httponly=True)
            return error_response

        # Query current state for rendering — handle is already known valid
        game_state = await handle.query(UserSessionWorkflow.get_game_state)

        response = _render_board(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
            error_message=error_message,
            partial=is_htmx,
            random_mode=random_mode,
        )
        if random_mode and game_id:
            response.set_cookie(key="game_id", value=game_id, httponly=True)
        return response

    return app


def create_production_app() -> FastAPI:
    """Create the app using environment-based settings.

    Uses Temporal's ``envconfig`` to load connection settings from
    ``TEMPORAL_ADDRESS``, ``TEMPORAL_NAMESPACE``, etc. or a TOML
    config file. The task queue is read from ``TEMPORAL_TASK_QUEUE``.

    Used as a uvicorn factory entry point via ``--factory``.

    :returns: A configured FastAPI application instance.
    """
    from pathlib import Path

    from temporalio.envconfig import ClientConfigProfile

    config_file = Path(__file__).resolve().parent.parent.parent / "temporal.toml"
    profile = ClientConfigProfile.load(config_source=config_file)
    connect_config = profile.to_client_connect_config()
    return create_app(
        temporal_url=connect_config.get("target_host", "localhost:7233"),
        temporal_namespace=connect_config.get("namespace", "default"),
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "wordle-tasks"),
    )
