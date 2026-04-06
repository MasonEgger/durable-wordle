# ABOUTME: Temporal workflow for a single Wordle game session. Holds game state,
# accepts guesses via Update, exposes state via Query, completes when game ends.
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from durable_wordle.activities import ValidateGuessInput, validate_guess
    from durable_wordle.game_logic import calculate_feedback
    from durable_wordle.models import GameState, GuessResult, LetterFeedback


@dataclass
class WorkflowInput:
    """Input for starting a new Wordle game session workflow.

    :param target_word: The word the player must guess (uppercase).
    :param session_id: Unique session identifier for this game.
    """

    target_word: str
    session_id: str


@dataclass
class MakeGuessInput:
    """Input for the make_guess update handler.

    :param guess: The 5-letter word being guessed.
    """

    guess: str


@workflow.defn
class UserSessionWorkflow:
    """Temporal workflow managing a single Wordle game session.

    The workflow holds game state, accepts guesses via an Update handler,
    exposes the current board via a Query handler, and completes when
    the player wins or exhausts all guesses.
    """

    def __init__(self) -> None:
        self._game_state: GameState | None = None

    @property
    def _state(self) -> GameState:
        """Return game state, asserting it has been initialized.

        :returns: The current game state.
        """
        assert self._game_state is not None
        return self._game_state

    @workflow.run
    async def run(self, workflow_input: WorkflowInput) -> GameState:
        """Run the game session until completion.

        :param workflow_input: Contains the target word and session ID.
        :returns: The final game state when the game is over.
        """
        self._game_state = GameState(target_word=workflow_input.target_word.upper())

        await workflow.wait_condition(lambda: self._state.is_game_over)

        # Ensure all in-flight update handlers finish before completing
        await workflow.wait_condition(workflow.all_handlers_finished)

        return self._state

    @workflow.update
    async def make_guess(self, guess_input: MakeGuessInput) -> GuessResult:
        """Process a guess attempt and return feedback.

        Validates the guess format, checks the word list via activity,
        calculates letter feedback, and updates game state.

        :param guess_input: Contains the guessed word.
        :returns: The guess result with per-letter feedback.
        :raises ApplicationError: If the game is over, guess is invalid format,
            or word is not in the dictionary.
        """
        normalized_guess = guess_input.guess.strip().upper()

        # Execute validate_guess activity to check word list
        is_valid = await workflow.execute_activity(
            validate_guess,
            ValidateGuessInput(guess=normalized_guess),
            start_to_close_timeout=timedelta(seconds=10),
        )
        if not is_valid:
            raise ApplicationError(
                f"'{normalized_guess}' is not a valid word",
                type="InvalidWord",
            )

        # Calculate letter feedback
        feedback = calculate_feedback(normalized_guess, self._state.target_word)

        # Build result and update state
        guess_result = GuessResult(word=normalized_guess, feedback=feedback)
        self._state.guesses.append(guess_result)

        # Check win condition
        if all(letter == LetterFeedback.CORRECT for letter in feedback):
            self._state.status = "won"

        # Check loss condition
        elif len(self._state.guesses) >= self._state.max_guesses:
            self._state.status = "lost"

        return guess_result

    @make_guess.validator
    def validate_make_guess(self, guess_input: MakeGuessInput) -> None:
        """Validate a guess before processing.

        Rejects guesses when the game is over or the guess has invalid format.
        Must not mutate state or block.

        :param guess_input: Contains the guessed word.
        :raises ApplicationError: If the game is over or guess format is invalid.
        """
        if self._game_state is not None and self._state.is_game_over:
            raise ApplicationError(
                "Game is already over",
                type="GameOver",
            )

        normalized_guess = guess_input.guess.strip().upper()
        if len(normalized_guess) != 5:
            raise ApplicationError(
                f"Guess must be exactly 5 letters, got {len(normalized_guess)}",
                type="InvalidFormat",
            )
        if not normalized_guess.isalpha():
            raise ApplicationError(
                "Guess must contain only letters",
                type="InvalidFormat",
            )

    @workflow.query
    def get_game_state(self) -> GameState:
        """Return the current game state for rendering.

        :returns: The current game state including guesses and status.
        """
        return self._state
