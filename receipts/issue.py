from sqlite3 import Connection

from lake_codec import canonical_json, content_digest
from receipts.ledger import Receipt, append_receipt


def issue_receipt(db: Connection, delivery_id: str, batch_id: str, position: int) -> Receipt:
    """Append a receipt to the caller's mirror transaction."""
    identity = content_digest(canonical_json([delivery_id, batch_id, position]))
    receipt = Receipt('receipt-' + identity, delivery_id, batch_id, position)
    append_receipt(db, receipt)
    return receipt
