from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from sqlite3 import Connection

from lake_codec import require_text
from sources.batches import read_batch
from sources.records import OriginalBatch, SourceChange
from warehouse.baselines import read_baseline
from warehouse.snapshots import WarehouseSnapshot


class InvalidSelection(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RoundSelection:
    baseline_id: str
    baseline: WarehouseSnapshot
    batches: tuple[OriginalBatch, ...]


def _check_ids(batch_ids: tuple[str, ...]) -> None:
    if not 1 <= len(batch_ids) <= 8:
        raise InvalidSelection('a round needs one to eight original batches')
    try:
        for batch_id in batch_ids:
            require_text(batch_id, 'batch_id')
    except ValueError as exc:
        raise InvalidSelection(str(exc)) from exc
    if len(set(batch_ids)) != len(batch_ids):
        raise InvalidSelection('an original batch may only appear once in a round')


def check_round_batches(batches: tuple[OriginalBatch, ...]) -> None:
    """Check the selection rules for already validated original batches."""
    _check_ids(tuple(batch.batch_id for batch in batches))
    owners: dict[tuple[str, str], str] = {}
    for batch in batches:
        for change in batch.changes:
            if type(change) is SourceChange:
                target = ('source', change.source_id)
            else:
                target = ('observation', change.observation_id)
            owner = owners.setdefault(target, batch.batch_id)
            if owner != batch.batch_id:
                raise InvalidSelection(f'{target!r} is changed by both {owner!r} and {batch.batch_id!r}')


def select_round(db: Connection, baseline_id: str, batch_ids: Iterable[str]) -> RoundSelection:
    if isinstance(batch_ids, (str, bytes)):
        raise InvalidSelection('batch_ids must be a collection of original batch identities')
    try:
        selected_ids = tuple(islice(batch_ids, 9))
    except TypeError as exc:
        raise InvalidSelection('batch_ids must be iterable') from exc
    _check_ids(selected_ids)
    baseline = read_baseline(db, baseline_id)
    batches = tuple(read_batch(db, batch_id) for batch_id in selected_ids)
    check_round_batches(batches)
    return RoundSelection(baseline_id, baseline, batches)


def select_replacement_batches(
    db: Connection, signed_prefix: tuple[OriginalBatch, ...], batch_ids: Iterable[str]
) -> tuple[OriginalBatch, ...]:
    """Select a replacement round, refusing the parent's already signed batch identities."""
    if isinstance(batch_ids, (str, bytes)):
        raise InvalidSelection('batch_ids must be a collection of original batch identities')
    try:
        selected_ids = tuple(islice(batch_ids, 9))
    except TypeError as exc:
        raise InvalidSelection('batch_ids must be iterable') from exc
    _check_ids(selected_ids)
    signed_ids = {batch.batch_id for batch in signed_prefix}
    reused = [batch_id for batch_id in selected_ids if batch_id in signed_ids]
    if reused:
        raise InvalidSelection(f'signed parent batches cannot be reused: {reused!r}')
    batches = tuple(read_batch(db, batch_id) for batch_id in selected_ids)
    check_round_batches(batches)
    return batches
