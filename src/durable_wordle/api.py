# ABOUTME: FastAPI web layer connecting browsers to Temporal workflows.
# Handles session cookies, game board rendering, and Temporal client lifecycle.
import asyncio
import datetime
import json
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowHandle,
    WorkflowQueryFailedError,
)
from temporalio.service import RPCError

from durable_wordle.leaderboard import add_entry as lb_add_entry
from durable_wordle.leaderboard import get_madlib_pairs, get_top_entries_for_date
from durable_wordle.models import (
    GameState,
    GuessResult,
    LetterFeedback,
    MakeGuessInput,
    WorkflowInput,
)
from durable_wordle.workflow import UserSessionWorkflow

_LA_TZ = ZoneInfo("America/Los_Angeles")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

KEYBOARD_ROWS: list[list[str]] = [
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
    ["Z", "X", "C", "V", "B", "N", "M"],
]

# CSS classes for board tiles keyed by LetterFeedback.value — single source of truth
# for both the template (tile rendering) and keyboard state builder.
TILE_FEEDBACK_CSS: dict[str, str] = {
    LetterFeedback.CORRECT.value: "bg-green-500 border-green-500",
    LetterFeedback.PRESENT.value: "bg-amber-500 border-amber-500",
    LetterFeedback.ABSENT.value: "bg-wordle-absent border-wordle-absent",
}

# Background CSS for keyboard keys, ordered highest→lowest priority.
_KEY_FEEDBACK_CSS: dict[LetterFeedback, str] = {
    LetterFeedback.CORRECT: "bg-green-500",
    LetterFeedback.PRESENT: "bg-amber-500",
    LetterFeedback.ABSENT: "bg-wordle-absent",
}
# Priority derived from insertion order so the dict above is the only thing to edit.
_KEY_FEEDBACK_PRIORITY: dict[str, int] = {
    css: len(_KEY_FEEDBACK_CSS) - idx
    for idx, css in enumerate(_KEY_FEEDBACK_CSS.values())
}


def _build_keyboard_state(
    guesses: list[GuessResult],
) -> dict[str, str]:
    """Build a mapping of each letter to its best-known feedback CSS class.

    Priority: CORRECT > PRESENT > ABSENT. A letter that was CORRECT in any
    guess stays green even if it appeared as ABSENT in another.

    :param guesses: The list of guess results so far.
    :returns: A dict mapping uppercase letters to keyboard-key CSS class names.
    """
    letter_states: dict[str, str] = {}
    for guess in guesses:
        for letter, letter_feedback in zip(guess.word, guess.feedback):
            css_class = _KEY_FEEDBACK_CSS[letter_feedback]
            current = letter_states.get(letter, "")
            if _KEY_FEEDBACK_PRIORITY.get(css_class, 0) > _KEY_FEEDBACK_PRIORITY.get(
                current, 0
            ):
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


def get_workflow_id(game_id: str) -> str:
    """Build a workflow ID from a game identifier.

    :param game_id: Unique game identifier (UUID).
    :returns: A workflow ID string.
    """
    return f"wordle-random-{game_id}"


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


async def _wait_for_game_state(
    handle: WorkflowHandle[UserSessionWorkflow, GameState],
    retries: int = 100,
    delay: float = 0.1,
) -> GameState:
    """Query the workflow for game state, retrying until the word is selected.

    The workflow runs a ``select_word`` activity before entering its main loop.
    Queries issued before that activity completes will fail with
    ``WorkflowQueryFailedError``. This helper retries with a short sleep.

    :param handle: The workflow handle to query.
    :param retries: Maximum number of attempts.
    :param delay: Seconds to wait between retries.
    :returns: The game state once the workflow has initialized.
    :raises WorkflowQueryFailedError: If the workflow never initializes.
    """
    for attempt in range(retries):
        try:
            return await handle.query(UserSessionWorkflow.get_game_state)
        except WorkflowQueryFailedError:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(delay)
    raise WorkflowQueryFailedError("Workflow did not initialize in time")


async def _get_or_start_workflow(
    client: Client,
    workflow_id: str,
    session_id: str,
    task_queue: str,
) -> WorkflowHandle[UserSessionWorkflow, GameState]:
    """Get an existing workflow handle or start a new workflow.

    :param client: The Temporal client.
    :param workflow_id: The workflow ID.
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
        WorkflowInput(session_id=session_id),
        id=workflow_id,
        task_queue=task_queue,
    )


def _game_context(
    request: Request,
    game_state: GameState | None,
    error_message: str = "",
    status_message: str = "",
    animate: bool = False,
) -> dict[str, Any]:
    """Build the Jinja2 context dict for game-screen and board-partial templates.

    :param request: The incoming HTTP request.
    :param game_state: Current game state, or None for an empty board.
    :param error_message: Optional error message to display.
    :param status_message: Optional status message to display.
    :param animate: If True, apply tile-flip animation to the latest guess row.
    :returns: Template context dict.
    """
    guesses = game_state.guesses if game_state else []
    status = game_state.status if game_state else "playing"

    if game_state and game_state.status == "won":
        status_message = status_message or "🎉 SPLENDID! You won! 🎉"
    elif game_state and game_state.status == "lost":
        target = game_state.target_word
        status_message = status_message or f"✗ OUT OF MOVES! The word was {target} ✗"

    started_at_ts = (
        int(game_state.started_at.timestamp())
        if game_state and game_state.started_at
        else 0
    )

    return {
        "request": request,
        "guesses": guesses,
        "status": status,
        "max_guesses": game_state.max_guesses if game_state else 6,
        "target_word": game_state.target_word if game_state else "",
        "error_message": error_message,
        "status_message": status_message,
        "keyboard_rows": KEYBOARD_ROWS,
        "keyboard_state": _build_keyboard_state(guesses),
        "tile_feedback_css": TILE_FEEDBACK_CSS,
        "has_started": len(guesses) > 0,
        "animate": animate,
        "started_at_ts": started_at_ts,
    }


def _render_full_page(
    templates: Jinja2Templates,
    request: Request,
    session_id: str,
    is_new_session: bool,
    game_state: GameState | None = None,
) -> HTMLResponse:
    """Render the full index.html page with the appropriate screen.

    Shows the start screen when there is no active game, otherwise shows
    the game screen. Sets the session cookie if this is a new session.

    :param templates: Jinja2 template engine.
    :param request: The incoming HTTP request.
    :param session_id: The player's session ID.
    :param is_new_session: Whether to set the session cookie on the response.
    :param game_state: Current game state, or None to show the start screen.
    :returns: Rendered HTML response.
    """
    if game_state is None:
        context: dict[str, Any] = {
            "request": request,
            "screen_state": "start",
            "current_screen_template": "_start_screen.html",
        }
    else:
        context = {
            **_game_context(request, game_state),
            "screen_state": "game",
            "current_screen_template": "_game_screen.html",
        }

    response = templates.TemplateResponse(
        request=request, name="index.html", context=context
    )
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response


def _render_game_screen(
    templates: Jinja2Templates,
    request: Request,
    session_id: str,
    is_new_session: bool,
    game_state: GameState,
    error_message: str = "",
    animate: bool = False,
) -> HTMLResponse:
    """Render just the game-screen fragment for HTMX swaps into #screen.

    :param templates: Jinja2 template engine.
    :param request: The incoming HTTP request.
    :param session_id: The player's session ID.
    :param is_new_session: Whether to set the session cookie on the response.
    :param game_state: Current game state.
    :param error_message: Optional error message to display.
    :param animate: If True, apply tile-flip animation to the latest guess row.
    :returns: Rendered HTML fragment response.
    """
    context = _game_context(
        request,
        game_state,
        error_message=error_message,
        animate=animate,
    )
    response = templates.TemplateResponse(
        request=request, name="_game_screen.html", context=context
    )
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response


def _render_board_partial(
    templates: Jinja2Templates,
    request: Request,
    session_id: str,
    is_new_session: bool,
    game_state: GameState,
    error_message: str = "",
    animate: bool = False,
) -> HTMLResponse:
    """Render just the board partial for HTMX swaps into #game-content.

    :param templates: Jinja2 template engine.
    :param request: The incoming HTTP request.
    :param session_id: The player's session ID.
    :param is_new_session: Whether to set the session cookie on the response.
    :param game_state: Current game state.
    :param error_message: Optional error message to display.
    :param animate: If True, apply tile-flip animation to the latest guess row.
    :returns: Rendered HTML fragment response.
    """
    context = _game_context(
        request,
        game_state,
        error_message=error_message,
        animate=animate,
    )
    response = templates.TemplateResponse(
        request=request, name="_board_partial.html", context=context
    )
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response


def _session_from_request(request: Request) -> tuple[str, bool, str | None]:
    """Extract or mint the session ID and game ID from cookies.

    :param request: The incoming HTTP request.
    :returns: ``(session_id, is_new_session, game_id)`` — ``is_new_session``
        is ``True`` when no session cookie existed yet.
    """
    existing = request.cookies.get("session_id")
    session_id = existing or str(uuid.uuid4())
    game_id = request.cookies.get("game_id")
    return session_id, existing is None, game_id


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
        """Render the full page.

        Shows the start screen when no game is active, otherwise shows the
        game screen with the current board state.

        :param request: The incoming HTTP request.
        :returns: Rendered HTML page.
        """
        session_id, is_new_session, game_id = _session_from_request(request)

        client: Client = app.state.temporal_client
        game_state: GameState | None = None
        if game_id:
            workflow_id = get_workflow_id(game_id)
            game_state = await _query_existing_game(client, workflow_id)

        return _render_full_page(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
        )

    @app.post("/play", response_class=HTMLResponse)
    async def play(
        request: Request,
        first_name: str | None = Form(default=None),
        last_name: str | None = Form(default=None),
        email: str | None = Form(default=None),
        madlib_noun: str | None = Form(default=None),
        madlib_verb: str | None = Form(default=None),
    ) -> HTMLResponse:
        """Start a new game and return the game screen fragment.

        Creates or resumes a workflow for the current session and returns
        the game screen HTML fragment for HTMX to swap into #screen.
        Stores player name, email, and madlib values in cookies for leaderboard use.

        :param request: The incoming HTTP request.
        :param first_name: Player's first name for the leaderboard.
        :param last_name: Player's last name for the leaderboard.
        :param email: Player's email for prize outreach (stored but not displayed).
        :param madlib_noun: The noun for the madlib phrase.
        :param madlib_verb: The past-tense verb for the madlib phrase.
        :returns: Rendered game screen HTML fragment.
        """
        session_id, is_new_session, game_id = _session_from_request(request)

        client: Client = app.state.temporal_client
        queue: str = app.state.task_queue

        if not game_id:
            game_id = str(uuid.uuid4())

        workflow_id = get_workflow_id(game_id)
        handle = await _get_or_start_workflow(client, workflow_id, session_id, queue)
        game_state = await _wait_for_game_state(handle)

        response = _render_game_screen(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
        )
        response.set_cookie(key="game_id", value=game_id, httponly=True)

        player_name = " ".join(
            part
            for part in [(first_name or "").strip(), (last_name or "").strip()]
            if part
        )
        if player_name:
            response.set_cookie(key="player_name", value=player_name, httponly=True)
        if email and email.strip():
            response.set_cookie(key="email", value=email.strip(), httponly=True)
        if madlib_noun and madlib_noun.strip():
            response.set_cookie(
                key="madlib_noun", value=madlib_noun.strip().upper(), httponly=True
            )
        if madlib_verb and madlib_verb.strip():
            response.set_cookie(
                key="madlib_verb", value=madlib_verb.strip().upper(), httponly=True
            )
        return response

    @app.post("/guess", response_class=HTMLResponse)
    async def submit_guess(
        request: Request,
        guess: str = Form(...),
    ) -> HTMLResponse:
        """Process a guess submission.

        Sends the guess as a workflow Update and returns the updated board
        partial for HTMX to swap into #game-content.

        :param request: The incoming HTTP request.
        :param guess: The guessed word from the form.
        :returns: Rendered board partial HTML fragment.
        """
        session_id, is_new_session, game_id = _session_from_request(request)

        client: Client = app.state.temporal_client
        queue: str = app.state.task_queue

        if not game_id:
            game_id = str(uuid.uuid4())

        workflow_id = get_workflow_id(game_id)
        handle = await _get_or_start_workflow(client, workflow_id, session_id, queue)

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
            cause = update_err.__cause__ or update_err
            error_message = _friendly_error(str(cause))

        is_htmx = request.headers.get("HX-Request") == "true"

        # On error for HTMX requests, return 422 with error trigger
        if error_message and is_htmx:
            error_response = HTMLResponse(content="", status_code=422)
            error_response.headers["HX-Trigger"] = json.dumps(
                {"guessError": error_message}
            )
            if is_new_session:
                error_response.set_cookie(
                    key="session_id", value=session_id, httponly=True
                )
            return error_response

        game_state = await handle.query(UserSessionWorkflow.get_game_state)

        response = _render_board_partial(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
            error_message=error_message,
            animate=is_htmx,
        )
        response.set_cookie(key="game_id", value=game_id, httponly=True)
        return response

    def _leaderboard_context(request: Request) -> dict[str, Any]:
        today = str(datetime.datetime.now(_LA_TZ).date())
        entries = get_top_entries_for_date(today)
        madlibs = get_madlib_pairs(entries)
        return {
            "request": request,
            "entries": entries,
            "madlibs_json": json.dumps(madlibs),
            "game_date": today,
        }

    @app.post("/leaderboard", response_class=HTMLResponse)
    async def post_leaderboard(request: Request) -> HTMLResponse:
        """Submit a leaderboard entry and return the leaderboard screen fragment.

        Reads game state from the current workflow and player metadata from
        cookies, then appends a new entry to the JSON leaderboard file.

        :param request: The incoming HTTP request.
        :returns: Rendered leaderboard screen HTML fragment.
        """
        session_id = request.cookies.get("session_id")
        if session_id:
            client: Client = app.state.temporal_client
            game_id = request.cookies.get("game_id")
            today = datetime.datetime.now(_LA_TZ).date()
            game_state = None
            if game_id:
                workflow_id = get_workflow_id(game_id)
                game_state = await _query_existing_game(client, workflow_id)

            if game_state and game_state.status == "won":
                lb_add_entry(
                    player_name=request.cookies.get("player_name", "Anonymous"),
                    email=request.cookies.get("email", ""),
                    guesses=len(game_state.guesses),
                    started_at=game_state.started_at,
                    madlib_noun=request.cookies.get("madlib_noun", ""),
                    madlib_verb=request.cookies.get("madlib_verb", ""),
                    game_date=str(today),
                )

        return templates.TemplateResponse(
            request=request,
            name="_leaderboard_screen.html",
            context=_leaderboard_context(request),
        )

    @app.get("/leaderboard-screen", response_class=HTMLResponse)
    async def leaderboard_screen(request: Request) -> HTMLResponse:
        """Return the leaderboard screen fragment for HTMX swap into #screen.

        :param request: The incoming HTTP request.
        :returns: Rendered leaderboard screen HTML fragment.
        """
        return templates.TemplateResponse(
            request=request,
            name="_leaderboard_screen.html",
            context=_leaderboard_context(request),
        )

    @app.get("/start-screen", response_class=HTMLResponse)
    async def start_screen(request: Request) -> HTMLResponse:
        """Return the start screen fragment for HTMX swap into #screen.

        :param request: The incoming HTTP request.
        :returns: Rendered start screen HTML fragment.
        """
        return templates.TemplateResponse(
            request=request, name="_start_screen.html", context={}
        )

    @app.get("/api/active-game")
    async def active_game(request: Request) -> dict[str, str | None]:
        """Return the currently running game workflow ID and run ID, if any.

        Lists today's running ``wordle-random-*`` workflows and returns the
        most recently started one. Returns ``null`` values when no game is active.

        :param request: The incoming HTTP request.
        :returns: Dict with ``workflow_id`` and ``run_id``, or nulls if idle.
        """
        client: Client = request.app.state.temporal_client
        today = datetime.datetime.now(_LA_TZ).strftime("%Y-%m-%d")
        query = (
            f'WorkflowType="UserSessionWorkflow" AND ExecutionStatus="Running"'
            f' AND WorkflowId STARTS_WITH "wordle-{today}-"'
        )
        try:
            async for execution in client.list_workflows(query):
                return {
                    "workflow_id": execution.id,
                    "run_id": execution.run_id,
                }
        except Exception:
            pass
        return {"workflow_id": None, "run_id": None}

    @app.get("/display", response_class=HTMLResponse)
    async def display(request: Request) -> HTMLResponse:
        """Render the holographic display page for the second screen.

        Shows attract mode (leaderboard + madlib cycling) when no game is
        active, and the Temporal timeline iframe during an active game.

        :param request: The incoming HTTP request.
        :returns: Rendered display HTML page.
        """
        today = datetime.datetime.now(_LA_TZ).strftime("%Y-%m-%d")
        entries = get_top_entries_for_date(today)
        madlibs = get_madlib_pairs(entries)
        return templates.TemplateResponse(
            request=request,
            name="display.html",
            context={
                "request": request,
                "entries": entries,
                "madlibs_json": json.dumps(madlibs),
                "game_date": today,
            },
        )

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
