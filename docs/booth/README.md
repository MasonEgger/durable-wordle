# Booth Mode

Booth mode is the event version of Durable Wordle. It keeps the Temporal
workflow as the complete game, but adds conference operations around it:
lead capture, madlibs, a daily leaderboard, prize outreach data, kiosk window
launching, and a second-screen display for the holographic fan.

Use the main [README](../../README.md) for the classic learning path. This file
is for operators running the booth experience.

## Running the Booth

```bash
uv sync
just booth
```

`just booth` starts the Temporal dev server, worker, and FastAPI app in
**booth** mode, then opens the game and display windows in kiosk mode. Ctrl-C
stops the stack.

Set `DURABLE_WORDLE_SHOW_MODE_TOGGLE=1` before launching if the booth start form
should expose the Random/Absurdle operator toggle.

Useful URLs:

| URL | Purpose |
|---|---|
| `http://localhost:8000` | Player game screen |
| `http://localhost:8000/display` | Second-screen display |
| `http://localhost:8000/health` | Health check |
| `http://localhost:8233` | Temporal UI |

## What Booth Mode Adds

- **Lead capture** - first name, last name, email, noun, and verb on the start
  screen.
- **Madlibs** - captured noun/verb pairs cycle on the leaderboard and display.
- **SQLite leaderboard** - score and participant data live in
  `data/leaderboard.db`; game state still lives only in Temporal workflows.
- **Second-screen display** - a display-only route for the holographic fan.
- **Kiosk launch** - `just booth` positions the player and display windows for
  the booth screens.
- **Temporal UI proxy** - booth display embeds the workflow timeline through the
  same FastAPI origin.

Booth-only Python code lives in `src/durable_wordle/booth/`. Booth-only display
templates and assets live in `templates/booth/` and `static/booth/`.

## Display Calibration

Open **http://localhost:8000/display** on the fan display. The page starts with
a calibration overlay: concentric rings, a center crosshair, and controls that
fit inside the safe circle.

Use the controls or keyboard shortcuts to align the visible circle:

- Arrow keys move the content center.
- `[` and `]` resize the circle.
- **Save & Launch** persists the calibration in `localStorage`.

The saved values map to CSS variables such as `--shift-x`, `--shift-y`, and
`--circle-w`.

## Display States

The display polls `GET /api/display-state` and switches between:

- **Attract mode** - no game is running. The display cycles a floating Ziggy,
  Temporal branding, player madlibs, and the leaderboard snapshot.
- **Game mode** - a game is running. The display shows the live Temporal
  workflow timeline extracted from an embedded Temporal UI iframe.
- **Result states** - on win or loss, the display shows the finished game state
  before returning to attract mode.

The leaderboard refreshes when the display returns from a game so the scroll can
complete without jerking back to the top.

## Leaderboard and Participants

Leaderboard data is booth-only persistence. It is not used for game state.

- Winning entries are written by `POST /guess`.
- Participant rows are recorded by `POST /play` for prize outreach, including
  people who do not win.
- Entries are scoped by `game_date` for daily resets.
- Exports use `just archive [YYYY-MM-DD]` and write CSVs under
  `data/archive/`.

Do not commit `data/`, local database files, exports, or runtime booth state.

## Operating Notes

- `POST /play` terminates other running game workflows so the booth tracks one
  active player at a time.
- Booth mode uses a 60-second inactivity timeout so abandoned games complete and
  the display can return to attract mode.
- The Temporal UI proxy lives under `/temporal-ui/` and strips frame-blocking
  headers so the display can embed the timeline.
- Use `just dev` when teaching the codebase; use `just booth` only for the full
  event experience.
