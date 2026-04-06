# ABOUTME: Temporal activities for Durable Wordle. Contains word validation,
# daily word selection, and guess feedback calculation.
import datetime
from collections import Counter

from temporalio import activity

from durable_wordle.models import (
    CalculateFeedbackInput,
    LetterFeedback,
    SelectDailyWordInput,
    ValidateGuessInput,
)
from durable_wordle.word_lists import get_daily_word, is_valid_guess


@activity.defn
def validate_guess(activity_input: ValidateGuessInput) -> bool:
    """Check whether a guess is a valid 5-letter word from the word list.

    Normalizes the guess to uppercase, checks length and alphabetic
    characters, then verifies membership in the valid guesses set.

    :param activity_input: The activity input containing the guess word.
    :returns: ``True`` if the guess is a valid 5-letter word.
    """
    normalized = activity_input.guess.strip().upper()
    if len(normalized) != 5:
        return False
    if not normalized.isalpha():
        return False
    return is_valid_guess(normalized)


@activity.defn
def select_daily_word(activity_input: SelectDailyWordInput) -> str:
    """Select the daily target word for a given date.

    Uses deterministic seeding so every player gets the same word
    on the same calendar day.

    :param activity_input: The activity input containing an ISO date string.
    :returns: The daily target word in uppercase.
    """
    game_date = datetime.date.fromisoformat(activity_input.game_date)
    return get_daily_word(game_date)


@activity.defn
def calculate_feedback(
    activity_input: CalculateFeedbackInput,
) -> list[LetterFeedback]:
    """Calculate per-letter feedback for a Wordle guess against a target word.

    Uses a two-pass algorithm to correctly handle duplicate letters:

    1. First pass marks exact positional matches as ``CORRECT`` and counts
       remaining unmatched target letters.
    2. Second pass marks non-exact letters as ``PRESENT`` if they exist in
       the remaining target letter pool, otherwise ``ABSENT``.

    :param activity_input: Contains the guess and target words.
    :returns: A list of per-letter feedback values.
    """
    normalized_guess = activity_input.guess.upper()
    normalized_target = activity_input.target.upper()
    word_length = len(normalized_guess)

    feedback: list[LetterFeedback | None] = [None] * word_length
    remaining_counts: Counter[str] = Counter(normalized_target)

    # First pass: mark exact matches (CORRECT) and decrement their counts
    for position in range(word_length):
        guess_letter = normalized_guess[position]
        target_letter = normalized_target[position]
        if guess_letter == target_letter:
            feedback[position] = LetterFeedback.CORRECT
            remaining_counts[guess_letter] -= 1

    # Second pass: mark PRESENT or ABSENT for non-exact positions
    for position in range(word_length):
        if feedback[position] is not None:
            continue
        guess_letter = normalized_guess[position]
        if remaining_counts[guess_letter] > 0:
            feedback[position] = LetterFeedback.PRESENT
            remaining_counts[guess_letter] -= 1
        else:
            feedback[position] = LetterFeedback.ABSENT

    return [
        letter_feedback
        for letter_feedback in feedback
        if letter_feedback is not None
    ]
