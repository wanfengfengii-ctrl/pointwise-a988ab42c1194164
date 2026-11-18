from sqlite3 import Connection

from lake_codec import require_text
from lake_db import table_exists, transaction
from warehouse.eligibility import validate_snapshot
from warehouse.snapshots import WarehouseSnapshot, decode_snapshot, encode_snapshot


class BaselineNotFound(LookupError):
    pass


class BaselineConflict(ValueError):
    pass


def bind_baseline(db: Connection, baseline_id: str, snapshot: WarehouseSnapshot) -> None:
    """Bind a baseline within the caller's write transaction."""
    require_text(baseline_id, 'baseline_id')
    content = encode_snapshot(validate_snapshot(snapshot))
    db.execute('''
        CREATE TABLE IF NOT EXISTS baselines (
            baseline_id TEXT PRIMARY KEY NOT NULL,
            content TEXT NOT NULL
        )
    ''')
    inserted = db.execute(
        'INSERT INTO baselines VALUES (?, ?) ON CONFLICT(baseline_id) DO NOTHING',
        (baseline_id, content),
    )
    if inserted.rowcount == 0:
        row = db.execute('SELECT content FROM baselines WHERE baseline_id = ?', (baseline_id,)).fetchone()
        if row[0] != content:
            raise BaselineConflict(baseline_id)


def register_baseline(db: Connection, baseline_id: str, snapshot: WarehouseSnapshot) -> str:
    with transaction(db, write=True):
        bind_baseline(db, baseline_id, snapshot)
    return baseline_id


def read_baseline(db: Connection, baseline_id: str) -> WarehouseSnapshot:
    require_text(baseline_id, 'baseline_id')
    if not table_exists(db, 'baselines'):
        raise BaselineNotFound(baseline_id)
    row = db.execute('SELECT content FROM baselines WHERE baseline_id = ?', (baseline_id,)).fetchone()
    if row is None:
        raise BaselineNotFound(baseline_id)
    return decode_snapshot(row[0])
