"""Card catalog, FSRS scheduling graded by answer speed, attempt history.

A card is one staff position ("treble:C4"); the answer is the letter. Speed decides the FSRS grade:
wrong = Again, slower than GOOD_MS = Hard, slower than EASY_MS = Good, otherwise Easy.
Time is measured client-side from the note appearing to the start of the answer (speech onset).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Optional

from fsrs import Card, Rating, Scheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Attempt, CardState, Practice

EASY_MS = 1000
GOOD_MS = 2000

LETTERS = "CDEFGAB"
# Ranges from the Read Music 10x Faster deck. "Staff" = no ledger lines needed.
CLEFS = {
    "treble": {"low": ("G", 3), "high": ("E", 6), "staff": (("D", 4), ("G", 5))},
    "bass": {"low": ("C", 2), "high": ("E", 4), "staff": (("F", 2), ("B", 3))},
}

scheduler = Scheduler(enable_fuzzing=False)


def _index(letter: str, octave: int) -> int:
    return octave * 7 + LETTERS.index(letter)


def catalog() -> list[dict]:
    out = []
    for clef, r in CLEFS.items():
        lo, hi = _index(*r["low"]), _index(*r["high"])
        s_lo, s_hi = _index(*r["staff"][0]), _index(*r["staff"][1])
        for n in range(lo, hi + 1):
            letter, octave = LETTERS[n % 7], n // 7
            out.append({"id": f"{clef}:{letter}{octave}", "clef": clef, "letter": letter, "octave": octave,
                        "pos": n, "ledger": not (s_lo <= n <= s_hi)})
    return out


CATALOG = {c["id"]: c for c in catalog()}


def rating_for(correct: bool, ms: Optional[float]) -> Rating:
    if not correct:
        return Rating.Again
    if ms is None or ms > GOOD_MS:
        return Rating.Hard
    if ms > EASY_MS:
        return Rating.Good
    return Rating.Easy


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fsrs_card(row: Optional[CardState]) -> Card:
    return Card.from_dict(row.fsrs) if row and row.fsrs else Card()


def state(s: Session, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = {r.id: r for r in s.scalars(select(CardState)).all()}
    attempts: dict[str, list[Attempt]] = {}
    for a in s.scalars(select(Attempt).order_by(Attempt.id)).all():
        attempts.setdefault(a.card_id, []).append(a)
    cards = []
    for cid, c in CATALOG.items():
        row, hist = rows.get(cid), attempts.get(cid, [])
        fc = _fsrs_card(row)
        recent = [a.ms for a in hist[-10:] if a.correct and a.ms is not None]
        cards.append({
            **c,
            "new": row is None,
            "due": _utc(fc.due).isoformat(),
            "is_due": row is None or _utc(fc.due) <= now,
            "stability": fc.stability,
            "difficulty": fc.difficulty,
            "retrievability": scheduler.get_card_retrievability(fc, now) if row else 0.0,
            "attempts": len(hist),
            "correct": sum(1 for a in hist if a.correct),
            "median_ms": round(statistics.median(recent)) if recent else None,
            "last": [{"ok": a.correct, "ms": a.ms} for a in hist[-5:]],
        })
    return {"cards": cards, "easy_ms": EASY_MS, "good_ms": GOOD_MS, "now": now.isoformat()}


def start_practice(s: Session, scope: str) -> dict:
    p = Practice(started_at=datetime.utcnow(), scope=(scope or "")[:200])
    s.add(p)
    s.flush()
    return {"practice_id": p.id}


def record_answer(s: Session, practice_id: int, card_id: str, answer: Optional[str], ms: Optional[float],
                  engine: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    if card_id not in CATALOG:
        return {"error": f"unknown card {card_id}"}
    if not s.get(Practice, practice_id):
        return {"error": f"no practice {practice_id}"}
    now = now or datetime.now(timezone.utc)
    letter = (answer or "").strip().upper()[:1] or None
    correct = letter == CATALOG[card_id]["letter"]
    ms = None if ms is None else max(0, int(round(ms)))
    rating = rating_for(correct, ms)
    row = s.get(CardState, card_id)
    fc, _ = scheduler.review_card(_fsrs_card(row), rating, review_datetime=now)
    if row is None:
        row = CardState(id=card_id)
        s.add(row)
    row.fsrs, row.due = fc.to_dict(), fc.due.replace(tzinfo=None)
    s.add(Attempt(practice_id=practice_id, card_id=card_id, at=now.replace(tzinfo=None), answer=letter,
                       correct=correct, ms=ms, rating=int(rating), engine=(engine or "")[:20] or None))
    return {"ok": True, "card_id": card_id, "correct": correct, "expected": CATALOG[card_id]["letter"],
            "ms": ms, "rating": rating.name, "due": _utc(fc.due).isoformat()}


def _summary(attempts: list[Attempt]) -> dict:
    ok_ms = [a.ms for a in attempts if a.correct and a.ms is not None]
    return {"answered": len(attempts), "correct": sum(1 for a in attempts if a.correct),
            "median_ms": round(statistics.median(ok_ms)) if ok_ms else None}


def finish_practice(s: Session, practice_id: int) -> dict:
    p = s.get(Practice, practice_id)
    if not p:
        return {"error": f"no practice {practice_id}"}
    p.ended_at = p.ended_at or datetime.utcnow()
    mine = s.scalars(select(Attempt).where(Attempt.practice_id == practice_id)).all()
    return {"ok": True, "practice": _summary(mine)}
