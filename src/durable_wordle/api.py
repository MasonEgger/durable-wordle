# ABOUTME: FastAPI web layer connecting browsers to Temporal workflows.
# Handles session cookies, game board rendering, and Temporal client lifecycle.
import asyncio
import datetime
import json
import os
import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
from durable_wordle.leaderboard import (
    format_elapsed,
    get_madlib_pairs,
    get_recent_win,
    get_top_entries_for_date,
)
from durable_wordle.models import (
    GameState,
    GuessResult,
    LetterFeedback,
    MakeGuessInput,
    WorkflowInput,
)
from durable_wordle.workflow import UserSessionWorkflow

_LA_TZ = ZoneInfo("America/Los_Angeles")


def _today_la() -> str:
    """Return today's date in the booth timezone as an ISO ``YYYY-MM-DD`` string.

    Single source for the leaderboard's daily-rollover boundary so every caller
    agrees on which day an entry belongs to.

    :returns: ISO date string in America/Los_Angeles.
    """
    return datetime.datetime.now(_LA_TZ).strftime("%Y-%m-%d")


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

# Emoji squares for the shareable result card, keyed by LetterFeedback.value.
SHARE_EMOJI: dict[str, str] = {
    LetterFeedback.CORRECT.value: "🟩",
    LetterFeedback.PRESENT.value: "🟨",
    LetterFeedback.ABSENT.value: "⬛",
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


async def _terminate_other_running_games(client: Client, keep_workflow_id: str) -> None:
    """Terminate every running game workflow except the one to keep.

    Abandoned games (closed before win/loss) leave their workflow running
    forever. Since only one game is ever active at the booth, this enforces a
    single running workflow so the display tracks the right game and stale
    timelines do not pile up.

    :param client: The Temporal client.
    :param keep_workflow_id: Workflow ID of the current game to preserve.
    """
    query = 'WorkflowType="UserSessionWorkflow" AND ExecutionStatus="Running"'
    try:
        async for execution in client.list_workflows(query):
            if execution.id == keep_workflow_id:
                continue
            try:
                handle = client.get_workflow_handle(execution.id)
                await handle.terminate("Superseded by a new game")
            except RPCError:
                pass
    except Exception:
        pass


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
    elif game_state and game_state.status == "abandoned":
        target = game_state.target_word
        status_message = status_message or f"⏱ TIME'S UP! The word was {target}"

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

        # Enforce a single running game: terminate any abandoned workflows so the
        # display tracks only the current game.
        await _terminate_other_running_games(client, workflow_id)

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
        today = _today_la()
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
                    game_date=_today_la(),
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

    @app.get("/board", response_class=HTMLResponse)
    async def board(request: Request) -> HTMLResponse:
        """Return the current game's board partial.

        Used by the inactivity-countdown reveal: when the client timer expires,
        it fetches the freshly-abandoned board (which now shows the target word).

        :param request: The incoming HTTP request.
        :returns: Board partial fragment, or 204 if there is no game.
        """
        session_id, is_new_session, game_id = _session_from_request(request)
        client: Client = app.state.temporal_client
        game_state = None
        if game_id:
            game_state = await _query_existing_game(client, get_workflow_id(game_id))
        if game_state is None:
            return HTMLResponse(content="", status_code=204)
        return _render_board_partial(
            templates, request, session_id, is_new_session, game_state
        )

    @app.get("/share", response_class=HTMLResponse)
    async def share(request: Request) -> HTMLResponse:
        """Render the shareable "I beat Durable Wordle!" result card.

        Builds a colored-square grid of the player's guesses, their guess count
        and elapsed time, name, Temporal branding, and a QR code linking to
        ``temporal.io``. Rendered from the current game's workflow state plus the
        player-name cookie, swapped into ``#screen`` like the other screens.

        :param request: The incoming HTTP request.
        :returns: Rendered share screen HTML fragment.
        """
        session_id, is_new_session, game_id = _session_from_request(request)
        client: Client = app.state.temporal_client

        game_state: GameState | None = None
        if game_id:
            game_state = await _query_existing_game(client, get_workflow_id(game_id))

        guesses = game_state.guesses if game_state else []
        elapsed_seconds = 0
        if game_state and game_state.started_at:
            started_at = game_state.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=datetime.UTC)
            now = datetime.datetime.now(datetime.UTC)
            elapsed_seconds = max(0, int((now - started_at).total_seconds()))

        emoji_grid = "\n".join(
            "".join(SHARE_EMOJI[fb.value] for fb in guess.feedback) for guess in guesses
        )

        context: dict[str, Any] = {
            "request": request,
            "guesses": guesses,
            "tile_feedback_css": TILE_FEEDBACK_CSS,
            "player_name": request.cookies.get("player_name", ""),
            "guess_count": len(guesses),
            "elapsed_formatted": format_elapsed(elapsed_seconds),
            "won": bool(game_state and game_state.status == "won"),
            "emoji_grid": emoji_grid,
        }
        response = templates.TemplateResponse(
            request=request, name="_share_screen.html", context=context
        )
        if is_new_session:
            response.set_cookie(key="session_id", value=session_id, httponly=True)
        return response

    @app.get("/api/leaderboard")
    async def api_leaderboard(request: Request) -> dict[str, Any]:
        """Return today's leaderboard as JSON for the live-updating display.

        :param request: The incoming HTTP request.
        :returns: Dict with ``game_date``, ``entries`` (rank/name/guesses/time),
            and deduplicated ``madlibs`` pairs.
        """
        today = _today_la()
        entries = get_top_entries_for_date(today)
        return {
            "game_date": today,
            "entries": [
                {
                    "player_name": entry.player_name,
                    "guesses": entry.guesses,
                    "elapsed_formatted": entry.elapsed_formatted,
                }
                for entry in entries
            ],
            "madlibs": get_madlib_pairs(entries),
        }

    @app.get("/api/last-win")
    async def last_win(request: Request) -> dict[str, Any]:
        """Return the most recent winning entry within the last few seconds.

        The display polls this to fire a one-off win celebration. Returns
        ``{"win": null}`` when there is no fresh win; otherwise the entry's
        name, guess count, formatted time, day rank, and ``submitted_at``
        (used client-side to dedupe so each win celebrates exactly once).

        :param request: The incoming HTTP request.
        :returns: Dict with a ``win`` object, or ``{"win": null}`` when idle.
        """
        result = get_recent_win(_today_la())
        if result is None:
            return {"win": None}
        entry, rank = result
        return {
            "win": {
                "player_name": entry.player_name,
                "guesses": entry.guesses,
                "elapsed_formatted": entry.elapsed_formatted,
                "rank": rank,
                "submitted_at": entry.submitted_at,
            }
        }

    @app.get("/api/active-game")
    async def active_game(request: Request) -> dict[str, str | None]:
        """Return the most recently started running game workflow, if any.

        Orders running ``UserSessionWorkflow`` executions by start time so the
        display always tracks the newest game even if stale workflows linger.
        Returns ``null`` values when no game is active.

        :param request: The incoming HTTP request.
        :returns: Dict with ``workflow_id`` and ``run_id``, or nulls if idle.
        """
        client: Client = request.app.state.temporal_client
        query = 'WorkflowType="UserSessionWorkflow" AND ExecutionStatus="Running"'
        try:
            running = [execution async for execution in client.list_workflows(query)]
        except Exception:
            running = []
        if not running:
            return {"workflow_id": None, "run_id": None}
        # Most recently started game wins (visibility ORDER BY is not supported
        # by the time-skipping test server, so sort client-side).
        newest = max(
            running,
            key=lambda execution: (
                execution.start_time
                or datetime.datetime.min.replace(tzinfo=datetime.UTC)
            ),
        )
        return {"workflow_id": newest.id, "run_id": newest.run_id}

    async def _temporal_proxy(upstream_path: str, request: Request) -> Response:
        """Forward a request to the Temporal dev server at localhost:8233.

        Strips X-Frame-Options and CSP headers so responses can be embedded in
        an iframe, and rewrites root-relative asset URLs in HTML responses so
        they continue to route through the ``/temporal-ui/`` proxy prefix.

        :param upstream_path: Path on the Temporal server (no leading slash).
        :param request: The incoming HTTP request.
        :returns: Proxied response with frame-busting headers removed.
        """
        query = request.url.query
        target_url = f"http://localhost:8233/{upstream_path}"
        if query:
            target_url = f"{target_url}?{query}"

        # Generous timeout: the UI long-polls history with waitNewEvent=true,
        # which the Temporal server holds open until an event or its own timeout.
        timeout = httpx.Timeout(70.0, connect=5.0)
        body = await request.body()
        forward_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "origin", "referer", "content-length")
        }
        forward_headers["accept-encoding"] = "identity"

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=timeout
        ) as http_client:
            try:
                upstream = await http_client.request(
                    request.method,
                    target_url,
                    headers=forward_headers,
                    content=body or None,
                )
            except httpx.ConnectError:
                return Response(
                    content="Temporal UI not available",
                    status_code=502,
                    media_type="text/plain",
                )
            except httpx.TimeoutException:
                # Long-poll exceeded our window — return empty so the UI retries
                return Response(status_code=204)

        skip_headers = {
            "x-frame-options",
            "content-security-policy",
            "transfer-encoding",
        }
        headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in skip_headers
        }

        content = upstream.content
        if "text/html" in upstream.headers.get("content-type", ""):
            text = content.decode("utf-8", errors="replace")
            # Rewrite root-relative asset/link URLs to route through the proxy
            text = text.replace('src="/', 'src="/temporal-ui/')
            text = text.replace("src='/", "src='/temporal-ui/")
            text = text.replace('href="/', 'href="/temporal-ui/')
            text = text.replace("href='/", "href='/temporal-ui/")
            # Rewrite dynamic ES-module imports in inline scripts
            # (e.g. import("/_app/...")) which the above does not catch
            text = text.replace('import("/', 'import("/temporal-ui/')
            text = text.replace("import('/", "import('/temporal-ui/")
            # Tell SvelteKit its base path so the client router strips the
            # /temporal-ui prefix and matches its routes correctly
            text = text.replace('base: ""', 'base: "/temporal-ui"')
            text = text.replace("base: ''", 'base: "/temporal-ui"')
            # Strip inline CSP meta tag (blocks script loading in iframe)
            text = re.sub(
                r'<meta[^>]+http-equiv=["\']content-security-policy["\'][^>]*>',
                "",
                text,
                flags=re.IGNORECASE,
            )
            content = text.encode("utf-8")
            headers["content-length"] = str(len(content))

        return Response(
            content=content,
            status_code=upstream.status_code,
            headers=headers,
            media_type=upstream.headers.get("content-type"),
        )

    _PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

    @app.api_route(
        "/temporal-ui/{path:path}",
        methods=_PROXY_METHODS,
        include_in_schema=False,
    )
    async def temporal_ui_proxy(path: str, request: Request) -> Response:
        """Reverse-proxy the Temporal UI assets, pages, and base-prefixed API.

        :param path: URL path under the Temporal UI prefix.
        :param request: The incoming HTTP request.
        :returns: Proxied response.
        """
        return await _temporal_proxy(path, request)

    @app.api_route(
        "/api/v1/{path:path}",
        methods=_PROXY_METHODS,
        include_in_schema=False,
    )
    async def temporal_api_proxy(path: str, request: Request) -> Response:
        """Reverse-proxy the Temporal server API so the UI's fetch calls work.

        The Temporal UI (SvelteKit) calls ``/api/v1/...`` using absolute paths.
        When the UI is embedded via the ``/temporal-ui/`` proxy those calls hit
        this app instead of ``localhost:8233``. This route forwards them.

        :param path: URL path under ``/api/v1/``.
        :param request: The incoming HTTP request.
        :returns: Proxied response.
        """
        return await _temporal_proxy(f"api/v1/{path}", request)

    @app.get("/display", response_class=HTMLResponse)
    async def display(request: Request) -> HTMLResponse:
        """Render the holographic display page for the second screen.

        Shows attract mode (leaderboard + madlib cycling) when no game is
        active, and the Temporal timeline iframe during an active game.

        :param request: The incoming HTTP request.
        :returns: Rendered display HTML page.
        """
        today = _today_la()
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
    from temporalio.envconfig import ClientConfigProfile

    config_file = Path(__file__).resolve().parent.parent.parent / "temporal.toml"
    profile = ClientConfigProfile.load(config_source=config_file)
    connect_config = profile.to_client_connect_config()
    return create_app(
        temporal_url=connect_config.get("target_host", "localhost:7233"),
        temporal_namespace=connect_config.get("namespace", "default"),
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "wordle-tasks"),
    )
