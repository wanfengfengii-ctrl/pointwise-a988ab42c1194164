from collections.abc import Iterable

from sources.records import OriginalBatch
from warehouse.eligibility import MissingEffectiveSource, apply_batch, validate_snapshot
from warehouse.snapshots import WarehouseSnapshot


class NoLegalOrder(ValueError):
    pass


def _search(
    snapshot: WarehouseSnapshot, remaining: tuple[OriginalBatch, ...]
) -> tuple[OriginalBatch, ...] | None:
    if not remaining:
        return ()
    for index, batch in enumerate(remaining):
        try:
            next_snapshot = apply_batch(snapshot, batch)
        except MissingEffectiveSource:
            continue
        rest = _search(next_snapshot, remaining[:index] + remaining[index + 1:])
        if rest is not None:
            return (batch, *rest)
    return None


def plan_order(
    baseline: WarehouseSnapshot, batches: Iterable[OriginalBatch]
) -> tuple[OriginalBatch, ...]:
    """Find the first complete legal order for a validated round selection."""
    initial = validate_snapshot(baseline)
    candidates = tuple(sorted(batches, key=lambda batch: batch.batch_id))
    result = _search(initial, candidates)
    if result is None:
        raise NoLegalOrder('no complete legal original-batch order')
    return result
