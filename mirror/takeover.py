from dataclasses import dataclass
from sqlite3 import Connection

from lake_db import table_exists


@dataclass(frozen=True, slots=True)
class Takeover:
    parent_delivery_id: str
    parent_position: int
    replacement_delivery_id: str


def ensure_takeover_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS mirror_takeover (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            parent_delivery_id TEXT NOT NULL,
            parent_position INTEGER NOT NULL CHECK (parent_position > 0),
            replacement_delivery_id TEXT NOT NULL
        )
    ''')


def find_takeover(db: Connection) -> Takeover | None:
    if not table_exists(db, 'mirror_takeover'):
        return None
    row = db.execute(
        '''SELECT parent_delivery_id, parent_position, replacement_delivery_id
           FROM mirror_takeover WHERE singleton = 1'''
    ).fetchone()
    return None if row is None else Takeover(*row)


def record_takeover(
    db: Connection, parent_delivery_id: str, parent_position: int, replacement_delivery_id: str
) -> None:
    """Bind the single allowed takeover within the caller's write transaction."""
    db.execute(
        '''INSERT INTO mirror_takeover (singleton, parent_delivery_id, parent_position, replacement_delivery_id)
           VALUES (1, ?, ?, ?)''',
        (parent_delivery_id, parent_position, replacement_delivery_id),
    )
