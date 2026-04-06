# ABOUTME: Temporal activities for Durable Wordle. Contains word validation
# via dictionary API, word selection, and guess feedback calculation.
import datetime
import random
from collections import Counter

import requests
from temporalio import activity

from durable_wordle.models import (
    CalculateFeedbackInput,
    LetterFeedback,
    SelectWordInput,
    ValidateGuessInput,
)
from durable_wordle.word_lists import ANSWER_LIST, get_daily_word

DICTIONARY_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en"


@activity.defn
def validate_guess(activity_input: ValidateGuessInput) -> bool:
    """Check whether a guess is a real English word via dictionary API.

    The update validator already rejects wrong length and non-alphabetic
    guesses before this activity runs. This activity only checks the
    external dictionary.

    :param activity_input: The activity input containing the guess word.
    :returns: ``True`` if the guess is a real English word.
    """
    normalized = activity_input.guess.strip().upper()
    response = requests.get(
        f"{DICTIONARY_API_URL}/{normalized.lower()}",
        timeout=5,
    )
    is_valid: bool = response.status_code == 200
    activity.logger.info(
        "validate_guess: %s → %s (status=%d)",
        normalized,
        "valid" if is_valid else "invalid",
        response.status_code,
    )
    return is_valid


@activity.defn
def select_word(activity_input: SelectWordInput) -> str:
    """Select the target word for a game.

    If ``game_date`` is provided, uses deterministic date-seeded selection
    so every player gets the same word on the same day. Otherwise, picks
    a random word from the answer list.

    :param activity_input: Contains an optional ISO date string.
    :returns: The target word in uppercase.
    """
    if activity_input.game_date:
        game_date = datetime.date.fromisoformat(activity_input.game_date)
        word = get_daily_word(game_date)
        activity.logger.info("select_word: daily, date=%s → %s", game_date, word)
    else:
        word = random.choice(ANSWER_LIST)
        activity.logger.info("select_word: random → %s", word)
    return word


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

    result = [
        letter_feedback for letter_feedback in feedback if letter_feedback is not None
    ]
    feedback_summary = "".join(fb.value[0].upper() for fb in result)
    activity.logger.info(
        "calculate_feedback: %s vs %s → %s",
        normalized_guess,
        normalized_target,
        feedback_summary,
    )
    return result
