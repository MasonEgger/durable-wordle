# ABOUTME: Core Wordle game logic containing the calculate_feedback function
# that determines green/yellow/gray feedback for each letter in a guess.
from collections import Counter

from durable_wordle.models import LetterFeedback


def calculate_feedback(guess: str, target: str) -> list[LetterFeedback]:
    """Calculate per-letter feedback for a Wordle guess against a target word.

    Uses a two-pass algorithm to correctly handle duplicate letters:

    1. First pass marks exact positional matches as ``CORRECT`` and counts
       remaining unmatched target letters.
    2. Second pass marks non-exact letters as ``PRESENT`` if they exist in
       the remaining target letter pool, otherwise ``ABSENT``.

    :param guess: The guessed word (any case, normalized to uppercase).
    :param target: The target word (any case, normalized to uppercase).
    :returns: A list of :class:`LetterFeedback` values, one per letter position.
    """
    normalized_guess = guess.upper()
    normalized_target = target.upper()
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

    # All positions are now assigned; cast away the None possibility
    return [
        letter_feedback for letter_feedback in feedback if letter_feedback is not None
    ]
