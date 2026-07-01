# ABOUTME: FastAPI web layer connecting browsers to Temporal workflows.
# Handles session cookies, game board rendering, and Temporal client lifecycle.
import asyncio
import datetime
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

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

from durable_wordle.booth.leaderboard import add_entry as lb_add_entry
from durable_wordle.booth.leaderboard import (
    format_elapsed,
    get_madlib_pairs,
    get_recent_win,
    get_top_entries_for_date,
    record_participant,
)
from durable_wordle.booth.proxy import proxy_router
from durable_wordle.models import (
    GameMode,
    GameState,
    MakeGuessInput,
    WorkflowInput,
)
from durable_wordle.rendering import (
    SHARE_EMOJI,
    TILE_FEEDBACK_CSS,
    friendly_error,
    render_board_partial,
    render_full_page,
    render_game_screen,
)
from durable_wordle.workflow import UserSessionWorkflow

_LA_TZ = ZoneInfo("America/Los_Angeles")
_TEMPORAL_UI_STATIC_ACCESS_LOG_PREFIX = "/temporal-ui/_app/immutable/"


def _today_la() -> str:
    """Return today's date in the booth timezone as an ISO ``YYYY-MM-DD`` string.

    Single source for the leaderboard's daily-rollover boundary so every caller
    agrees on which day an entry belongs to.

    :returns: ISO date string in America/Los_Angeles.
    """
    return datetime.datetime.now(_LA_TZ).strftime("%Y-%m-%d")


def _access_log_request_path(record: logging.LogRecord) -> str | None:
    """Extract the request path from uvicorn's structured access-log args."""
    if not isinstance(record.args, tuple) or len(record.args) < 3:
        return None
    request_path = record.args[2]
    if not isinstance(request_path, str):
        return None
    return request_path


class _TemporalUiStaticAccessLogFilter(logging.Filter):
    """Suppress noisy Temporal UI static asset access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return ``False`` for proxied Temporal UI immutable static chunks."""
        request_path = _access_log_request_path(record)
        if request_path is None:
            return True
        return not request_path.startswith(_TEMPORAL_UI_STATIC_ACCESS_LOG_PREFIX)


def _configure_access_log_filters() -> None:
    """Install booth-specific uvicorn access-log filters once per process."""
    access_logger = logging.getLogger("uvicorn.access")
    has_filter = any(
        isinstance(existing_filter, _TemporalUiStaticAccessLogFilter)
        for existing_filter in access_logger.filters
    )
    if not has_filter:
        access_logger.addFilter(_TemporalUiStaticAccessLogFilter())


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"
APP_MODE_CLASSIC = "classic"
APP_MODE_BOOTH = "booth"


def _normalized_app_mode(raw_mode: str | None) -> str:
    """Normalize the runtime app mode.

    :param raw_mode: Raw mode value from configuration.
    :returns: ``"classic"`` or ``"booth"``.
    """
    if raw_mode == APP_MODE_CLASSIC:
        return APP_MODE_CLASSIC
    return APP_MODE_BOOTH


def _game_mode_from_value(raw_mode: str | None) -> GameMode:
    """Parse a submitted or cookie-backed game mode.

    :param raw_mode: Raw game mode string.
    :returns: Parsed game mode, defaulting to random.
    """
    if raw_mode in {mode.value for mode in GameMode}:
        return GameMode(raw_mode)
    return GameMode.RANDOM


def _game_mode_from_request(
    request: Request,
    submitted_mode: str | None = None,
) -> GameMode:
    """Resolve the active game mode from form data or cookies.

    :param request: The incoming HTTP request.
    :param submitted_mode: Optional form-submitted game mode.
    :returns: The selected game mode.
    """
    return _game_mode_from_value(submitted_mode or request.cookies.get("game_mode"))


def get_workflow_id(
    game_id: str,
    *,
    session_id: str = "",
    game_date: str = "",
    game_mode: GameMode = GameMode.RANDOM,
) -> str:
    """Build a workflow ID from a game identifier.

    :param game_id: Unique game identifier (UUID).
    :param session_id: Browser session ID used by daily games.
    :param game_date: ISO date string used by daily games.
    :param game_mode: Selected game mode.
    :returns: A workflow ID string.
    """
    if game_mode is GameMode.DAILY:
        return f"wordle-{game_date}-{session_id or game_id}"
    if game_mode is GameMode.ABSURDLE:
        return f"wordle-absurdle-{game_id}"
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
    game_mode: GameMode = GameMode.RANDOM,
    game_date: str = "",
) -> WorkflowHandle[UserSessionWorkflow, GameState]:
    """Get an existing workflow handle or start a new workflow.

    :param client: The Temporal client.
    :param workflow_id: The workflow ID.
    :param session_id: The session ID.
    :param task_queue: The task queue for the worker.
    :param game_mode: Selected game mode.
    :param game_date: ISO date used by daily mode.
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
        WorkflowInput(
            session_id=session_id,
            game_mode=game_mode,
            game_date=game_date,
        ),
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
    app_mode: str = APP_MODE_BOOTH,
    show_booth_mode_toggle: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application.

    :param temporal_url: Address of the Temporal server.
    :param temporal_namespace: Temporal namespace to use.
    :param task_queue: Task queue for the Temporal worker.
    :param temporal_client: Optional pre-connected Temporal client (for testing).
    :param app_mode: Runtime mode, either ``"classic"`` or ``"booth"``.
    :param show_booth_mode_toggle: Whether booth start form exposes game modes.
    :returns: A configured FastAPI application instance.
    """
    _configure_access_log_filters()
    normalized_app_mode = _normalized_app_mode(app_mode)

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
    app.state.app_mode = normalized_app_mode
    app.state.show_game_mode_selector = (
        normalized_app_mode == APP_MODE_CLASSIC or show_booth_mode_toggle
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    if normalized_app_mode == APP_MODE_BOOTH:
        app.include_router(proxy_router)  # /temporal-ui/* + /api/v1/* → Temporal UI
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _share_context(
        request: Request, game_state: GameState | None
    ) -> dict[str, object]:
        guesses = game_state.guesses if game_state else []
        elapsed_seconds = 0
        if game_state and game_state.started_at:
            started_at = game_state.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=datetime.UTC)
            now = datetime.datetime.now(datetime.UTC)
            elapsed_seconds = max(0, int((now - started_at).total_seconds()))

        emoji_grid = "\n".join(
            "".join(SHARE_EMOJI[feedback.value] for feedback in guess.feedback)
            for guess in guesses
        )

        return {
            "request": request,
            "guesses": guesses,
            "tile_feedback_css": TILE_FEEDBACK_CSS,
            "player_name": request.cookies.get("player_name", ""),
            "guess_count": len(guesses),
            "elapsed_formatted": format_elapsed(elapsed_seconds),
            "won": bool(game_state and game_state.status == "won"),
            "emoji_grid": emoji_grid,
        }

    def _render_share_screen(
        request: Request,
        session_id: str,
        is_new_session: bool,
        game_state: GameState | None,
        *,
        retarget_screen: bool = False,
    ) -> HTMLResponse:
        response = templates.TemplateResponse(
            request=request,
            name="_share_screen.html",
            context=_share_context(request, game_state),
        )
        if retarget_screen:
            response.headers["HX-Retarget"] = "#screen"
            response.headers["HX-Reswap"] = "innerHTML"
        if is_new_session:
            response.set_cookie(key="session_id", value=session_id, httponly=True)
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Return application health status.

        :returns: A dict with ``status`` key.
        """
        return {"status": "ok"}

    @app.get("/new-game")
    async def new_game(request: Request) -> Response:
        """Start a new random game by clearing cookies and redirecting or swapping.

        Sets a fresh game_id and clears the session_id so the player
        gets a completely new workflow.

        :param request: The incoming HTTP request.
        :returns: A redirect or start-screen fragment with fresh cookies.
        """
        response: Response
        if request.headers.get("HX-Request") == "true":
            if normalized_app_mode == APP_MODE_CLASSIC:
                response = render_game_screen(
                    templates,
                    request,
                    session_id=str(uuid.uuid4()),
                    is_new_session=True,
                    game_state=GameState(target_word=""),
                )
            else:
                response = templates.TemplateResponse(
                    request=request,
                    name="_start_screen.html",
                    context={
                        "show_game_mode_selector": app.state.show_game_mode_selector,
                    },
                )
            response.headers["HX-Push-Url"] = "/"
        else:
            response = RedirectResponse(url="/", status_code=302)

        new_game_id = str(uuid.uuid4())
        response.set_cookie(key="game_id", value=new_game_id, httponly=True)
        response.delete_cookie(key="game_mode")
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
        game_mode = _game_mode_from_request(request)
        if game_id:
            workflow_id = get_workflow_id(
                game_id,
                session_id=session_id,
                game_date=_today_la(),
                game_mode=game_mode,
            )
            game_state = await _query_existing_game(client, workflow_id)
        if normalized_app_mode == APP_MODE_CLASSIC and game_state is None:
            game_state = GameState(target_word="", game_mode=game_mode)

        return render_full_page(
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
        game_mode: str | None = Form(default=None),
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
        :param game_mode: Optional selected Wordle mode.
        :returns: Rendered game screen HTML fragment.
        """
        session_id, is_new_session, game_id = _session_from_request(request)

        client: Client = app.state.temporal_client
        queue: str = app.state.task_queue

        if not game_id:
            game_id = str(uuid.uuid4())

        selected_game_mode = _game_mode_from_request(request, game_mode)
        workflow_id = get_workflow_id(
            game_id,
            session_id=session_id,
            game_date=_today_la(),
            game_mode=selected_game_mode,
        )
        handle = await _get_or_start_workflow(
            client,
            workflow_id,
            session_id,
            queue,
            game_mode=selected_game_mode,
            game_date=_today_la(),
        )
        game_state = await _wait_for_game_state(handle)

        # Enforce a single running game: terminate any abandoned workflows so the
        # display tracks only the current game.
        await _terminate_other_running_games(client, workflow_id)

        response = render_game_screen(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
        )
        response.set_cookie(key="game_id", value=game_id, httponly=True)
        response.set_cookie(
            key="game_mode", value=selected_game_mode.value, httponly=True
        )

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

        # Record everyone who plays (win or lose) for post-event email outreach —
        # not just winners who land on the leaderboard. No-op when email is blank.
        record_participant(
            player_name=player_name or "Anonymous",
            email=(email or "").strip(),
            madlib_noun=(madlib_noun or "").strip().upper(),
            madlib_verb=(madlib_verb or "").strip().upper(),
            game_date=_today_la(),
        )
        return response

    @app.post("/guess", response_class=HTMLResponse)
    async def submit_guess(
        request: Request,
        guess: str = Form(...),
        game_mode: str | None = Form(default=None),
    ) -> HTMLResponse:
        """Process a guess submission.

        Sends the guess as a workflow Update and returns the updated board
        partial for HTMX to swap into #game-content.

        :param request: The incoming HTTP request.
        :param guess: The guessed word from the form.
        :param game_mode: Optional selected Wordle mode for a new game.
        :returns: Rendered board partial HTML fragment.
        """
        session_id, is_new_session, game_id = _session_from_request(request)

        client: Client = app.state.temporal_client
        queue: str = app.state.task_queue

        if not game_id:
            game_id = str(uuid.uuid4())

        selected_game_mode = _game_mode_from_request(request, game_mode)
        workflow_id = get_workflow_id(
            game_id,
            session_id=session_id,
            game_date=_today_la(),
            game_mode=selected_game_mode,
        )
        handle = await _get_or_start_workflow(
            client,
            workflow_id,
            session_id,
            queue,
            game_mode=selected_game_mode,
            game_date=_today_la(),
        )

        # Send guess via Update
        error_message = ""
        try:
            await handle.execute_update(
                UserSessionWorkflow.make_guess,
                MakeGuessInput(guess=guess.strip().upper()),
            )
        except RPCError as rpc_err:
            error_message = friendly_error(str(rpc_err))
        except Exception as update_err:
            cause = update_err.__cause__ or update_err
            error_message = friendly_error(str(cause))

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

        # Auto-save the leaderboard entry the moment the game is won — this is the
        # only successful update that yields a "won" status, so it fires exactly
        # once (a replayed guess on a finished game errors out above and returns
        # early). Replaces the old manual "POST TO LEADERBOARD" button.
        if not error_message and game_state.status == "won":
            lb_add_entry(
                player_name=request.cookies.get("player_name", "Anonymous"),
                email=request.cookies.get("email", ""),
                guesses=len(game_state.guesses),
                started_at=game_state.started_at,
                madlib_noun=request.cookies.get("madlib_noun", ""),
                madlib_verb=request.cookies.get("madlib_verb", ""),
                game_date=_today_la(),
            )

        response = render_board_partial(
            templates,
            request,
            session_id,
            is_new_session,
            game_state=game_state,
            error_message=error_message,
            animate=is_htmx,
            auto_share_after_reveal=is_htmx and game_state.status == "won",
        )
        response.set_cookie(key="game_id", value=game_id, httponly=True)
        response.set_cookie(
            key="game_mode", value=selected_game_mode.value, httponly=True
        )
        return response

    def _leaderboard_context(request: Request) -> dict[str, object]:
        today = _today_la()
        entries = get_top_entries_for_date(today)
        madlibs = get_madlib_pairs(entries)
        return {
            "request": request,
            "entries": entries,
            "madlibs_json": json.dumps(madlibs),
            "game_date": today,
        }

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
        game_mode = _game_mode_from_request(request)
        if game_id:
            workflow_id = get_workflow_id(
                game_id,
                session_id=session_id,
                game_date=_today_la(),
                game_mode=game_mode,
            )
            game_state = await _query_existing_game(client, workflow_id)
        if game_state is None:
            return HTMLResponse(content="", status_code=204)
        return render_board_partial(
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
        game_mode = _game_mode_from_request(request)
        if game_id:
            workflow_id = get_workflow_id(
                game_id,
                session_id=session_id,
                game_date=_today_la(),
                game_mode=game_mode,
            )
            game_state = await _query_existing_game(client, workflow_id)

        return _render_share_screen(
            request,
            session_id,
            is_new_session,
            game_state,
        )

    def _leaderboard_payload(game_date: str) -> dict[str, object]:
        """Build the JSON leaderboard payload for the display.

        :param game_date: Booth-local ISO date string.
        :returns: Leaderboard entries and madlib pairs.
        """
        entries = get_top_entries_for_date(game_date)
        return {
            "game_date": game_date,
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

    def _recent_win_payload(game_date: str) -> dict[str, object] | None:
        """Build the fresh-win payload for the display.

        :param game_date: Booth-local ISO date string.
        :returns: Win payload, or ``None`` when there is no fresh win.
        """
        result = get_recent_win(game_date)
        if result is None:
            return None
        entry, rank = result
        return {
            "player_name": entry.player_name,
            "guesses": entry.guesses,
            "elapsed_formatted": entry.elapsed_formatted,
            "rank": rank,
            "submitted_at": entry.submitted_at,
        }

    async def _active_game_payload(client: Client) -> dict[str, str | None]:
        """Return the most recently started running game workflow, if any.

        Orders running ``UserSessionWorkflow`` executions by start time so the
        display always tracks the newest game even if stale workflows linger.
        Returns ``null`` values when no game is active.

        :param client: The Temporal client.
        :returns: Dict with ``workflow_id`` and ``run_id``, or nulls if idle.
        """
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

    async def _recent_loss_payload(client: Client) -> dict[str, object] | None:
        """Return the most recently closed reveal-worthy game.

        Losses and inactivity timeouts are not stored in SQLite, so the display
        reads them from the completed workflow state.

        :param client: The Temporal client.
        :returns: Loss/timeout payload, or ``None`` when no recent reveal exists.
        """
        query = 'WorkflowType="UserSessionWorkflow" AND ExecutionStatus="Completed"'
        try:
            completed = [execution async for execution in client.list_workflows(query)]
        except Exception:
            return None
        newest_first = sorted(
            completed,
            key=lambda execution: (
                execution.close_time
                or execution.start_time
                or datetime.datetime.min.replace(tzinfo=datetime.UTC)
            ),
            reverse=True,
        )
        for execution in newest_first[:10]:
            try:
                game_state = await client.get_workflow_handle(execution.id).query(
                    UserSessionWorkflow.get_game_state
                )
            except RPCError:
                continue
            if game_state.status not in ("lost", "abandoned"):
                continue
            return {
                "workflow_id": execution.id,
                "run_id": execution.run_id,
                "target_word": game_state.target_word,
                "guesses": len(game_state.guesses),
                "status": game_state.status,
            }
        return None

    @app.get("/api/leaderboard")
    async def api_leaderboard(request: Request) -> dict[str, object]:
        """Return today's leaderboard as JSON for the live-updating display.

        :param request: The incoming HTTP request.
        :returns: Dict with ``game_date``, ``entries`` (rank/name/guesses/time),
            and deduplicated ``madlibs`` pairs.
        """
        return _leaderboard_payload(_today_la())

    @app.get("/api/last-win")
    async def last_win(request: Request) -> dict[str, object]:
        """Return the most recent winning entry within the last few seconds.

        The display polls this to fire a one-off win celebration. Returns
        ``{"win": null}`` when there is no fresh win; otherwise the entry's
        name, guess count, formatted time, day rank, and ``submitted_at``
        (used client-side to dedupe so each win celebrates exactly once).

        :param request: The incoming HTTP request.
        :returns: Dict with a ``win`` object, or ``{"win": null}`` when idle.
        """
        return {"win": _recent_win_payload(_today_la())}

    @app.get("/api/active-game")
    async def active_game(request: Request) -> dict[str, str | None]:
        """Return the most recently started running game workflow, if any.

        :param request: The incoming HTTP request.
        :returns: Dict with ``workflow_id`` and ``run_id``, or nulls if idle.
        """
        client: Client = request.app.state.temporal_client
        return await _active_game_payload(client)

    @app.get("/api/display-state")
    async def display_state(request: Request) -> dict[str, object]:
        """Return all display data in one polling response.

        This keeps the second-screen booth display from issuing separate
        active-game, leaderboard, and recent-win requests on every interval.

        :param request: The incoming HTTP request.
        :returns: Active game, leaderboard, and fresh-win payloads.
        """
        client: Client = request.app.state.temporal_client
        today = _today_la()
        active_game_payload = await _active_game_payload(client)
        loss_payload = None
        if active_game_payload["workflow_id"] is None:
            loss_payload = await _recent_loss_payload(client)
        return {
            "active_game": active_game_payload,
            "leaderboard": _leaderboard_payload(today),
            "win": _recent_win_payload(today),
            "loss": loss_payload,
        }

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
            name="booth/display.html",
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

    temporal_address = os.environ.get("TEMPORAL_ADDRESS")
    temporal_namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    app_mode = _normalized_app_mode(os.environ.get("DURABLE_WORDLE_APP_MODE"))
    show_booth_mode_toggle = os.environ.get(
        "DURABLE_WORDLE_SHOW_MODE_TOGGLE", ""
    ).lower() in {"1", "true", "yes", "on"}
    if temporal_address:
        return create_app(
            temporal_url=temporal_address,
            temporal_namespace=temporal_namespace,
            task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "wordle-tasks"),
            app_mode=app_mode,
            show_booth_mode_toggle=show_booth_mode_toggle,
        )

    config_file = Path(__file__).resolve().parent.parent.parent / "temporal.toml"
    profile = ClientConfigProfile.load(config_source=config_file)
    connect_config = profile.to_client_connect_config()
    return create_app(
        temporal_url=connect_config.get("target_host", "localhost:7233"),
        temporal_namespace=connect_config.get("namespace", "default"),
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "wordle-tasks"),
        app_mode=app_mode,
        show_booth_mode_toggle=show_booth_mode_toggle,
    )
