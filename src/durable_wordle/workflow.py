# ABOUTME: Temporal workflow for a single Wordle game session. Holds game state,
# accepts guesses via Update, exposes state via Query, completes when game ends.
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from durable_wordle.activities import (
        calculate_feedback,
        select_word,
        validate_guess,
    )
    from durable_wordle.models import (
        CalculateFeedbackInput,
        GameState,
        GuessResult,
        LetterFeedback,
        MakeGuessInput,
        SelectWordInput,
        ValidateGuessInput,
        WorkflowInput,
    )


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

        Selects the target word — either the daily word via activity
        or a random word using Temporal's deterministic RNG — then
        waits for guesses until the player wins or exhausts all attempts.

        :param workflow_input: Contains session ID and game mode.
        :returns: The final game state when the game is over.
        """
        game_date = (
            "" if workflow_input.random_mode else workflow.now().date().isoformat()
        )
        mode_label = "random" if workflow_input.random_mode else f"daily ({game_date})"
        workflow.logger.info("Selecting word: %s", mode_label)
        target_word = await workflow.execute_activity(
            select_word,
            SelectWordInput(game_date=game_date),
            start_to_close_timeout=timedelta(seconds=10),
        )
        self._game_state = GameState(target_word=target_word)
        workflow.logger.info(
            "Game initialized (session=%s, mode=%s)",
            workflow_input.session_id,
            "random" if workflow_input.random_mode else "daily",
        )

        await workflow.wait_condition(lambda: self._state.is_game_over)

        # Ensure all in-flight update handlers finish before completing
        await workflow.wait_condition(workflow.all_handlers_finished)

        workflow.logger.info(
            "Game completed: %s in %d guesses",
            self._state.status,
            len(self._state.guesses),
        )
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
        # Wait for the select_daily_word activity to finish initializing state
        await workflow.wait_condition(lambda: self._game_state is not None)

        normalized_guess = guess_input.guess.strip().upper()
        guess_number = len(self._state.guesses) + 1
        workflow.logger.info(
            "Guess %d/%d: %s",
            guess_number,
            self._state.max_guesses,
            normalized_guess,
        )

        # Execute validate_guess activity to check word list
        is_valid = await workflow.execute_activity(
            validate_guess,
            ValidateGuessInput(guess=normalized_guess),
            start_to_close_timeout=timedelta(seconds=10),
        )
        if not is_valid:
            workflow.logger.warning("Rejected invalid word: %s", normalized_guess)
            raise ApplicationError(
                f"'{normalized_guess}' is not a valid word",
                type="InvalidWord",
            )

        # Calculate letter feedback via activity for event history visibility
        feedback = await workflow.execute_activity(
            calculate_feedback,
            CalculateFeedbackInput(
                guess=normalized_guess, target=self._state.target_word
            ),
            start_to_close_timeout=timedelta(seconds=10),
        )

        # Build result and update state
        guess_result = GuessResult(word=normalized_guess, feedback=feedback)
        self._state.guesses.append(guess_result)

        feedback_summary = "".join(fb.value[0].upper() for fb in feedback)
        workflow.logger.info("Feedback for %s: %s", normalized_guess, feedback_summary)

        # Check win condition
        if all(letter == LetterFeedback.CORRECT for letter in feedback):
            self._state.status = "won"
            workflow.logger.info("Player won on guess %d!", guess_number)

        # Check loss condition
        elif len(self._state.guesses) >= self._state.max_guesses:
            self._state.status = "lost"
            workflow.logger.info("Player lost — word was %s", self._state.target_word)

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
