from sqlite3 import Connection, DatabaseError

from delivery.manifest import ReplacementManifest
from lake_db import transaction
from mirror.imports import DeliveryBindingMismatch, check_delivery_binding, open_delivery
from mirror.state import MirrorCursor, read_cursor, write_cursor
from mirror.storage import load_projection, write_projection
from mirror.takeover import ensure_takeover_schema, find_takeover, record_takeover
from receipts.issue import issue_receipt
from receipts.ledger import Receipt, find_receipt_for_batch
from receipts.retry import find_retry_receipt
from warehouse.eligibility import apply_batch
from warehouse.snapshots import snapshot_identity


class BatchOutOfOrder(ValueError):
    pass


def _receive_next(
    db: Connection, manifest, batch_id: str, requested_position: int, cursor: MirrorCursor
) -> Receipt:
    """Receive within the active delivery: retry a signed batch or append the next one."""
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


def _reclaim_parent_receipt(
    db: Connection, manifest, batch_id: str, requested_position: int, cursor: MirrorCursor
) -> Receipt:
    """Return a still-valid parent receipt after a takeover, or refuse the package."""
    if (manifest.baseline_id, manifest.baseline_key) != (cursor.baseline_id, cursor.baseline_key):
        raise DeliveryBindingMismatch('delivery baseline does not match the installed mirror baseline')
    receipt = find_receipt_for_batch(db, manifest.delivery_id, batch_id)
    if receipt is None:
        raise DeliveryBindingMismatch('mirror is already bound to a different delivery')
    if receipt.position != requested_position:
        raise DatabaseError('receipt position does not match the fixed delivery order')
    return receipt


def _take_over(
    db: Connection, manifest: ReplacementManifest, requested_position: int, cursor: MirrorCursor
) -> Receipt:
    """Bind the single allowed replacement and commit its first batch atomically."""
    if (manifest.baseline_id, manifest.baseline_key) != (cursor.baseline_id, cursor.baseline_key):
        raise DeliveryBindingMismatch('replacement baseline does not match the installed mirror baseline')
    ensure_takeover_schema(db)
    if find_takeover(db) is not None:
        raise DeliveryBindingMismatch('mirror already took over a replacement delivery')
    if (cursor.delivery_id, cursor.position) != (manifest.parent_delivery_id, manifest.parent_position):
        raise DeliveryBindingMismatch('mirror prefix does not match the replacement parent binding')
    projection = load_projection(db)
    if snapshot_identity(projection) != manifest.prefix_key:
        raise DeliveryBindingMismatch('mirror state does not match the replacement prefix state')
    if requested_position != 1:
        raise BatchOutOfOrder(f'requested position {requested_position}, expected 1')

    batch = manifest.batches[0]
    candidate = apply_batch(projection, batch)
    write_projection(db, candidate)
    record_takeover(db, manifest.parent_delivery_id, manifest.parent_position, manifest.delivery_id)
    write_cursor(db, MirrorCursor(cursor.baseline_id, cursor.baseline_key, manifest.delivery_id, 1))
    return issue_receipt(db, manifest.delivery_id, batch.batch_id, 1)


def accept_batch(db: Connection, package: str, batch_id: str) -> Receipt:
    manifest, requested_position = open_delivery(package, batch_id)
    with transaction(db, write=True):
        cursor = read_cursor(db)
        if type(manifest) is ReplacementManifest:
            if cursor.delivery_id != manifest.delivery_id:
                return _take_over(db, manifest, requested_position, cursor)
        elif cursor.delivery_id is not None and cursor.delivery_id != manifest.delivery_id:
            return _reclaim_parent_receipt(db, manifest, batch_id, requested_position, cursor)
        return _receive_next(db, manifest, batch_id, requested_position, cursor)
