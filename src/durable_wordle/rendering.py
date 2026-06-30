# ABOUTME: View layer for the game UI — Jinja context building, board/screen
# ABOUTME: render helpers, keyboard-state, and friendly error text.
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from durable_wordle.models import GameState, GuessResult, LetterFeedback

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
        "keyboard_state": build_keyboard_state(guesses),
        "tile_feedback_css": TILE_FEEDBACK_CSS,
        "has_started": len(guesses) > 0,
        "animate": animate,
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
        context: dict[str, Any] = {
            "request": request,
            "screen_state": "start",
            "current_screen_template": "_start_screen.html",
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
    context = game_context(
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


def render_board_partial(
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
    context = game_context(
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
