from collections.abc import Iterable
from sqlite3 import Connection

from delivery.registry import ensure_delivery_schema
from lake_db import transaction
from sources.batches import record_batch
from sources.ledger import ensure_batch_schema
from sources.records import OriginalBatch
from warehouse.baselines import bind_baseline
from warehouse.snapshots import WarehouseSnapshot


def initialize_source(
    db: Connection,
    baseline_id: str,
    snapshot: WarehouseSnapshot,
    *,
    batches: Iterable[OriginalBatch] = (),
) -> str:
    """Install the baseline and optional original sample batches as one operation."""
    with transaction(db, write=True):
        ensure_batch_schema(db)
        ensure_delivery_schema(db)
        bind_baseline(db, baseline_id, snapshot)
        for batch in batches:
            record_batch(db, batch)
    return baseline_id
