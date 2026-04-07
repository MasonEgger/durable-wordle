# ABOUTME: Tests for word list data and daily word selection logic.
# Validates list integrity, membership, and deterministic word picking.
import datetime

from durable_wordle.word_lists import (
    ANSWER_LIST,
    VALID_GUESSES,
    get_daily_word,
    is_valid_guess,
)


class TestAnswerList:
    """Tests for the curated answer word list."""

    def test_answer_list_contains_only_five_letter_words(self) -> None:
        for word in ANSWER_LIST:
            assert len(word) == 5, f"Answer word {word!r} is not 5 letters"

    def test_answer_list_contains_only_alphabetic_words(self) -> None:
        for word in ANSWER_LIST:
            assert word.isalpha(), f"Answer word {word!r} is not alphabetic"

    def test_answer_list_has_reasonable_size(self) -> None:
        assert 200 <= len(ANSWER_LIST) <= 500, (
            f"Answer list has {len(ANSWER_LIST)} words, expected 200-500"
        )


class TestValidGuesses:
    """Tests for the valid guesses word list."""

    def test_valid_guesses_contains_only_five_letter_alphabetic_words(self) -> None:
        for word in VALID_GUESSES:
            assert len(word) == 5, f"Valid guess {word!r} is not 5 letters"
            assert word.isalpha(), f"Valid guess {word!r} is not alphabetic"

    def test_valid_guesses_is_superset_of_answer_list(self) -> None:
        answer_set = set(ANSWER_LIST)
        guess_set = set(VALID_GUESSES)
        missing = answer_set - guess_set
        assert not missing, f"Answer words missing from valid guesses: {missing}"


class TestGetDailyWord:
    """Tests for deterministic daily word selection."""

    def test_returns_word_from_answer_list(self) -> None:
        today = datetime.date(2026, 4, 4)
        word = get_daily_word(today)
        assert word in ANSWER_LIST

    def test_returns_same_word_for_same_date(self) -> None:
        date = datetime.date(2026, 1, 15)
        first_call = get_daily_word(date)
        second_call = get_daily_word(date)
        assert first_call == second_call

    def test_returns_different_words_for_different_dates(self) -> None:
        date_one = datetime.date(2026, 1, 1)
        date_two = datetime.date(2026, 6, 15)
        word_one = get_daily_word(date_one)
        word_two = get_daily_word(date_two)
        assert word_one != word_two


class TestIsValidGuess:
    """Tests for the word membership check function."""

    def test_valid_word_returns_true(self) -> None:
        # Pick a word we know is in the answer list (and thus valid guesses)
        sample_word = list(ANSWER_LIST)[0]
        assert is_valid_guess(sample_word) is True

    def test_invalid_word_returns_false(self) -> None:
        assert is_valid_guess("ZZZZZ") is False

    def test_is_case_insensitive(self) -> None:
        sample_word = list(ANSWER_LIST)[0]
        assert is_valid_guess(sample_word.lower()) is True
