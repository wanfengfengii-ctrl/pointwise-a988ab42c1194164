from sqlite3 import Connection

from lake_codec import require_text
from receipts.ledger import Receipt, find_receipt_by_id


class ReceiptNotFound(LookupError):
    pass


def read_receipt(db: Connection, receipt_id: str) -> Receipt:
    require_text(receipt_id, 'receipt_id')
    receipt = find_receipt_by_id(db, receipt_id)
    if receipt is None:
        raise ReceiptNotFound(receipt_id)
    return receipt
