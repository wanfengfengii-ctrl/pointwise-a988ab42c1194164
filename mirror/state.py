from dataclasses import dataclass
from sqlite3 import Connection, DatabaseError

from lake_codec import require_text
from lake_db import table_exists, transaction
from mirror.storage import ensure_projection_schema, write_projection
from receipts.ledger import ensure_receipt_schema
from warehouse.eligibility import validate_snapshot
from warehouse.snapshots import WarehouseSnapshot, snapshot_identity


class MirrorNotReady(LookupError):
    pass


class MirrorInitializationConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MirrorCursor:
    baseline_id: str
    baseline_key: str
    delivery_id: str | None
    position: int


def _ensure_cursor_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS mirror_cursor (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            baseline_id TEXT NOT NULL,
            baseline_key TEXT NOT NULL,
            delivery_id TEXT,
            position INTEGER NOT NULL,
            CHECK ((delivery_id IS NULL AND position = 0)
                   OR (delivery_id IS NOT NULL AND position > 0))
        )
    ''')


def read_cursor(db: Connection) -> MirrorCursor:
    if table_exists(db, 'mirror_cursor'):
        row = db.execute(
            'SELECT baseline_id, baseline_key, delivery_id, position FROM mirror_cursor WHERE singleton = 1'
        ).fetchone()
        if row is not None:
            return MirrorCursor(*row)
    raise MirrorNotReady('mirror has no installed baseline')


def initialize_mirror(db: Connection, baseline_id: str, snapshot: WarehouseSnapshot) -> MirrorCursor:
    require_text(baseline_id, 'baseline_id')
    baseline = validate_snapshot(snapshot)
    baseline_key = snapshot_identity(baseline)
    with transaction(db, write=True):
        _ensure_cursor_schema(db)
        try:
            current = read_cursor(db)
        except MirrorNotReady:
            ensure_projection_schema(db)
            ensure_receipt_schema(db)
            write_projection(db, baseline)
            db.execute(
                '''INSERT INTO mirror_cursor (singleton, baseline_id, baseline_key, delivery_id, position)
                   VALUES (1, ?, ?, NULL, 0)''',
                (baseline_id, baseline_key),
            )
            return MirrorCursor(baseline_id, baseline_key, None, 0)
        if (current.baseline_id, current.baseline_key) != (baseline_id, baseline_key):
            raise MirrorInitializationConflict(baseline_id)
        return current


def write_cursor(db: Connection, cursor: MirrorCursor) -> None:
    """Write the new prefix of the installed baseline in the caller's transaction."""
    updated = db.execute(
        '''UPDATE mirror_cursor SET delivery_id = ?, position = ?
           WHERE singleton = 1 AND baseline_id = ? AND baseline_key = ?''',
        (cursor.delivery_id, cursor.position, cursor.baseline_id, cursor.baseline_key),
    )
    if updated.rowcount != 1:
        raise DatabaseError('cannot change the installed mirror baseline')
