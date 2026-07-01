# ABOUTME: Tests for template rendering helpers such as keyboard reveal timing.
# ABOUTME: Covers duplicate-letter cases where one key maps to multiple tiles.
from durable_wordle.models import GuessResult, LetterFeedback
from durable_wordle.rendering import build_keyboard_transition_indices


def test_keyboard_transition_uses_tile_that_creates_best_feedback() -> None:
    """Duplicate letters should flip the key with the tile that upgrades it."""
    guesses = [
        GuessResult(
            word="BRAVE",
            feedback=[
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
            ],
        ),
        GuessResult(
            word="ALARM",
            feedback=[
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
                LetterFeedback.CORRECT,
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
            ],
        ),
    ]

    assert build_keyboard_transition_indices(guesses)["A"] == 2


def test_keyboard_transition_skips_unchanged_best_feedback() -> None:
    """A key that is already green should not animate again for worse feedback."""
    guesses = [
        GuessResult(
            word="BRAVE",
            feedback=[
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
                LetterFeedback.CORRECT,
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
            ],
        ),
        GuessResult(
            word="ALARM",
            feedback=[
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
                LetterFeedback.CORRECT,
                LetterFeedback.ABSENT,
                LetterFeedback.ABSENT,
            ],
        ),
    ]

    assert "A" not in build_keyboard_transition_indices(guesses)
