from lake_codec import require_text
from sources.records import ObservationChange, OriginalBatch, SourceChange


class InvalidBatch(ValueError):
    pass


def _validate_change(change: SourceChange | ObservationChange) -> None:
    if type(change) is SourceChange:
        require_text(change.source_id, 'source_id')
    elif type(change) is ObservationChange:
        require_text(change.observation_id, 'observation_id')
    else:
        raise ValueError('change must be a source or observation change')
    if type(change.operation) is not str or change.operation not in ('upsert', 'remove'):
        raise ValueError('change operation must be upsert or remove')
    if change.operation == 'remove':
        if change.code is not None or (type(change) is ObservationChange and change.value is not None):
            raise ValueError('a removal carries only the target identity')
        return
    require_text(change.code, 'code')
    if type(change) is ObservationChange and type(change.value) is not int:
        raise ValueError('observation value must be an integer')


def validate_batch(batch: OriginalBatch) -> None:
    try:
        if type(batch) is not OriginalBatch:
            raise ValueError('expected an OriginalBatch')
        require_text(batch.batch_id, 'batch_id')
        if type(batch.changes) is not tuple:
            raise ValueError('changes must be an ordered tuple')
        for change in batch.changes:
            _validate_change(change)
    except ValueError as exc:
        raise InvalidBatch(str(exc)) from exc
