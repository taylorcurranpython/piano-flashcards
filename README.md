# Piano flashcards

Note-reading flashcards for phone and laptop. A note appears on the treble or bass staff and you **say** its
letter. The app times how fast you *start* answering and uses that to schedule what you see next.

- **Cards:** every staff position is its own card: treble G3–E6 and bass C2–E4, 37 in all, so C4 and C5 are
  tracked separately. Pick treble or bass, staff notes or ledger lines, a custom range (savable), or everything.
- **Timing:** from the note being drawn to the start of your speech, not to when recognition finishes.
- **Scheduling:** [FSRS](https://github.com/open-spaced-repetition/py-fsrs) graded by speed.
  Wrong = Again, over 2s = Hard, 1–2s = Good, under 1s = Easy. A practice runs until nothing in the chosen range
  is due; missed and slow cards come back a few cards later. "Keep practicing" drills your weakest cards.
- **History:** every answer is stored with its time. Stats shows median time and accuracy per note.
- **Voice:** offline [Vosk](https://github.com/ccoreilly/vosk-browser) in the browser (~40 MB model, downloaded once
  per device). `?engine=browser` uses the browser's own speech recognition instead. A–G keys also work on a laptop.
- **Pulse mode:** an internal-pulse drill, under the Pulse tab. Clap along with a metronome (~20 s), then the click
  stops and the mic keeps judging your claps against the beat. Four beats in a row missed or more than a tenth of a
  beat off bring the click back; hold a whole silence and the next one is 5 s longer (up to 90 s). Sessions are 3–5
  minutes at 40–120 bpm. Each silence is stored per tempo (seconds held, rushing or dragging), and the app suggests
  the tempo that is weakest or least recently practiced; tempos you hold well start with a longer silence.
  Claps only, through the speaker (no headphones: the mic hears the click too). `?pulse_click=4&pulse_silent=6&pulse_minutes=1`
  shortens everything for testing.

## Run it

```bash
docker compose up -d --build        # http://localhost:8000, data in ./data/piano.db
```

Or without Docker:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Optional: `ACCESS_TOKEN=...` makes the page ask for that token once per device. Leave it unset on a private network.
`DATABASE_URL` switches from SQLite to Postgres (`pip install psycopg2-binary`).

**Phones:** browsers only allow the microphone on `https://` or `localhost`, so opening `http://<laptop-ip>:8000` on
a phone won't get the mic. Put it behind HTTPS, e.g. `tailscale serve 8000`, or Caddy with a real domain.

## Vercel

The repo also deploys to Vercel as-is (Python runtime, entrypoint in `pyproject.toml`). Connect it once and every pull
request gets its own preview URL, with production at `<project>.vercel.app`. Vercel serves `public/` from its CDN. The
voice model isn't in git, so the page loads it from the vosk-browser CDN (one-time ~40 MB per device).

One-time setup in Vercel:
1. **Add New → Project → import `piano-flashcards`**. Leave the framework preset on "Other" and the build settings empty.
2. **Storage → Neon Postgres → connect to the project.** It sets `DATABASE_URL`. Untick Preview if previews shouldn't
   write to your real history. Without `DATABASE_URL`, a deployment uses a throwaway SQLite file in `/tmp` that resets
   whenever the function restarts. That's fine for trying a preview, not for keeping history.
3. **Settings → Environment Variables:** add `ACCESS_TOKEN` for Production, since the URL is public.

Previews sit behind Vercel's login by default (Deployment Protection), which also keeps them private. The mic works
there because every Vercel URL is HTTPS.

## Tests

```bash
pip install -r requirements-dev.txt
python -m tests.test_api      # catalog, grading, FSRS scheduling, auth
python -m tests.e2e_voice     # needs Chrome: CHROME=/path/to/chrome or `playwright install chromium`
python -m tests.e2e_pulse     # pulse drill with synthetic claps (same Chrome)
```

`e2e_voice` plays recorded spoken note names (`tests/audio/`) into headless Chrome as its microphone. It runs the real
recognition, checks every grade, and checks that each recorded time matches when the speech actually started.
`e2e_pulse` plays synthetic claps as the microphone: steady (every silence held and growing), stopping when the click
stops (click back after 4 beats), and drifting late (lost after a few seconds, "drags").

## Layout

`app/drill.py` cards, grading, FSRS · `app/server.py` API (`/api/state`, `/api/practice`, `/api/answer`,
`/api/finish`) and static files · `public/` the page (VexFlow draws the staff, Vosk does voice) ·
`app/pulse.py` pulse history and tempo suggestion (`/api/pulse/*`) · `public/pulse.js`, `public/pulse-worklet.js` the
pulse drill (click scheduling and mic clap detection on one AudioContext clock).
