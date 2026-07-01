# ABOUTME: Temporal activities for Durable Wordle. Contains word validation
# via dictionary API, word selection, and guess feedback calculation.
import datetime
import random
from collections import Counter

import requests
from temporalio import activity

from durable_wordle.models import (
    AbsurdleFeedbackInput,
    AbsurdleFeedbackResult,
    CalculateFeedbackInput,
    LetterFeedback,
    SelectWordInput,
    ValidateGuessInput,
)
from durable_wordle.word_lists import ANSWER_LIST, get_daily_word, is_valid_guess

DICTIONARY_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en"
FEEDBACK_RANK: dict[LetterFeedback, int] = {
    LetterFeedback.ABSENT: 0,
    LetterFeedback.PRESENT: 1,
    LetterFeedback.CORRECT: 2,
}


@activity.defn
def validate_guess(activity_input: ValidateGuessInput) -> bool:
    """Check whether a guess is a real English word via dictionary API.

    The update validator already rejects wrong length and non-alphabetic
    guesses before this activity runs. This activity only checks the
    external dictionary.

    :param activity_input: The activity input containing the guess word.
    :returns: ``True`` if the guess is a real English word.
    """
    # Workflow normalizes before calling; the input is already uppercase.
    word = activity_input.guess
    # Fast path: skip the external API for words in our curated lists.
    if is_valid_guess(word):
        activity.logger.info("validate_guess: %s → valid (local list)", word)
        return True
    try:
        response = requests.get(
            f"{DICTIONARY_API_URL}/{word.lower()}",
            timeout=2,
        )
    except requests.RequestException as err:
        activity.logger.warning(
            "validate_guess: %s → dictionary lookup failed: %s", word, err
        )
        return False
    is_valid: bool = response.status_code == 200
    activity.logger.info(
        "validate_guess: %s → %s (status=%d)",
        word,
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
    feedback = _calculate_feedback(activity_input.guess, activity_input.target)

    feedback_summary = "".join(
        feedback_item.value[0].upper() for feedback_item in feedback
    )
    activity.logger.info(
        "calculate_feedback: %s vs %s → %s",
        activity_input.guess.upper(),
        activity_input.target.upper(),
        feedback_summary,
    )
    return feedback


@activity.defn
def choose_absurdle_feedback(
    activity_input: AbsurdleFeedbackInput,
) -> AbsurdleFeedbackResult:
    """Choose adversarial Absurdle feedback for a guess.

    Candidate words are partitioned by the feedback they would produce. The
    activity chooses the partition that leaves the most possible answers, with
    deterministic tie-breakers that prefer less helpful feedback.

    :param activity_input: Contains the guess and current candidate list.
    :returns: Selected feedback, remaining candidates, and a reveal word.
    """
    guess = activity_input.guess.upper()
    candidate_words = sorted(
        {candidate.upper() for candidate in activity_input.candidates}
    )
    if not candidate_words:
        return AbsurdleFeedbackResult(
            feedback=[LetterFeedback.ABSENT] * len(guess),
            candidates=[],
            reveal_word=guess,
        )

    partitions: dict[tuple[LetterFeedback, ...], list[str]] = {}
    for candidate_word in candidate_words:
        feedback = tuple(_calculate_feedback(guess, candidate_word))
        partitions.setdefault(feedback, []).append(candidate_word)

    selected_feedback, selected_candidates = max(
        partitions.items(),
        key=lambda partition: _absurdle_partition_key(
            partition[0],
            partition[1],
        ),
    )
    reveal_word = selected_candidates[0]
    feedback_summary = "".join(
        feedback_item.value[0].upper() for feedback_item in selected_feedback
    )
    activity.logger.info(
        "choose_absurdle_feedback: %s → %s (%d candidates)",
        guess,
        feedback_summary,
        len(selected_candidates),
    )
    return AbsurdleFeedbackResult(
        feedback=list(selected_feedback),
        candidates=selected_candidates,
        reveal_word=reveal_word,
    )


def _calculate_feedback(guess_input: str, target_input: str) -> list[LetterFeedback]:
    guess = guess_input.upper()
    target = target_input.upper()

    feedback: list[LetterFeedback] = [LetterFeedback.ABSENT] * len(guess)
    remaining_counts: Counter[str] = Counter(target)

    # First pass: mark exact matches (CORRECT) and decrement their counts
    for position in range(len(guess)):
        if guess[position] == target[position]:
            feedback[position] = LetterFeedback.CORRECT
            remaining_counts[guess[position]] -= 1

    # Second pass: mark PRESENT for non-exact positions with remaining letters
    for position in range(len(guess)):
        if feedback[position] is LetterFeedback.CORRECT:
            continue
        if remaining_counts[guess[position]] > 0:
            feedback[position] = LetterFeedback.PRESENT
            remaining_counts[guess[position]] -= 1

    return feedback


def _absurdle_partition_key(
    feedback: tuple[LetterFeedback, ...],
    candidates: list[str],
) -> tuple[int, int, int, tuple[int, ...]]:
    correct_count = feedback.count(LetterFeedback.CORRECT)
    present_count = feedback.count(LetterFeedback.PRESENT)
    feedback_pattern_key = tuple(
        -FEEDBACK_RANK[feedback_item] for feedback_item in feedback
    )
    return (
        len(candidates),
        -correct_count,
        -present_count,
        feedback_pattern_key,
    )
