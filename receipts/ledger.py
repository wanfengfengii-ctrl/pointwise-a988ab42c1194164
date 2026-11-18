from dataclasses import dataclass
from sqlite3 import Connection

from lake_db import table_exists


@dataclass(frozen=True, slots=True)
class Receipt:
    receipt_id: str
    delivery_id: str
    batch_id: str
    position: int


def ensure_receipt_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS mirror_receipts (
            receipt_id TEXT PRIMARY KEY NOT NULL,
            delivery_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position > 0),
            UNIQUE (delivery_id, batch_id),
            UNIQUE (delivery_id, position)
        )
    ''')


def append_receipt(db: Connection, receipt: Receipt) -> None:
    db.execute(
        'INSERT INTO mirror_receipts (receipt_id, delivery_id, batch_id, position) VALUES (?, ?, ?, ?)',
        (receipt.receipt_id, receipt.delivery_id, receipt.batch_id, receipt.position),
    )


def find_receipt_by_id(db: Connection, receipt_id: str) -> Receipt | None:
    if not table_exists(db, 'mirror_receipts'):
        return None
    row = db.execute(
        'SELECT receipt_id, delivery_id, batch_id, position FROM mirror_receipts WHERE receipt_id = ?',
        (receipt_id,),
    ).fetchone()
    return None if row is None else Receipt(*row)


def find_receipt_for_batch(db: Connection, delivery_id: str, batch_id: str) -> Receipt | None:
    if not table_exists(db, 'mirror_receipts'):
        return None
    row = db.execute(
        '''SELECT receipt_id, delivery_id, batch_id, position
           FROM mirror_receipts WHERE delivery_id = ? AND batch_id = ?''',
        (delivery_id, batch_id),
    ).fetchone()
    return None if row is None else Receipt(*row)
