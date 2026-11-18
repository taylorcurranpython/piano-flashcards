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

## Tests

```bash
pip install -r requirements-dev.txt
python -m tests.test_api      # catalog, grading, FSRS scheduling, auth
python -m tests.e2e_voice     # needs Chrome: CHROME=/path/to/chrome or `playwright install chromium`
```

`e2e_voice` plays recorded spoken note names (`tests/audio/`) into headless Chrome as its microphone. It runs the real
recognition, checks every grade, and checks that each recorded time matches when the speech actually started.

## Layout

`app/drill.py` cards, grading, FSRS · `app/server.py` API (`/api/state`, `/api/practice`, `/api/answer`,
`/api/finish`) and static files · `public/` the page (VexFlow draws the staff, Vosk does voice).
