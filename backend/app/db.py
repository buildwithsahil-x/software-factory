"""SQLite engine and session helpers."""

from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import SQLModel, create_engine


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(engine: Engine) -> None:
    from app import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)

