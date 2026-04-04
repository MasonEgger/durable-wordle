# ABOUTME: Tests for the core data models including LetterFeedback enum,
# GuessResult dataclass, and GameState dataclass with game-over logic.
from durable_wordle.models import GameState, GuessResult, LetterFeedback


def test_letter_feedback_has_correct_value() -> None:
    """LetterFeedback enum should have a CORRECT member."""
    assert LetterFeedback.CORRECT is not None


def test_letter_feedback_has_present_value() -> None:
    """LetterFeedback enum should have a PRESENT member."""
    assert LetterFeedback.PRESENT is not None


def test_letter_feedback_has_absent_value() -> None:
    """LetterFeedback enum should have an ABSENT member."""
    assert LetterFeedback.ABSENT is not None


def test_guess_result_stores_word_and_feedback() -> None:
    """GuessResult should store a word and a list of LetterFeedback values."""
    feedback = [
        LetterFeedback.CORRECT,
        LetterFeedback.ABSENT,
        LetterFeedback.PRESENT,
        LetterFeedback.ABSENT,
        LetterFeedback.CORRECT,
    ]
    result = GuessResult(word="HELLO", feedback=feedback)
    assert result.word == "HELLO"
    assert result.feedback == feedback


def test_game_state_initializes_with_defaults() -> None:
    """GameState should initialize with defaults."""
    state = GameState(target_word="APPLE")
    assert state.target_word == "APPLE"
    assert state.guesses == []
    assert state.max_guesses == 6
    assert state.status == "playing"


def test_game_state_is_game_over_when_won() -> None:
    """is_game_over should return True when status is 'won'."""
    state = GameState(target_word="APPLE", status="won")
    assert state.is_game_over is True


def test_game_state_is_game_over_when_lost() -> None:
    """is_game_over should return True when status is 'lost'."""
    state = GameState(target_word="APPLE", status="lost")
    assert state.is_game_over is True


def test_game_state_is_not_game_over_when_playing() -> None:
    """is_game_over should return False when status is 'playing'."""
    state = GameState(target_word="APPLE", status="playing")
    assert state.is_game_over is False
