# ABOUTME: Tests for the SQLite leaderboard's participant tracking — the records
# kept for everyone who plays (win or lose) for post-event email outreach.
import pathlib

import pytest


def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Point the leaderboard at a throwaway DB and reset the schema flag."""
    from durable_wordle import leaderboard

    monkeypatch.setattr(leaderboard, "DB_FILE", tmp_path / "lb.db")
    monkeypatch.setattr(leaderboard, "_schema_ready", False)


def test_record_participant_persists_and_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A recorded participant should come back from get_participants_for_date."""
    from durable_wordle import leaderboard

    _isolate_db(monkeypatch, tmp_path)
    leaderboard.record_participant(
        "Ada", "ada@example.com", "CODE", "RAN", "2026-06-30"
    )

    participants = leaderboard.get_participants_for_date("2026-06-30")
    assert len(participants) == 1
    assert participants[0].player_name == "Ada"
    assert participants[0].email == "ada@example.com"
    assert participants[0].madlib_noun == "CODE"
    assert participants[0].madlib_verb == "RAN"
    assert participants[0].game_date == "2026-06-30"


def test_record_participant_dedupes_per_email_per_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Replaying the same email on the same day keeps a single record."""
    from durable_wordle import leaderboard

    _isolate_db(monkeypatch, tmp_path)
    leaderboard.record_participant(
        "Ada", "ada@example.com", "CODE", "RAN", "2026-06-30"
    )
    leaderboard.record_participant(
        "Ada", "ada@example.com", "DESK", "JUMPED", "2026-06-30"
    )

    participants = leaderboard.get_participants_for_date("2026-06-30")
    assert len(participants) == 1


def test_record_participant_skips_blank_email(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """No email means no outreach record (can't email them anyway)."""
    from durable_wordle import leaderboard

    _isolate_db(monkeypatch, tmp_path)
    leaderboard.record_participant("NoEmail", "", "CODE", "RAN", "2026-06-30")
    leaderboard.record_participant("Spaces", "   ", "CODE", "RAN", "2026-06-30")

    assert leaderboard.get_participants_for_date("2026-06-30") == []


def test_get_participants_for_date_scopes_by_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Participants are filtered by game_date."""
    from durable_wordle import leaderboard

    _isolate_db(monkeypatch, tmp_path)
    leaderboard.record_participant("Today", "today@example.com", "", "", "2026-06-30")
    leaderboard.record_participant(
        "Yesterday", "yesterday@example.com", "", "", "2026-06-29"
    )

    today = leaderboard.get_participants_for_date("2026-06-30")
    assert [p.player_name for p in today] == ["Today"]
