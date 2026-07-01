# ABOUTME: Core data models for Durable Wordle including letter feedback,
# guess results, and game state used by workflows and API layers.
import enum
from dataclasses import dataclass, field
from datetime import datetime

WORD_LENGTH: int = 5


class LetterFeedback(enum.StrEnum):
    """Feedback for a single letter in a guess.

    :cvar CORRECT: Letter is in the correct position (green).
    :cvar PRESENT: Letter is in the word but wrong position (yellow).
    :cvar ABSENT: Letter is not in the word (gray).
    """

    CORRECT = "correct"
    PRESENT = "present"
    ABSENT = "absent"


class GameMode(enum.StrEnum):
    """Available game modes.

    :cvar DAILY: Deterministic word based on the game date.
    :cvar RANDOM: Random word selected at workflow start.
    :cvar ABSURDLE: Adversarial mode that chooses feedback per guess.
    """

    DAILY = "daily"
    RANDOM = "random"
    ABSURDLE = "absurdle"


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
    :param status: Current game status — ``"playing"``, ``"won"``, ``"lost"``,
        or ``"abandoned"`` (closed after an inactivity timeout).
    """

    target_word: str
    guesses: list[GuessResult] = field(default_factory=list)
    max_guesses: int = 6
    status: str = "playing"
    started_at: datetime | None = None
    game_mode: GameMode = GameMode.RANDOM
    remaining_candidates: list[str] = field(default_factory=list)

    @property
    def is_game_over(self) -> bool:
        """Check whether the game has ended.

        :returns: ``True`` if the game was won, lost, or abandoned.
        """
        return self.status in ("won", "lost", "abandoned")


@dataclass
class WorkflowInput:
    """Input for starting a new Wordle game session workflow.

    :param session_id: Unique session identifier for this game.
    :param game_mode: Word selection and feedback mode.
    :param game_date: ISO-format date used by daily mode.
    :param inactivity_timeout_seconds: Optional inactivity timeout override,
        used by tests and previews. ``None`` uses the production default.
    """

    session_id: str
    game_mode: GameMode = GameMode.RANDOM
    game_date: str = ""
    inactivity_timeout_seconds: float | None = None


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


@dataclass
class AbsurdleFeedbackInput:
    """Input for the Absurdle feedback activity.

    :param guess: The guessed word (uppercase).
    :param candidates: Candidate words still consistent with prior feedback.
    """

    guess: str
    candidates: list[str]


@dataclass
class AbsurdleFeedbackResult:
    """Result of an Absurdle feedback choice.

    :param feedback: Per-letter feedback selected for this guess.
    :param candidates: Remaining candidate words after applying feedback.
    :param reveal_word: Deterministic word to reveal if the player loses.
    """

    feedback: list[LetterFeedback]
    candidates: list[str]
    reveal_word: str
