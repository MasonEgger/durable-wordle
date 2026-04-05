# ABOUTME: Temporal activities for Durable Wordle. Contains validate_guess,
# a sync activity that checks if a guess is a valid 5-letter word.
from dataclasses import dataclass

from temporalio import activity

from durable_wordle.word_lists import is_valid_guess


@dataclass
class ValidateGuessInput:
    """Input for the validate_guess activity.

    :param guess: The word to validate.
    """

    guess: str


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
