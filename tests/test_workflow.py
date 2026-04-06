# ABOUTME: Tests for UserSessionWorkflow covering game lifecycle — initial state,
# valid/invalid guesses, win/loss conditions, and post-game rejection.
import concurrent.futures
import uuid

import pytest
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from durable_wordle.activities import (
    calculate_feedback,
    select_daily_word,
    validate_guess,
)
from durable_wordle.models import LetterFeedback, MakeGuessInput, WorkflowInput
from durable_wordle.word_lists import ANSWER_LIST
from durable_wordle.workflow import UserSessionWorkflow

# Valid 5-letter words that are NOT in the answer list (guaranteed wrong)
WRONG_GUESSES = ["ABOVE", "ABUSE", "ACTOR", "ADMIT", "ADOPT", "ADULT"]


@pytest.fixture()
def task_queue() -> str:
    """Generate a unique task queue name per test."""
    return str(uuid.uuid4())


class TestUserSessionWorkflow:
    """Tests for the UserSessionWorkflow game lifecycle."""

    async def test_first_guess_initializes_game(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """After first guess, query should return playing state."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session", random_mode=True),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # The update's wait_condition ensures the workflow has
                # finished initializing before processing the guess
                await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=WRONG_GUESSES[0]),
                )

                state = await handle.query(UserSessionWorkflow.get_game_state)
                assert state.status == "playing"
                assert len(state.guesses) == 1
                assert state.max_guesses == 6
                assert state.target_word in ANSWER_LIST

    async def test_valid_guess_returns_feedback(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A valid guess should return a GuessResult with feedback."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session", random_mode=True),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                result = await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=WRONG_GUESSES[0]),
                )
                assert result.word == WRONG_GUESSES[0]
                assert len(result.feedback) == 5

    async def test_invalid_word_rejected(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A word not in the dictionary should be rejected."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session", random_mode=True),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                with pytest.raises(WorkflowUpdateFailedError):
                    await handle.execute_update(
                        UserSessionWorkflow.make_guess,
                        MakeGuessInput(guess="ZZZZZ"),
                    )

    async def test_wrong_length_rejected_by_validator(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A guess that isn't 5 letters should be rejected."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session", random_mode=True),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                with pytest.raises(WorkflowUpdateFailedError):
                    await handle.execute_update(
                        UserSessionWorkflow.make_guess,
                        MakeGuessInput(guess="HI"),
                    )

    async def test_correct_guess_wins_game(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Guessing the target word sets status to 'won' and completes."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session", random_mode=True),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # Make a wrong guess first to discover the target word
                await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=WRONG_GUESSES[0]),
                )
                state = await handle.query(UserSessionWorkflow.get_game_state)
                target = state.target_word

                # Now guess the correct word
                result = await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=target),
                )
                assert result.word == target
                assert all(fb == LetterFeedback.CORRECT for fb in result.feedback)

                final_state = await handle.result()
                assert final_state.status == "won"
                assert len(final_state.guesses) == 2

    async def test_six_wrong_guesses_loses_game(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Using all 6 guesses without winning sets status to 'lost'."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session", random_mode=True),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # Discover the target so we can avoid it
                await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=WRONG_GUESSES[0]),
                )
                state = await handle.query(UserSessionWorkflow.get_game_state)
                target = state.target_word

                # Use remaining 5 guesses with words != target
                remaining_wrong = [
                    word for word in WRONG_GUESSES[1:] if word != target
                ][:5]
                for guess_word in remaining_wrong:
                    await handle.execute_update(
                        UserSessionWorkflow.make_guess,
                        MakeGuessInput(guess=guess_word),
                    )

                final_state = await handle.result()
                assert final_state.status == "lost"
                assert len(final_state.guesses) == 6

    async def test_guess_after_game_over_rejected(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Submitting a guess after the game ends should be rejected."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session", random_mode=True),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # Discover target and win
                await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=WRONG_GUESSES[0]),
                )
                state = await handle.query(UserSessionWorkflow.get_game_state)
                await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=state.target_word),
                )
                await handle.result()

                # Now try to guess again — should fail
                with pytest.raises(
                    (
                        WorkflowUpdateFailedError,
                        WorkflowFailureError,
                        RPCError,
                    )
                ):
                    await handle.execute_update(
                        UserSessionWorkflow.make_guess,
                        MakeGuessInput(guess=WRONG_GUESSES[1]),
                    )

    async def test_daily_mode_uses_activity(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Daily mode should select word via the select_daily_word activity."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[calculate_feedback, select_daily_word, validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # Make a guess to ensure initialization completes
                await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess=WRONG_GUESSES[0]),
                )

                state = await handle.query(UserSessionWorkflow.get_game_state)
                assert state.target_word in ANSWER_LIST
                assert state.status == "playing"
