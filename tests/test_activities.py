# ABOUTME: Tests for the validate_guess Temporal activity that checks
# whether a submitted guess is a valid 5-letter word from the word list.
import pytest
from temporalio.testing import ActivityEnvironment

from durable_wordle.activities import ValidateGuessInput, validate_guess


@pytest.fixture()
def activity_environment() -> ActivityEnvironment:
    """Create a Temporal ActivityEnvironment for isolated activity testing."""
    return ActivityEnvironment()


class TestValidateGuess:
    """Tests for the validate_guess activity."""

    def test_valid_word_returns_true(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        result = activity_environment.run(
            validate_guess, ValidateGuessInput(guess="ABOUT")
        )
        assert result is True

    def test_invalid_word_returns_false(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        result = activity_environment.run(
            validate_guess, ValidateGuessInput(guess="ZZZZZ")
        )
        assert result is False

    def test_case_insensitive(self, activity_environment: ActivityEnvironment) -> None:
        result = activity_environment.run(
            validate_guess, ValidateGuessInput(guess="about")
        )
        assert result is True

    def test_empty_string_returns_false(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        result = activity_environment.run(validate_guess, ValidateGuessInput(guess=""))
        assert result is False

    def test_wrong_length_returns_false(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        result = activity_environment.run(
            validate_guess, ValidateGuessInput(guess="TOOLONG")
        )
        assert result is False
