from sqlite3 import Connection

from lake_codec import require_text
from lake_db import transaction
from sources.ledger import BatchBinding, bind_batch, ensure_batch_schema, load_batch
from sources.records import OriginalBatch


class BatchConflict(ValueError):
    pass


class BatchNotFound(LookupError):
    pass


def record_batch(db: Connection, batch: OriginalBatch) -> str:
    """Register a batch in an existing source write transaction."""
    if bind_batch(db, batch) is BatchBinding.CONFLICT:
        raise BatchConflict(batch.batch_id)
    return batch.batch_id


def register_batch(db: Connection, batch: OriginalBatch) -> str:
    with transaction(db, write=True):
        ensure_batch_schema(db)
        return record_batch(db, batch)


def read_batch(db: Connection, batch_id: str) -> OriginalBatch:
    require_text(batch_id, 'batch_id')
    batch = load_batch(db, batch_id)
    if batch is None:
        raise BatchNotFound(batch_id)
    return batch
