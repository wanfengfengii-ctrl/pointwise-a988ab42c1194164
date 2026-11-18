from sqlite3 import Connection, DatabaseError

from receipts.ledger import Receipt, find_receipt_for_batch


def find_retry_receipt(
    db: Connection, delivery_id: str, batch_id: str, committed_position: int
) -> Receipt | None:
    receipt = find_receipt_for_batch(db, delivery_id, batch_id)
    if receipt is not None and receipt.position > committed_position:
        raise DatabaseError('receipt position exceeds the committed mirror prefix')
    return receipt
