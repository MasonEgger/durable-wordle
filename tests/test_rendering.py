# ABOUTME: Tests for template rendering helpers such as keyboard reveal timing.
# ABOUTME: Covers duplicate-letter cases where one key maps to multiple tiles.
import json
import pathlib
import re
from typing import TypedDict, cast

from durable_wordle.models import GuessResult, LetterFeedback
from durable_wordle.rendering import build_keyboard_transition_indices


class IdleDemoSequence(TypedDict):
    """Start-screen tutorial sequence parsed from the HTML template."""

    target: str
    guesses: list[str]


def _read_idle_demo_sequences() -> list[IdleDemoSequence]:
    """Read the hardcoded tutorial sequences from the start-page script."""
    template = pathlib.Path("templates/index.html").read_text()
    sequence_match = re.search(
        r"var IDLE_DEMO_SEQUENCES = (\[.*?\]);\n    var IDLE_DEMO_FEEDBACK_CLASS",
        template,
        re.DOTALL,
    )
    assert sequence_match is not None
    return cast(list[IdleDemoSequence], json.loads(sequence_match.group(1)))


def _feedback_for_guess(guess: str, target: str) -> list[str]:
    """Calculate Wordle feedback using the same two-pass tutorial rules."""
    feedback = ["absent"] * len(target)
    remaining_letters: dict[str, int] = {}

    for letter_index, target_letter in enumerate(target):
        if guess[letter_index] == target_letter:
            feedback[letter_index] = "correct"
        else:
            remaining_letters[target_letter] = (
                remaining_letters.get(target_letter, 0) + 1
            )

    for letter_index, guess_letter in enumerate(guess):
        if feedback[letter_index] == "correct":
            continue
        if remaining_letters.get(guess_letter, 0) > 0:
            feedback[letter_index] = "present"
            remaining_letters[guess_letter] -= 1

    return feedback


def test_idle_demo_sequences_include_wrong_spot_clues() -> None:
    """Every tutorial sequence should demonstrate a yellow wrong-spot clue."""
    for sequence in _read_idle_demo_sequences():
        non_final_guesses = sequence["guesses"][:-1]
        sequence_feedback = [
            _feedback_for_guess(guess, sequence["target"])
            for guess in non_final_guesses
        ]

        assert any("present" in feedback for feedback in sequence_feedback), sequence


def test_idle_demo_six_guess_sequence_places_r_deliberately() -> None:
    """The six-guess tutorial should show R moving before it is solved."""
    six_guess_sequences = [
        sequence
        for sequence in _read_idle_demo_sequences()
        if len(sequence["guesses"]) == 6
    ]
    assert len(six_guess_sequences) == 1
    sequence = six_guess_sequences[0]
    target_r_index = sequence["target"].index("R")
    r_clues: list[tuple[int, str]] = []

    for guess in sequence["guesses"][:-1]:
        feedback = _feedback_for_guess(guess, sequence["target"])
        for letter_index, guess_letter in enumerate(guess):
            if guess_letter == "R":
                r_clues.append((letter_index, feedback[letter_index]))

    assert any(
        letter_index != target_r_index and feedback == "present"
        for letter_index, feedback in r_clues
    )
    assert any(
        letter_index == target_r_index and feedback == "correct"
        for letter_index, feedback in r_clues
    )


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
