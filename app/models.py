"""Tables. A card is one staff position ("treble:C4"); its FSRS memory state lives in CardState.

CardState    one row per card you've answered at least once (FSRS state as JSON + next due time)
Practice     one sitting
Attempt      one answer: card, letter given, right/wrong, ms from note shown to answer start, FSRS grade
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CardState(Base):
    __tablename__ = "card_states"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    fsrs: Mapped[dict] = mapped_column(JSON)
    due: Mapped[datetime] = mapped_column(DateTime)  # UTC


class Practice(Base):
    __tablename__ = "practices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)  # UTC
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    scope: Mapped[Optional[str]] = mapped_column(String(200))


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    practice_id: Mapped[int] = mapped_column(ForeignKey("practices.id"))
    card_id: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime)  # UTC
    answer: Mapped[Optional[str]] = mapped_column(String(2))
    correct: Mapped[bool] = mapped_column(Boolean)
    ms: Mapped[Optional[int]] = mapped_column(Integer)
    rating: Mapped[int] = mapped_column(Integer)  # FSRS: 1 Again, 2 Hard, 3 Good, 4 Easy
    engine: Mapped[Optional[str]] = mapped_column(String(20))
