# ABOUTME: Tests for the calculate_feedback function that determines green,
# yellow, and gray feedback for each letter in a Wordle guess.
from durable_wordle.game_logic import calculate_feedback
from durable_wordle.models import LetterFeedback

CORRECT = LetterFeedback.CORRECT
PRESENT = LetterFeedback.PRESENT
ABSENT = LetterFeedback.ABSENT


def test_all_correct_letters() -> None:
    """When guess matches target exactly, all feedback should be CORRECT."""
    result = calculate_feedback("APPLE", "APPLE")
    assert result == [CORRECT, CORRECT, CORRECT, CORRECT, CORRECT]


def test_all_absent_letters() -> None:
    """When no letters match, all feedback should be ABSENT."""
    result = calculate_feedback("BRICK", "STUDY")
    assert result == [ABSENT, ABSENT, ABSENT, ABSENT, ABSENT]


def test_mixed_feedback() -> None:
    """Mix of CORRECT, PRESENT, and ABSENT feedback."""
    # target=APPLE, guess=ARISE: A=CORRECT, R=ABSENT, I=ABSENT, S=ABSENT, E=CORRECT
    result = calculate_feedback("ARISE", "APPLE")
    assert result == [CORRECT, ABSENT, ABSENT, ABSENT, CORRECT]


def test_letter_in_wrong_position() -> None:
    """Letter present in target but in wrong position should be PRESENT."""
    # target=HEART, guess=THETA:
    #   T=PRESENT, H=PRESENT, E=PRESENT, T=ABSENT, A=PRESENT
    # HEART has H,E,A,R,T and THETA has T,H,E,T,A
    # pos0: T vs H → T is in HEART at pos4 → PRESENT
    # pos1: H vs E → H is in HEART at pos0 → PRESENT
    # pos2: E vs A → E is in HEART at pos1 → PRESENT
    # pos3: T vs R → T already accounted for (1 T in target, used by pos0) → ABSENT
    # pos4: A vs T → A is in HEART at pos2 → PRESENT
    result = calculate_feedback("THETA", "HEART")
    assert result == [PRESENT, PRESENT, PRESENT, ABSENT, PRESENT]


def test_duplicate_letter_in_guess_target_has_two() -> None:
    """Duplicate letters: target=AABBB, guess=XAAAA.

    Target has 2 A's (pos 0, 1). Guess has 4 A's (pos 1, 2, 3, 4).
    pos 0: X → ABSENT (no X in target)
    pos 1: A → CORRECT (exact match at pos 1)
    pos 2: A → PRESENT (one remaining A in target at pos 0)
    pos 3: A → ABSENT (no more A's available)
    pos 4: A → ABSENT (no more A's available)
    """
    result = calculate_feedback("XAAAA", "AABBB")
    assert result == [ABSENT, CORRECT, PRESENT, ABSENT, ABSENT]


def test_duplicate_letter_in_guess_target_has_none() -> None:
    """When guess has duplicate letters that don't exist in target, all ABSENT."""
    # target=BRICK, guess=AAHED: A at pos0 → ABSENT, A at pos1 → ABSENT
    result = calculate_feedback("AAHED", "BRICK")
    assert result == [ABSENT, ABSENT, ABSENT, ABSENT, ABSENT]


def test_duplicate_letter_complex_scenario() -> None:
    """Target=HELLO, guess=LLONE.

    Target: H(0) E(1) L(2) L(3) O(4) — two L's, one O
    Guess:  L(0) L(1) O(2) N(3) E(4)

    First pass (exact matches): no exact matches at any position.
    Remaining target counts: H=1, E=1, L=2, O=1

    Second pass:
    pos 0: L → L in remaining (count=2) → PRESENT, remaining L=1
    pos 1: L → L in remaining (count=1) → PRESENT, remaining L=0
    pos 2: O → O in remaining (count=1) → PRESENT, remaining O=0
    pos 3: N → not in remaining → ABSENT
    pos 4: E → E in remaining (count=1) → PRESENT, remaining E=0
    """
    result = calculate_feedback("LLONE", "HELLO")
    assert result == [PRESENT, PRESENT, PRESENT, ABSENT, PRESENT]


def test_case_insensitivity() -> None:
    """calculate_feedback should normalize to uppercase internally."""
    result = calculate_feedback("Hello", "HELLO")
    assert result == [CORRECT, CORRECT, CORRECT, CORRECT, CORRECT]


def test_case_insensitivity_lowercase_target() -> None:
    """Both guess and target in lowercase should work."""
    result = calculate_feedback("apple", "apple")
    assert result == [CORRECT, CORRECT, CORRECT, CORRECT, CORRECT]
