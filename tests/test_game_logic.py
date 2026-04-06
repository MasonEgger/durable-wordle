# ABOUTME: Tests for the calculate_feedback activity that determines green,
# yellow, and gray feedback for each letter in a Wordle guess.
import pytest
from temporalio.testing import ActivityEnvironment

from durable_wordle.activities import calculate_feedback
from durable_wordle.models import CalculateFeedbackInput, LetterFeedback

CORRECT = LetterFeedback.CORRECT
PRESENT = LetterFeedback.PRESENT
ABSENT = LetterFeedback.ABSENT


@pytest.fixture()
def activity_environment() -> ActivityEnvironment:
    """Create a Temporal ActivityEnvironment for isolated activity testing."""
    return ActivityEnvironment()


def test_all_correct_letters(activity_environment: ActivityEnvironment) -> None:
    """When guess matches target exactly, all feedback should be CORRECT."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="APPLE", target="APPLE")
    )
    assert result == [CORRECT, CORRECT, CORRECT, CORRECT, CORRECT]


def test_all_absent_letters(activity_environment: ActivityEnvironment) -> None:
    """When no letters match, all feedback should be ABSENT."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="BRICK", target="STUDY")
    )
    assert result == [ABSENT, ABSENT, ABSENT, ABSENT, ABSENT]


def test_mixed_feedback(activity_environment: ActivityEnvironment) -> None:
    """Mix of CORRECT, PRESENT, and ABSENT feedback."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="ARISE", target="APPLE")
    )
    assert result == [CORRECT, ABSENT, ABSENT, ABSENT, CORRECT]


def test_letter_in_wrong_position(activity_environment: ActivityEnvironment) -> None:
    """Letter present in target but in wrong position should be PRESENT."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="THETA", target="HEART")
    )
    assert result == [PRESENT, PRESENT, PRESENT, ABSENT, PRESENT]


def test_duplicate_letter_in_guess_target_has_two(
    activity_environment: ActivityEnvironment,
) -> None:
    """Duplicate letters: target has 2 A's, guess has 4 A's."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="XAAAA", target="AABBB")
    )
    assert result == [ABSENT, CORRECT, PRESENT, ABSENT, ABSENT]


def test_duplicate_letter_in_guess_target_has_none(
    activity_environment: ActivityEnvironment,
) -> None:
    """When guess has duplicate letters not in target, all ABSENT."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="AAHED", target="BRICK")
    )
    assert result == [ABSENT, ABSENT, ABSENT, ABSENT, ABSENT]


def test_duplicate_letter_complex_scenario(
    activity_environment: ActivityEnvironment,
) -> None:
    """Target=HELLO, guess=LLONE — two L's, displaced letters."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="LLONE", target="HELLO")
    )
    assert result == [PRESENT, PRESENT, PRESENT, ABSENT, PRESENT]


def test_case_insensitivity(activity_environment: ActivityEnvironment) -> None:
    """calculate_feedback should normalize to uppercase internally."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="Hello", target="HELLO")
    )
    assert result == [CORRECT, CORRECT, CORRECT, CORRECT, CORRECT]


def test_case_insensitivity_lowercase_target(
    activity_environment: ActivityEnvironment,
) -> None:
    """Both guess and target in lowercase should work."""
    result = activity_environment.run(
        calculate_feedback, CalculateFeedbackInput(guess="apple", target="apple")
    )
    assert result == [CORRECT, CORRECT, CORRECT, CORRECT, CORRECT]
