from collections.abc import Iterable
from sqlite3 import Connection

from delivery.manifest import DeliveryManifest, decode_manifest, encode_manifest, freeze_manifest
from delivery.order import plan_order
from delivery.selection import select_round
from lake_codec import require_text
from lake_db import table_exists, transaction


class DeliveryNotFound(LookupError):
    pass


def ensure_delivery_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS deliveries (
            delivery_id TEXT PRIMARY KEY NOT NULL,
            content TEXT NOT NULL
        )
    ''')


def publish_delivery(db: Connection, baseline_id: str, batch_ids: Iterable[str]) -> str:
    with transaction(db, write=True):
        selection = select_round(db, baseline_id, batch_ids)
        ordered = plan_order(selection.baseline, selection.batches)
        manifest = freeze_manifest(selection.baseline_id, selection.baseline, ordered)
        content = encode_manifest(manifest)
        ensure_delivery_schema(db)
        inserted = db.execute(
            'INSERT INTO deliveries VALUES (?, ?) ON CONFLICT(delivery_id) DO NOTHING',
            (manifest.delivery_id, content),
        )
        if inserted.rowcount == 0:
            row = db.execute(
                'SELECT content FROM deliveries WHERE delivery_id = ?', (manifest.delivery_id,)
            ).fetchone()
            if row[0] != content:
                raise RuntimeError('delivery identity already binds different content')
    return manifest.delivery_id


def read_delivery(db: Connection, delivery_id: str) -> DeliveryManifest:
    require_text(delivery_id, 'delivery_id')
    if not table_exists(db, 'deliveries'):
        raise DeliveryNotFound(delivery_id)
    row = db.execute('SELECT content FROM deliveries WHERE delivery_id = ?', (delivery_id,)).fetchone()
    if row is None:
        raise DeliveryNotFound(delivery_id)
    manifest = decode_manifest(row[0])
    if manifest.delivery_id != delivery_id:
        raise RuntimeError('stored delivery has an inconsistent identity')
    return manifest
