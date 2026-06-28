# ABOUTME: Leaderboard persistence backed by a local JSON file.
# Entries survive restarts; sorted by fewest guesses then fastest time.
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

LEADERBOARD_FILE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "leaderboard.json"
)


@dataclass
class LeaderboardEntry:
    """A single leaderboard entry.

    :param player_name: Display name for the player.
    :param guesses: Number of guesses used.
    :param elapsed_seconds: Seconds from game start to win.
    :param madlib_noun: Noun from the player's madlib.
    :param madlib_verb: Past-tense verb from the player's madlib.
    :param submitted_at: ISO-format UTC timestamp of submission.
    """

    player_name: str
    guesses: int
    elapsed_seconds: int
    madlib_noun: str
    madlib_verb: str
    submitted_at: str

    @property
    def elapsed_formatted(self) -> str:
        """Format elapsed time as H:MM:SS.

        :returns: Human-readable elapsed time string.
        """
        h = self.elapsed_seconds // 3600
        m = (self.elapsed_seconds % 3600) // 60
        s = self.elapsed_seconds % 60
        return f"{h}:{m:02d}:{s:02d}"


def _sort_key(entry: LeaderboardEntry) -> tuple[int, int]:
    return (entry.guesses, entry.elapsed_seconds)


def load_entries() -> list[LeaderboardEntry]:
    """Load all leaderboard entries from disk.

    :returns: List of entries, or empty list if file is missing/corrupt.
    """
    if not LEADERBOARD_FILE.exists():
        return []
    try:
        data: list[dict[str, object]] = json.loads(LEADERBOARD_FILE.read_text())
        return [LeaderboardEntry(**entry) for entry in data]  # type: ignore[arg-type]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _save_entries(entries: list[LeaderboardEntry]) -> None:
    LEADERBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEADERBOARD_FILE.write_text(json.dumps([asdict(e) for e in entries], indent=2))


def add_entry(
    player_name: str,
    guesses: int,
    started_at: datetime | None,
    madlib_noun: str,
    madlib_verb: str,
) -> list[LeaderboardEntry]:
    """Append a new entry and persist the updated list.

    :param player_name: Display name for the player.
    :param guesses: Number of guesses used.
    :param started_at: When the game started (used to compute elapsed time).
    :param madlib_noun: Noun from the player's madlib.
    :param madlib_verb: Past-tense verb from the player's madlib.
    :returns: Full sorted entry list after insertion.
    """
    now = datetime.now(UTC)
    if started_at is not None:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        elapsed = max(0, int((now - started_at).total_seconds()))
    else:
        elapsed = 0

    entry = LeaderboardEntry(
        player_name=player_name or "Anonymous",
        guesses=guesses,
        elapsed_seconds=elapsed,
        madlib_noun=madlib_noun,
        madlib_verb=madlib_verb,
        submitted_at=now.isoformat(),
    )
    entries = load_entries()
    entries.append(entry)
    entries.sort(key=_sort_key)
    _save_entries(entries)
    return entries


def get_top_entries(n: int = 10) -> list[LeaderboardEntry]:
    """Return the top N entries sorted by fewest guesses then fastest time.

    :param n: Maximum number of entries to return.
    :returns: Top N leaderboard entries.
    """
    entries = load_entries()
    entries.sort(key=_sort_key)
    return entries[:n]


def get_madlib_pairs(entries: list[LeaderboardEntry]) -> list[list[str]]:
    """Return unique [noun, verb] pairs from entries that have madlib data.

    :param entries: Leaderboard entries to scan.
    :returns: Deduplicated list of [noun, verb] pairs, newest first.
    """
    seen: set[tuple[str, str]] = set()
    pairs: list[list[str]] = []
    for entry in reversed(entries):
        if entry.madlib_noun and entry.madlib_verb:
            key = (entry.madlib_noun, entry.madlib_verb)
            if key not in seen:
                seen.add(key)
                pairs.append([entry.madlib_noun, entry.madlib_verb])
    pairs.reverse()
    return pairs
