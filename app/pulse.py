"""Internal pulse drill: clap along with a click, then keep the beat when it goes quiet.

The page does the listening and judging; this keeps the history per tempo and picks what to practice.
A round is one silent stretch. It ends early (the click comes back) after OFF_RUN claps in a row more than
OFF_FRAC of a beat away from where the beat would be. Held time is the silence before that run started.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PulseRound, PulseSession

TEMPOS = list(range(40, 121, 10))
MIN_BPM, MAX_BPM = TEMPOS[0], TEMPOS[-1]
MINUTES = (3, 4, 5)
CLICK_S = 20  # click-on stretch between silent ones
BASE_SILENT_S, STEP_S, MAX_SILENT_S = 20, 5, 90
GOAL_S = 60  # holding a minute of silence = level 1.0
OFF_FRAC, OFF_RUN = 0.1, 4
RECENT = 5


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def tempo_stats(rounds: list[PulseRound], now: datetime) -> dict:
    """Stats for one tempo from its rounds (oldest first)."""
    recent = rounds[-RECENT:]
    held = [r.held_s for r in recent]
    offsets = [r.mean_offset_ms for r in recent if r.mean_offset_ms is not None]
    done_targets = [r.target_s for r in rounds[-10:] if r.completed]
    recent_s = statistics.median(held) if held else None
    last = _utc(rounds[-1].at) if rounds else None
    return {
        "rounds": len(rounds),
        "completed": sum(1 for r in rounds if r.completed),
        "best_s": round(max((r.held_s for r in rounds), default=0), 1) if rounds else None,
        "recent_s": None if recent_s is None else round(recent_s, 1),
        "offset_ms": round(statistics.mean(offsets)) if offsets else None,
        "level": 0.0 if recent_s is None else round(min(1.0, recent_s / GOAL_S), 3),
        "last_at": last.isoformat() if last else None,
        "days_since": None if last is None else round((now - last).total_seconds() / 86400, 2),
        # Tempos already held well start with a longer silence: the longest stretch completed lately.
        "start_s": int(min(MAX_SILENT_S, max([BASE_SILENT_S, *done_targets]))),
    }


def priority(st: dict) -> float:
    """Higher = practice sooner: weak, not practiced lately, few rounds. A tempo never tried scores 1."""
    weakness = 1 - st["level"]
    stale = 1.0 if st["days_since"] is None else min(st["days_since"], 14) / 14
    sparse = 1 - min(st["rounds"], 10) / 10
    return round(0.5 * weakness + 0.3 * stale + 0.2 * sparse, 4)


def suggest(stats: dict[int, dict]) -> int:
    # Ties go to the tempo nearest 80 bpm, so a first practice starts mid-range and spreads out from there.
    return max(TEMPOS, key=lambda t: (priority(stats[t]), -abs(t - 80), -t))


def _rounds_by_bpm(s: Session) -> dict[int, list[PulseRound]]:
    out: dict[int, list[PulseRound]] = {}
    for r in s.scalars(select(PulseRound).order_by(PulseRound.id)).all():
        out.setdefault(r.bpm, []).append(r)
    return out


def state(s: Session, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    by = _rounds_by_bpm(s)
    stats = {t: tempo_stats(by.get(t, []), now) for t in sorted({*TEMPOS, *by})}
    for t, st in stats.items():
        st["priority"] = priority(st)
    return {
        "tempos": [{"bpm": t, **st} for t, st in stats.items()],
        "suggested": suggest(stats),
        "minutes": list(MINUTES),
        "click_s": CLICK_S, "base_silent_s": BASE_SILENT_S, "step_s": STEP_S, "max_silent_s": MAX_SILENT_S,
        "off_frac": OFF_FRAC, "off_run": OFF_RUN,
    }


def _bpm(v) -> int:
    bpm = int(v)
    if not MIN_BPM <= bpm <= MAX_BPM:
        raise ValueError(f"bpm must be {MIN_BPM}-{MAX_BPM}")
    return bpm


def start(s: Session, bpm, minutes) -> dict:
    bpm, minutes = _bpm(bpm), max(1, min(10, int(minutes)))
    p = PulseSession(started_at=datetime.utcnow(), bpm=bpm, minutes=minutes)
    s.add(p)
    s.flush()
    st = tempo_stats(_rounds_by_bpm(s).get(bpm, []), datetime.now(timezone.utc))
    return {"session_id": p.id, "bpm": bpm, "start_s": st["start_s"]}


def record_round(s: Session, session_id: int, b: dict, now: Optional[datetime] = None) -> dict:
    p = s.get(PulseSession, session_id)
    if not p:
        return {"error": f"no pulse session {session_id}"}
    opt = lambda k: None if b.get(k) is None else round(float(b[k]), 1)  # noqa: E731
    target = max(0.0, float(b["target_s"]))
    completed = bool(b.get("completed"))
    held = target if completed else max(0.0, min(target, float(b["held_s"])))
    r = PulseRound(session_id=p.id, bpm=p.bpm, at=(now or datetime.now(timezone.utc)).replace(tzinfo=None),
                   target_s=round(target, 1), held_s=round(held, 1), completed=completed,
                   claps=max(0, int(b.get("claps") or 0)), mean_offset_ms=opt("mean_offset_ms"),
                   mean_abs_ms=opt("mean_abs_ms"))
    s.add(r)
    s.flush()
    return {"ok": True, "round_id": r.id, "held_s": r.held_s, "completed": r.completed}


def finish(s: Session, session_id: int) -> dict:
    p = s.get(PulseSession, session_id)
    if not p:
        return {"error": f"no pulse session {session_id}"}
    p.ended_at = p.ended_at or datetime.utcnow()
    mine = s.scalars(select(PulseRound).where(PulseRound.session_id == session_id).order_by(PulseRound.id)).all()
    offs = [r.mean_offset_ms for r in mine if r.mean_offset_ms is not None]
    return {"ok": True, "session": {
        "bpm": p.bpm, "rounds": len(mine), "completed": sum(r.completed for r in mine),
        "best_s": max((r.held_s for r in mine), default=None),
        "offset_ms": round(statistics.mean(offs)) if offs else None}}
