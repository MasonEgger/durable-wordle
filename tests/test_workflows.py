# ABOUTME: Tests for UserSessionWorkflow covering game lifecycle — initial state,
# valid/invalid guesses, win/loss conditions, and post-game rejection.
import concurrent.futures
import uuid

import pytest
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from durable_wordle.activities import validate_guess
from durable_wordle.models import LetterFeedback

# Import workflow types through the sandbox pass-through pattern
from durable_wordle.workflows import (
    MakeGuessInput,
    UserSessionWorkflow,
    WorkflowInput,
)


@pytest.fixture()
def task_queue() -> str:
    """Generate a unique task queue name per test."""
    return str(uuid.uuid4())


class TestUserSessionWorkflow:
    """Tests for the UserSessionWorkflow game lifecycle."""

    async def test_initial_game_state_is_empty(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Starting a workflow and querying should return empty playing state."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(target_word="ABOUT", session_id="test-session"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                state = await handle.query(UserSessionWorkflow.get_game_state)
                assert state.status == "playing"
                assert state.guesses == []
                assert state.max_guesses == 6
                # target_word should not be exposed via query (but GameState has it)
                assert state.target_word == "ABOUT"

    async def test_valid_guess_returns_feedback(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A valid guess should return a GuessResult with correct feedback."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(target_word="ABOUT", session_id="test-session"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                result = await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess="ABOVE"),
                )
                assert result.word == "ABOVE"
                assert len(result.feedback) == 5
                # A-B are correct, O is present, V is absent, E is absent
                assert result.feedback[0] == LetterFeedback.CORRECT  # A
                assert result.feedback[1] == LetterFeedback.CORRECT  # B

    async def test_invalid_word_rejected(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """A word not in the dictionary should be rejected."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(target_word="ABOUT", session_id="test-session"),
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
        """A guess that isn't 5 letters should be rejected by the validator."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(target_word="ABOUT", session_id="test-session"),
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
                activities=[validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(target_word="ABOUT", session_id="test-session"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                result = await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess="ABOUT"),
                )
                assert result.word == "ABOUT"
                assert all(
                    feedback == LetterFeedback.CORRECT for feedback in result.feedback
                )

                # Workflow should complete and return final state
                final_state = await handle.result()
                assert final_state.status == "won"
                assert len(final_state.guesses) == 1

    async def test_six_wrong_guesses_loses_game(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Using all 6 guesses without winning should set status to 'lost'."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=[validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(target_word="ABOUT", session_id="test-session"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # Use 6 different valid words that are NOT "ABOUT"
                wrong_guesses = [
                    "ABOVE",
                    "ABUSE",
                    "ACTOR",
                    "ADMIT",
                    "ADOPT",
                    "ADULT",
                ]
                for guess_word in wrong_guesses:
                    await handle.execute_update(
                        UserSessionWorkflow.make_guess,
                        MakeGuessInput(guess=guess_word),
                    )

                # Workflow should complete with "lost" status
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
                activities=[validate_guess],
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(target_word="ABOUT", session_id="test-session"),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                # Win the game
                await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess="ABOUT"),
                )

                # Wait for workflow to complete
                await handle.result()

                # Now try to guess again — should fail
                with pytest.raises(
                    (WorkflowUpdateFailedError, WorkflowFailureError, RPCError)
                ):
                    await handle.execute_update(
                        UserSessionWorkflow.make_guess,
                        MakeGuessInput(guess="ABOVE"),
                    )
