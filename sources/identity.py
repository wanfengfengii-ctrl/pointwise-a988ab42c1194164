from lake_codec import canonical_json, decode_integer, encode_integer, load_json, require_fields, require_list
from sources.records import ObservationChange, OriginalBatch, SourceChange
from sources.validation import InvalidBatch, validate_batch


def _change_document(change: SourceChange | ObservationChange) -> dict:
    if type(change) is SourceChange:
        document = {'type': 'source', 'id': change.source_id, 'op': change.operation}
    else:
        document = {'type': 'observation', 'id': change.observation_id, 'op': change.operation}
    if change.operation == 'upsert':
        document['code'] = change.code
        if type(change) is ObservationChange:
            document['value'] = encode_integer(change.value)
    return document


def batch_document(batch: OriginalBatch) -> dict:
    validate_batch(batch)
    return {'id': batch.batch_id, 'changes': [_change_document(change) for change in batch.changes]}


def _read_change(document: object) -> SourceChange | ObservationChange:
    if type(document) is not dict:
        raise ValueError('change must be an object')
    kind, operation = document.get('type'), document.get('op')
    fields = ('type', 'id', 'op')
    if kind not in ('source', 'observation') or operation not in ('upsert', 'remove'):
        raise ValueError('unknown change type or operation')
    if operation == 'upsert':
        fields += ('code',)
        if kind == 'observation':
            fields += ('value',)
    require_fields(document, fields)
    if kind == 'source':
        return SourceChange(document['id'], operation, document.get('code'))
    value = decode_integer(document['value']) if operation == 'upsert' else None
    return ObservationChange(document['id'], operation, document.get('code'), value)


def batch_from_document(document: object) -> OriginalBatch:
    try:
        row = require_fields(document, ('id', 'changes'))
        changes = tuple(_read_change(change) for change in require_list(row['changes'], 'changes'))
    except ValueError as exc:
        raise InvalidBatch(str(exc)) from exc
    batch = OriginalBatch(row['id'], changes)
    validate_batch(batch)
    return batch


def encode_batch(batch: OriginalBatch) -> str:
    return canonical_json(batch_document(batch))


def decode_batch(text: str) -> OriginalBatch:
    try:
        return batch_from_document(load_json(text))
    except InvalidBatch:
        raise
    except ValueError as exc:
        raise InvalidBatch(str(exc)) from exc


def batch_content_key(batch: OriginalBatch) -> str:
    """Use the complete canonical text as the exact equality key."""
    return encode_batch(batch)
