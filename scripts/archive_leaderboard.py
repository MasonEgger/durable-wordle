# ABOUTME: Export a day's leaderboard AND all participants (including emails) to
# ABOUTME: CSV for prize/outreach. Output lands in data/archive/ (gitignored, PII).
"""Archive the leaderboard and participant list for a given day to CSV files.

Usage::

    uv run python scripts/archive_leaderboard.py            # today (LA time)
    uv run python scripts/archive_leaderboard.py 2026-06-29 # a specific day

Writes two CSVs: the leaderboard (winners) and every participant (win or lose,
for post-event email outreach). Both include emails, so they are written under
``data/archive/`` (gitignored).
"""

import argparse
import csv
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from durable_wordle.booth.leaderboard import (
    get_entries_for_date,
    get_participants_for_date,
)

_LA_TZ = ZoneInfo("America/Los_Angeles")
_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "archive"
_FIELDS = [
    "rank",
    "player_name",
    "email",
    "guesses",
    "elapsed_seconds",
    "elapsed_formatted",
    "madlib_noun",
    "madlib_verb",
    "submitted_at",
    "game_date",
]
_PARTICIPANT_FIELDS = [
    "player_name",
    "email",
    "madlib_noun",
    "madlib_verb",
    "first_seen",
    "game_date",
]


def archive_date(game_date: str) -> Path:
    """Write all leaderboard entries for ``game_date`` to a CSV file.

    :param game_date: ISO date string (``YYYY-MM-DD``).
    :returns: Path to the written CSV file.
    """
    entries = get_entries_for_date(game_date)
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _ARCHIVE_DIR / f"leaderboard-{game_date}.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        writer.writeheader()
        for rank, entry in enumerate(entries, start=1):
            writer.writerow(
                {
                    "rank": rank,
                    "player_name": entry.player_name,
                    "email": entry.email,
                    "guesses": entry.guesses,
                    "elapsed_seconds": entry.elapsed_seconds,
                    "elapsed_formatted": entry.elapsed_formatted,
                    "madlib_noun": entry.madlib_noun,
                    "madlib_verb": entry.madlib_verb,
                    "submitted_at": entry.submitted_at,
                    "game_date": entry.game_date,
                }
            )
    print(f"Archived {len(entries)} entries for {game_date} -> {output_path}")
    return output_path


def archive_participants(game_date: str) -> Path:
    """Write all participants for ``game_date`` (win or lose) to a CSV file.

    :param game_date: ISO date string (``YYYY-MM-DD``).
    :returns: Path to the written CSV file.
    """
    participants = get_participants_for_date(game_date)
    _ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _ARCHIVE_DIR / f"participants-{game_date}.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_PARTICIPANT_FIELDS)
        writer.writeheader()
        for participant in participants:
            writer.writerow(
                {
                    "player_name": participant.player_name,
                    "email": participant.email,
                    "madlib_noun": participant.madlib_noun,
                    "madlib_verb": participant.madlib_verb,
                    "first_seen": participant.first_seen,
                    "game_date": participant.game_date,
                }
            )
    print(f"Archived {len(participants)} participants for {game_date} -> {output_path}")
    return output_path


def main() -> None:
    """Parse arguments and archive the requested day's leaderboard + participants."""
    parser = argparse.ArgumentParser(
        description="Archive a day's leaderboard and participant list to CSV."
    )
    parser.add_argument(
        "game_date",
        nargs="?",
        default=datetime.datetime.now(_LA_TZ).strftime("%Y-%m-%d"),
        help="ISO date (YYYY-MM-DD); defaults to today in America/Los_Angeles.",
    )
    args = parser.parse_args()
    archive_date(args.game_date)
    archive_participants(args.game_date)


if __name__ == "__main__":
    main()
