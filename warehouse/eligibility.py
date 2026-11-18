from collections.abc import Iterable

from sources.records import ObservationChange, OriginalBatch, SourceChange
from sources.validation import validate_batch
from warehouse.catalog import apply_source_changes, sources_by_code
from warehouse.facts import apply_observation_changes, referenced_codes
from warehouse.snapshots import WarehouseSnapshot, make_snapshot


class MissingEffectiveSource(ValueError):
    def __init__(self, codes: Iterable[str]):
        self.codes = tuple(sorted(codes))
        super().__init__(f'no effective source for codes {self.codes!r}')


def validate_snapshot(snapshot: WarehouseSnapshot) -> WarehouseSnapshot:
    if type(snapshot) is not WarehouseSnapshot:
        raise ValueError('expected a WarehouseSnapshot')
    canonical = make_snapshot(snapshot.sources, snapshot.observations)
    missing = referenced_codes(canonical.observations) - sources_by_code(canonical.sources).keys()
    if missing:
        raise MissingEffectiveSource(missing)
    return canonical


def apply_batch(snapshot: WarehouseSnapshot, batch: OriginalBatch) -> WarehouseSnapshot:
    """Compute one atomic batch result from a structurally valid snapshot."""
    validate_batch(batch)
    source_changes = (change for change in batch.changes if type(change) is SourceChange)
    fact_changes = (change for change in batch.changes if type(change) is ObservationChange)
    candidate = WarehouseSnapshot(
        apply_source_changes(snapshot.sources, source_changes),
        apply_observation_changes(snapshot.observations, fact_changes),
    )
    return validate_snapshot(candidate)
