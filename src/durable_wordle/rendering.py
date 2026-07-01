# ABOUTME: View layer for the game UI — Jinja context building, board/screen
# ABOUTME: render helpers, keyboard-state, and friendly error text.
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from durable_wordle.models import GameMode, GameState, GuessResult, LetterFeedback

KEYBOARD_ROWS: list[list[str]] = [
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
    ["Z", "X", "C", "V", "B", "N", "M"],
]

# CSS classes for board tiles keyed by LetterFeedback.value — single source of
# truth for both the template (tile rendering) and the keyboard state builder.
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


def build_keyboard_state(guesses: list[GuessResult]) -> dict[str, str]:
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


def build_keyboard_transition_indices(guesses: list[GuessResult]) -> dict[str, int]:
    """Map changed keyboard letters to the latest-row tile that reveals them.

    A single keyboard key can correspond to multiple tiles in a duplicate-letter
    guess. The key should flip when the tile responsible for the new best
    keyboard state flips, not necessarily at the first matching letter.

    :param guesses: The list of guess results so far.
    :returns: A dict mapping uppercase letters to latest-row tile indexes.
    """
    if not guesses:
        return {}

    prior_state = build_keyboard_state(guesses[:-1])
    final_state = build_keyboard_state(guesses)
    latest_guess = guesses[-1]
    transition_indices: dict[str, int] = {}

    for letter_index, (letter, letter_feedback) in enumerate(
        zip(latest_guess.word, latest_guess.feedback)
    ):
        final_css = final_state.get(letter)
        if not final_css or final_css == prior_state.get(letter, "bg-wordle-key"):
            continue

        tile_css = _KEY_FEEDBACK_CSS[letter_feedback]
        if tile_css == final_css and letter not in transition_indices:
            transition_indices[letter] = letter_index

    return transition_indices


def friendly_error(raw_error: str) -> str:
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


def game_context(
    request: Request,
    game_state: GameState | None,
    error_message: str = "",
    status_message: str = "",
    animate: bool = False,
    auto_share_after_reveal: bool = False,
) -> dict[str, object]:
    """Build the Jinja2 context dict for game-screen and board-partial templates.

    :param request: The incoming HTTP request.
    :param game_state: Current game state, or None for an empty board.
    :param error_message: Optional error message to display.
    :param status_message: Optional status message to display.
    :param animate: If True, apply tile-flip animation to the latest guess row.
    :param auto_share_after_reveal: If True, open the share screen after the
        latest animated row finishes revealing.
    :returns: Template context dict.
    """
    guesses = game_state.guesses if game_state else []
    status = game_state.status if game_state else "playing"
    app_mode = getattr(request.app.state, "app_mode", "")
    show_game_timers = app_mode != "classic"
    selected_game_mode = game_state.game_mode if game_state else GameMode.RANDOM
    show_game_mode_selector = (
        bool(getattr(request.app.state, "show_game_mode_selector", False))
        and status == "playing"
        and not guesses
    )

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
        "keyboard_state": build_keyboard_state(guesses),
        # Keyboard colours BEFORE the latest guess, so animated swaps can hold the
        # prior colour and flip changed keys in sync with their tiles.
        "keyboard_state_prior": build_keyboard_state(guesses[:-1]) if guesses else {},
        "keyboard_transition_indices": build_keyboard_transition_indices(guesses),
        "tile_feedback_css": TILE_FEEDBACK_CSS,
        "has_started": len(guesses) > 0,
        "game_modes": list(GameMode),
        "selected_game_mode": selected_game_mode,
        "show_game_mode_selector": show_game_mode_selector,
        "show_game_timers": show_game_timers,
        "animate": animate,
        "auto_share_after_reveal": auto_share_after_reveal,
        "started_at_ts": started_at_ts,
    }


def render_full_page(
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
        context: dict[str, object] = {
            "request": request,
            "screen_state": "start",
            "current_screen_template": "_start_screen.html",
            "show_game_mode_selector": bool(
                getattr(request.app.state, "show_game_mode_selector", False)
            ),
        }
    else:
        context = {
            **game_context(request, game_state),
            "screen_state": "game",
            "current_screen_template": "_game_screen.html",
        }

    response = templates.TemplateResponse(
        request=request, name="index.html", context=context
    )
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response


def render_game_screen(
    templates: Jinja2Templates,
    request: Request,
    session_id: str,
    is_new_session: bool,
    game_state: GameState,
    error_message: str = "",
    animate: bool = False,
    auto_share_after_reveal: bool = False,
) -> HTMLResponse:
    """Render just the game-screen fragment for HTMX swaps into #screen.

    :param templates: Jinja2 template engine.
    :param request: The incoming HTTP request.
    :param session_id: The player's session ID.
    :param is_new_session: Whether to set the session cookie on the response.
    :param game_state: Current game state.
    :param error_message: Optional error message to display.
    :param animate: If True, apply tile-flip animation to the latest guess row.
    :param auto_share_after_reveal: If True, open the share screen after the
        latest animated row finishes revealing.
    :returns: Rendered HTML fragment response.
    """
    context = game_context(
        request,
        game_state,
        error_message=error_message,
        animate=animate,
        auto_share_after_reveal=auto_share_after_reveal,
    )
    response = templates.TemplateResponse(
        request=request, name="_game_screen.html", context=context
    )
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response


def render_board_partial(
    templates: Jinja2Templates,
    request: Request,
    session_id: str,
    is_new_session: bool,
    game_state: GameState,
    error_message: str = "",
    animate: bool = False,
    auto_share_after_reveal: bool = False,
) -> HTMLResponse:
    """Render just the board partial for HTMX swaps into #game-content.

    :param templates: Jinja2 template engine.
    :param request: The incoming HTTP request.
    :param session_id: The player's session ID.
    :param is_new_session: Whether to set the session cookie on the response.
    :param game_state: Current game state.
    :param error_message: Optional error message to display.
    :param animate: If True, apply tile-flip animation to the latest guess row.
    :param auto_share_after_reveal: If True, open the share screen after the
        latest animated row finishes revealing.
    :returns: Rendered HTML fragment response.
    """
    context = game_context(
        request,
        game_state,
        error_message=error_message,
        animate=animate,
        auto_share_after_reveal=auto_share_after_reveal,
    )
    response = templates.TemplateResponse(
        request=request, name="_board_partial.html", context=context
    )
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response
