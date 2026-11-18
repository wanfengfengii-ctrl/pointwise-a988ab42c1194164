from enum import Enum, auto
from sqlite3 import Connection

from lake_db import table_exists
from sources.identity import batch_content_key, decode_batch
from sources.records import OriginalBatch


class BatchBinding(Enum):
    NEW = auto()
    SAME = auto()
    CONFLICT = auto()


def ensure_batch_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS original_batches (
            batch_id TEXT PRIMARY KEY NOT NULL,
            content TEXT NOT NULL
        )
    ''')


def bind_batch(db: Connection, batch: OriginalBatch) -> BatchBinding:
    """Bind an original batch within the caller's write transaction."""
    content = batch_content_key(batch)
    inserted = db.execute(
        'INSERT INTO original_batches VALUES (?, ?) ON CONFLICT(batch_id) DO NOTHING',
        (batch.batch_id, content),
    )
    if inserted.rowcount == 1:
        return BatchBinding.NEW
    row = db.execute(
        'SELECT content FROM original_batches WHERE batch_id = ?', (batch.batch_id,)
    ).fetchone()
    return BatchBinding.SAME if row[0] == content else BatchBinding.CONFLICT


def load_batch(db: Connection, batch_id: str) -> OriginalBatch | None:
    if not table_exists(db, 'original_batches'):
        return None
    row = db.execute(
        'SELECT content FROM original_batches WHERE batch_id = ?', (batch_id,)
    ).fetchone()
    return None if row is None else decode_batch(row[0])
