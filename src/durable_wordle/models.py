# ABOUTME: Core data models for Durable Wordle including letter feedback,
# guess results, and game state used by workflows and API layers.
import enum
from dataclasses import dataclass, field


class LetterFeedback(enum.StrEnum):
    """Feedback for a single letter in a guess.

    :cvar CORRECT: Letter is in the correct position (green).
    :cvar PRESENT: Letter is in the word but wrong position (yellow).
    :cvar ABSENT: Letter is not in the word (gray).
    """

    CORRECT = "correct"
    PRESENT = "present"
    ABSENT = "absent"


@dataclass
class GuessResult:
    """Result of a single guess attempt.

    :param word: The guessed word (uppercase).
    :param feedback: Per-letter feedback indicating correctness.
    """

    word: str
    feedback: list[LetterFeedback]


@dataclass
class GameState:
    """Current state of a Wordle game session.

    :param target_word: The word the player is trying to guess (uppercase).
    :param guesses: List of guess results submitted so far.
    :param max_guesses: Maximum number of guesses allowed.
    :param status: Current game status — ``"playing"``, ``"won"``, or ``"lost"``.
    """

    target_word: str
    guesses: list[GuessResult] = field(default_factory=list)
    max_guesses: int = 6
    status: str = "playing"

    @property
    def is_game_over(self) -> bool:
        """Check whether the game has ended.

        :returns: ``True`` if the game status is ``"won"`` or ``"lost"``.
        """
        return self.status in ("won", "lost")


@dataclass
class WorkflowInput:
    """Input for starting a new Wordle game session workflow.

    :param session_id: Unique session identifier for this game.
    :param random_mode: If True, pick a random word instead of the daily word.
    """

    session_id: str
    random_mode: bool = False


@dataclass
class MakeGuessInput:
    """Input for the make_guess update handler.

    :param guess: The 5-letter word being guessed.
    """

    guess: str


@dataclass
class ValidateGuessInput:
    """Input for the validate_guess activity.

    :param guess: The word to validate.
    """

    guess: str


@dataclass
class SelectWordInput:
    """Input for the select_word activity.

    :param game_date: ISO-format date string for daily word. If empty,
        a random word is selected instead.
    """

    game_date: str = ""


@dataclass
class CalculateFeedbackInput:
    """Input for the calculate_feedback activity.

    :param guess: The guessed word (uppercase).
    :param target: The target word (uppercase).
    """

    guess: str
    target: str
