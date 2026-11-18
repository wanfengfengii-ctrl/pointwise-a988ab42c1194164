from sqlite3 import Connection, DatabaseError

from lake_db import transaction
from mirror.imports import check_delivery_binding, open_delivery
from mirror.state import MirrorCursor, read_cursor, write_cursor
from mirror.storage import load_projection, write_projection
from receipts.issue import issue_receipt
from receipts.ledger import Receipt
from receipts.retry import find_retry_receipt
from warehouse.eligibility import apply_batch


class BatchOutOfOrder(ValueError):
    pass


def accept_batch(db: Connection, package: str, batch_id: str) -> Receipt:
    manifest, requested_position = open_delivery(package, batch_id)
    with transaction(db, write=True):
        cursor = read_cursor(db)
        check_delivery_binding(manifest, cursor)
        existing = find_retry_receipt(db, manifest.delivery_id, batch_id, cursor.position)
        if existing is not None:
            if existing.position != requested_position:
                raise DatabaseError('receipt position does not match the fixed delivery order')
            return existing
        next_position = cursor.position + 1
        if requested_position != next_position:
            raise BatchOutOfOrder(f'requested position {requested_position}, expected {next_position}')

        batch = manifest.batches[next_position - 1]
        candidate = apply_batch(load_projection(db), batch)
        write_projection(db, candidate)
        write_cursor(db, MirrorCursor(cursor.baseline_id, cursor.baseline_key, manifest.delivery_id, next_position))
        return issue_receipt(db, manifest.delivery_id, batch.batch_id, next_position)
