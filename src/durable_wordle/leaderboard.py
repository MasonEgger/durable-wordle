# ABOUTME: Leaderboard persistence backed by SQLite.
# Entries are scoped by game_date for daily resets; all entries are retained for
# prize outreach via get_entries_for_date with include_email=True.
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "leaderboard.db"
TOP_N: int = 25

_schema_ready = False


def format_elapsed(seconds: int) -> str:
    """Format an elapsed duration as ``H:MM:SS``.

    Single source of truth for elapsed-time display, used by both leaderboard
    entries and the share card.

    :param seconds: Elapsed time in whole seconds.
    :returns: Human-readable elapsed time string.
    """
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name   TEXT    NOT NULL,
                email         TEXT    NOT NULL DEFAULT '',
                guesses       INTEGER NOT NULL,
                elapsed_seconds INTEGER NOT NULL,
                madlib_noun   TEXT    NOT NULL DEFAULT '',
                madlib_verb   TEXT    NOT NULL DEFAULT '',
                submitted_at  TEXT    NOT NULL,
                game_date     TEXT    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game_date ON entries(game_date)")
    _schema_ready = True


@dataclass
class LeaderboardEntry:
    """A single leaderboard entry.

    :param player_name: Display name for the player.
    :param email: Player email for prize outreach (not shown publicly).
    :param guesses: Number of guesses used.
    :param elapsed_seconds: Seconds from game start to win.
    :param madlib_noun: Noun from the player's madlib.
    :param madlib_verb: Past-tense verb from the player's madlib.
    :param submitted_at: ISO-format UTC timestamp of submission.
    :param game_date: ISO date string (YYYY-MM-DD) of the game day.
    """

    player_name: str
    email: str
    guesses: int
    elapsed_seconds: int
    madlib_noun: str
    madlib_verb: str
    submitted_at: str
    game_date: str

    @property
    def elapsed_formatted(self) -> str:
        """Format elapsed time as H:MM:SS.

        :returns: Human-readable elapsed time string.
        """
        return format_elapsed(self.elapsed_seconds)


def add_entry(
    player_name: str,
    email: str,
    guesses: int,
    started_at: datetime | None,
    madlib_noun: str,
    madlib_verb: str,
    game_date: str,
) -> list[LeaderboardEntry]:
    """Insert a new entry and return the updated top entries for that day.

    :param player_name: Display name for the player.
    :param email: Player email for prize outreach.
    :param guesses: Number of guesses used.
    :param started_at: When the game started (used to compute elapsed time).
    :param madlib_noun: Noun from the player's madlib.
    :param madlib_verb: Past-tense verb from the player's madlib.
    :param game_date: ISO date string of the game day (YYYY-MM-DD).
    :returns: Top entries for the day after insertion.
    """
    _ensure_schema()
    now = datetime.now(UTC)
    if started_at is not None:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        elapsed = max(0, int((now - started_at).total_seconds()))
    else:
        elapsed = 0

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO entries
                (player_name, email, guesses, elapsed_seconds,
                 madlib_noun, madlib_verb, submitted_at, game_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player_name or "Anonymous",
                email or "",
                guesses,
                elapsed,
                madlib_noun,
                madlib_verb,
                now.isoformat(),
                game_date,
            ),
        )

    return get_top_entries_for_date(game_date)


_SEED_PLAYER = "Shy Ruparel"
_SEED_EMAIL = "test@test.com"  # placeholder — keep a real address out of the repo
_SEED_NOUN = "CODE"
_SEED_VERB = "FAILS"
_SEED_ELAPSED = 540  # 9:00 — last-place floor entry


def _ensure_seed_entry(game_date: str) -> None:
    """Insert the default last-place entry for game_date if it doesn't exist yet.

    :param game_date: ISO date string (YYYY-MM-DD) to seed.
    """
    _ensure_schema()
    with _connect() as conn:
        count: int = conn.execute(
            """
            SELECT COUNT(*) FROM entries
            WHERE  game_date    = ?
              AND  player_name  = ?
              AND  madlib_noun  = ?
              AND  madlib_verb  = ?
            """,
            (game_date, _SEED_PLAYER, _SEED_NOUN, _SEED_VERB),
        ).fetchone()[0]
        if count == 0:
            conn.execute(
                """
                INSERT INTO entries
                    (player_name, email, guesses, elapsed_seconds,
                     madlib_noun, madlib_verb, submitted_at, game_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _SEED_PLAYER,
                    _SEED_EMAIL,
                    6,
                    _SEED_ELAPSED,
                    _SEED_NOUN,
                    _SEED_VERB,
                    datetime(2000, 1, 1, tzinfo=UTC).isoformat(),
                    game_date,
                ),
            )


def get_top_entries_for_date(game_date: str, n: int = TOP_N) -> list[LeaderboardEntry]:
    """Return the top N entries for a given game day.

    Sorted by fewest guesses then fastest time.

    :param game_date: ISO date string (YYYY-MM-DD).
    :param n: Maximum number of entries to return.
    :returns: Top N leaderboard entries for the day.
    """
    _ensure_seed_entry(game_date)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT player_name, email, guesses, elapsed_seconds,
                   madlib_noun, madlib_verb, submitted_at, game_date
            FROM   entries
            WHERE  game_date = ?
            ORDER  BY guesses ASC, elapsed_seconds ASC
            LIMIT  ?
            """,
            (game_date, n),
        ).fetchall()
    return [LeaderboardEntry(**dict(row)) for row in rows]


def get_entries_for_date(game_date: str) -> list[LeaderboardEntry]:
    """Return all entries for a given game day, including emails.

    Use this for prize outreach — emails are not exposed to the public
    leaderboard template.

    :param game_date: ISO date string (YYYY-MM-DD).
    :returns: All entries for the day, sorted by rank.
    """
    _ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT player_name, email, guesses, elapsed_seconds,
                   madlib_noun, madlib_verb, submitted_at, game_date
            FROM   entries
            WHERE  game_date = ?
            ORDER  BY guesses ASC, elapsed_seconds ASC
            """,
            (game_date,),
        ).fetchall()
    return [LeaderboardEntry(**dict(row)) for row in rows]


def get_recent_win(
    game_date: str,
    now: datetime | None = None,
    within_seconds: int = 15,
) -> tuple[LeaderboardEntry, int] | None:
    """Return the most recently submitted entry and its rank, if it is recent.

    Used by the display's win celebration: returns the newest submission for the
    day only when it landed within ``within_seconds`` so the celebration fires
    once per real win and not for stale or seed entries. Rank is the 1-based
    position in the day's standings (fewest guesses, then fastest time).

    :param game_date: ISO date string (YYYY-MM-DD).
    :param now: Reference time for the recency window (defaults to ``now(UTC)``).
    :param within_seconds: Maximum age of the submission to qualify as recent.
    :returns: A ``(entry, rank)`` tuple for the most recent win, or ``None`` if
        there is no qualifying submission.
    """
    current = now or datetime.now(UTC)
    entries = get_entries_for_date(game_date)  # already ranked (guesses, time)
    if not entries:
        return None

    most_recent_index = max(
        range(len(entries)), key=lambda index: entries[index].submitted_at
    )
    most_recent = entries[most_recent_index]

    submitted = datetime.fromisoformat(most_recent.submitted_at)
    if submitted.tzinfo is None:
        submitted = submitted.replace(tzinfo=UTC)
    if (current - submitted).total_seconds() > within_seconds:
        return None

    return most_recent, most_recent_index + 1


def get_madlib_pairs(entries: list[LeaderboardEntry]) -> list[list[str]]:
    """Return unique [noun, verb] pairs from entries that have madlib data.

    :param entries: Leaderboard entries to scan.
    :returns: Deduplicated list of [noun, verb] pairs.
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
