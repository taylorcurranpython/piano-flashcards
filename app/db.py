import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# On Vercel only /tmp is writable, so without a Postgres URL a deployment gets a throwaway SQLite file.
DATA_DIR = Path(os.environ.get("DATA_DIR") or ("/tmp" if os.environ.get("VERCEL") else "data"))
DATABASE_URL = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
                or f"sqlite:///{DATA_DIR / 'piano.db'}")
for prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefix):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(prefix):]
        break
if DATABASE_URL.startswith("sqlite:///"):
    Path(DATABASE_URL[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session() -> Session:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
