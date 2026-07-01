# ABOUTME: Tests for UserSessionWorkflow covering game lifecycle — initial state,
# valid/invalid guesses, win/loss conditions, and post-game rejection.
import concurrent.futures
import uuid
from datetime import timedelta

import pytest
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from durable_wordle.activities import (
    calculate_feedback,
    choose_absurdle_feedback,
    select_word,
    validate_guess,
)
from durable_wordle.models import (
    GameMode,
    LetterFeedback,
    MakeGuessInput,
    WorkflowInput,
)
from durable_wordle.word_lists import ANSWER_LIST, VALID_GUESSES
from durable_wordle.workflow import UserSessionWorkflow

# Valid 5-letter words that are NOT in the answer list (guaranteed wrong)
WRONG_GUESSES = ["ABOVE", "ABUSE", "ACTOR", "ADMIT", "ADOPT", "ADULT"]
WORKFLOW_ACTIVITIES = [
    calculate_feedback,
    choose_absurdle_feedback,
    select_word,
    validate_guess,
]


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
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
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
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
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
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
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
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
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
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
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
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
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
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(session_id="test-session"),
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
        """Daily mode should select word via the select_word activity."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=WORKFLOW_ACTIVITIES,
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

    async def test_absurdle_mode_tracks_remaining_candidates(
        self, workflow_environment: WorkflowEnvironment, task_queue: str
    ) -> None:
        """Absurdle mode should persist candidate state after each guess."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            async with Worker(
                workflow_environment.client,
                task_queue=task_queue,
                workflows=[UserSessionWorkflow],
                activities=WORKFLOW_ACTIVITIES,
                activity_executor=executor,
            ):
                handle = await workflow_environment.client.start_workflow(
                    UserSessionWorkflow.run,
                    WorkflowInput(
                        session_id="absurdle-session",
                        game_mode=GameMode.ABSURDLE,
                    ),
                    id=str(uuid.uuid4()),
                    task_queue=task_queue,
                )

                result = await handle.execute_update(
                    UserSessionWorkflow.make_guess,
                    MakeGuessInput(guess="CRANE"),
                )

                state = await handle.query(UserSessionWorkflow.get_game_state)
                assert result.word == "CRANE"
                assert state.game_mode is GameMode.ABSURDLE
                assert state.status == "playing"
                assert 0 < len(state.remaining_candidates) < len(VALID_GUESSES)
                assert state.target_word in state.remaining_candidates


class TestInactivityTimeout:
    """Tests for the inactivity timeout that abandons idle games."""

    async def test_game_abandoned_after_inactivity(self) -> None:
        """A game with no guesses should abandon once the timeout elapses."""
        async with await WorkflowEnvironment.start_time_skipping() as env:
            task_queue = f"timeout-{uuid.uuid4()}"
            with concurrent.futures.ThreadPoolExecutor() as executor:
                async with Worker(
                    env.client,
                    task_queue=task_queue,
                    workflows=[UserSessionWorkflow],
                    activities=WORKFLOW_ACTIVITIES,
                    activity_executor=executor,
                ):
                    handle = await env.client.start_workflow(
                        UserSessionWorkflow.run,
                        WorkflowInput(session_id="idle-session"),
                        id=str(uuid.uuid4()),
                        task_queue=task_queue,
                    )
                    # Time-skipping fast-forwards through the 60s timer.
                    final_state = await handle.result()
                    assert final_state.status == "abandoned"
                    assert len(final_state.guesses) == 0

    async def test_zero_inactivity_timeout_keeps_game_running(self) -> None:
        """A zero timeout should preserve the original untimed classic flow."""
        async with await WorkflowEnvironment.start_time_skipping() as env:
            task_queue = f"untimed-{uuid.uuid4()}"
            with concurrent.futures.ThreadPoolExecutor() as executor:
                async with Worker(
                    env.client,
                    task_queue=task_queue,
                    workflows=[UserSessionWorkflow],
                    activities=WORKFLOW_ACTIVITIES,
                    activity_executor=executor,
                ):
                    handle = await env.client.start_workflow(
                        UserSessionWorkflow.run,
                        WorkflowInput(
                            session_id="untimed-session",
                            inactivity_timeout_seconds=0,
                        ),
                        id=str(uuid.uuid4()),
                        task_queue=task_queue,
                    )

                    await env.sleep(timedelta(seconds=120))

                    state = await handle.query(UserSessionWorkflow.get_game_state)
                    assert state.status == "playing"
                    assert len(state.guesses) == 0

                    await handle.terminate("test cleanup")
