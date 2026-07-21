# ABOUTME: Tests for Temporal activities — validate_guess for word validation
# and select_word for deterministic daily word selection.
import datetime

from temporalio.testing import ActivityEnvironment

from durable_wordle.activities import (
    choose_absurdle_feedback,
    select_word,
    validate_guess,
)
from durable_wordle.models import (
    AbsurdleFeedbackInput,
    LetterFeedback,
    SelectWordInput,
    ValidateGuessInput,
)
from durable_wordle.word_lists import get_daily_word


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


class TestSelectWord:
    """Tests for the select_word activity."""

    def test_returns_word_for_date(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """Activity should return the same word as get_daily_word for a date."""
        game_date = datetime.date(2025, 1, 1)
        result = activity_environment.run(
            select_word,
            SelectWordInput(game_date=game_date.isoformat()),
        )
        assert result == get_daily_word(game_date)

    def test_deterministic_across_calls(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """Same date should always produce the same word."""
        date_iso = "2025-06-15"
        first = activity_environment.run(
            select_word, SelectWordInput(game_date=date_iso)
        )
        second = activity_environment.run(
            select_word, SelectWordInput(game_date=date_iso)
        )
        assert first == second

    def test_different_dates_can_differ(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """Different dates should generally produce different words."""
        words = set()
        for day_offset in range(30):
            game_date = datetime.date(2025, 1, 1) + datetime.timedelta(days=day_offset)
            word = activity_environment.run(
                select_word,
                SelectWordInput(game_date=game_date.isoformat()),
            )
            words.add(word)
        # Over 30 days, we should see at least a few different words
        assert len(words) > 1

    def test_returns_uppercase(self, activity_environment: ActivityEnvironment) -> None:
        """Activity should return an uppercase word."""
        result = activity_environment.run(
            select_word,
            SelectWordInput(game_date="2025-01-01"),
        )
        assert result == result.upper()
        assert len(result) == 5

    def test_random_mode_returns_word(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """Empty game_date should return a random word from the answer list."""
        result = activity_environment.run(select_word, SelectWordInput())
        assert len(result) == 5
        assert result == result.upper()

    def test_random_mode_varies(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """Multiple random calls should produce different words sometimes."""
        words = set()
        for _ in range(20):
            word = activity_environment.run(select_word, SelectWordInput())
            words.add(word)
        assert len(words) > 1


# Full calculate_feedback tests are in test_game_logic.py


class TestChooseAbsurdleFeedback:
    """Tests for adversarial Absurdle feedback selection."""

    def test_chooses_largest_remaining_candidate_partition(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """The activity should keep the largest possible candidate set."""
        result = activity_environment.run(
            choose_absurdle_feedback,
            AbsurdleFeedbackInput(
                guess="CRANE",
                candidates=["BRICK", "SLUMP", "GHOST", "CRANK"],
            ),
        )

        assert result.feedback == [
            LetterFeedback.ABSENT,
            LetterFeedback.ABSENT,
            LetterFeedback.ABSENT,
            LetterFeedback.ABSENT,
            LetterFeedback.ABSENT,
        ]
        assert result.candidates == ["GHOST", "SLUMP"]
        assert result.reveal_word == "GHOST"

    def test_avoids_all_correct_feedback_when_other_candidates_remain(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """Absurdle should not concede a win while another partition is larger."""
        result = activity_environment.run(
            choose_absurdle_feedback,
            AbsurdleFeedbackInput(
                guess="APPLE",
                candidates=["APPLE", "BRICK", "GHOST"],
            ),
        )

        assert result.feedback != [LetterFeedback.CORRECT] * 5
        assert result.candidates == ["BRICK", "GHOST"]

    def test_inch_corridor_keeps_other_answer_candidates(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """Absurdle should force the player through same-pattern corridors."""
        result = activity_environment.run(
            choose_absurdle_feedback,
            AbsurdleFeedbackInput(
                guess="WINCH",
                candidates=["CINCH", "PINCH", "WINCH"],
            ),
        )

        assert result.feedback == [
            LetterFeedback.ABSENT,
            LetterFeedback.CORRECT,
            LetterFeedback.CORRECT,
            LetterFeedback.CORRECT,
            LetterFeedback.CORRECT,
        ]
        assert result.candidates == ["CINCH", "PINCH"]
        assert result.reveal_word == "CINCH"

    def test_returns_all_correct_when_only_candidate_matches_guess(
        self, activity_environment: ActivityEnvironment
    ) -> None:
        """The final candidate can still be guessed normally."""
        result = activity_environment.run(
            choose_absurdle_feedback,
            AbsurdleFeedbackInput(guess="APPLE", candidates=["APPLE"]),
        )

        assert result.feedback == [LetterFeedback.CORRECT] * 5
        assert result.candidates == ["APPLE"]
        assert result.reveal_word == "APPLE"
