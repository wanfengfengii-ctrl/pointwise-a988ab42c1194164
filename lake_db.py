from contextlib import contextmanager
from sqlite3 import Connection
from typing import Iterator


@contextmanager
def transaction(db: Connection, *, write: bool = False) -> Iterator[None]:
    """Own an entire SQLite transaction, including a possible COMMIT failure."""
    if db.in_transaction:
        raise RuntimeError('this operation requires an idle SQLite connection')
    db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise


def table_exists(db: Connection, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None
