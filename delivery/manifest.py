from collections.abc import Iterable
from dataclasses import dataclass

from delivery.selection import check_round_batches
from lake_codec import canonical_json, content_digest, load_json, require_fields, require_list, require_text
from sources.identity import batch_document, batch_from_document
from sources.records import OriginalBatch
from warehouse.snapshots import WarehouseSnapshot, snapshot_identity


class InvalidManifest(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryManifest:
    delivery_id: str
    baseline_id: str
    baseline_key: str
    batches: tuple[OriginalBatch, ...]


@dataclass(frozen=True, slots=True)
class ReplacementManifest:
    """A continuation delivery bound to a signed prefix of one normal parent delivery."""

    delivery_id: str
    baseline_id: str
    baseline_key: str
    parent_delivery_id: str
    parent_position: int
    prefix_key: str
    batches: tuple[OriginalBatch, ...]


def _content_key(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(
        char not in '0123456789abcdef' for char in value
    ):
        raise ValueError(f'invalid {field} content key')
    return value


def _body(baseline_id: str, baseline_key: str, batches: tuple[OriginalBatch, ...]) -> dict:
    require_text(baseline_id, 'baseline_id')
    _content_key(baseline_key, 'baseline')
    if type(batches) is not tuple:
        raise ValueError('manifest batches must be an ordered tuple')
    documents = [batch_document(batch) for batch in batches]
    check_round_batches(batches)
    return {'version': 1, 'baseline_id': baseline_id, 'baseline_key': baseline_key, 'batches': documents}


def _replacement_body(
    baseline_id: str,
    baseline_key: str,
    parent_delivery_id: str,
    parent_position: int,
    prefix_key: str,
    batches: tuple[OriginalBatch, ...],
) -> dict:
    require_text(baseline_id, 'baseline_id')
    _content_key(baseline_key, 'baseline')
    require_text(parent_delivery_id, 'parent_delivery_id')
    if type(parent_position) is not int or parent_position < 1:
        raise ValueError('parent position must be a positive integer')
    _content_key(prefix_key, 'prefix state')
    if type(batches) is not tuple:
        raise ValueError('manifest batches must be an ordered tuple')
    documents = [batch_document(batch) for batch in batches]
    check_round_batches(batches)
    return {
        'version': 2,
        'baseline_id': baseline_id,
        'baseline_key': baseline_key,
        'parent_delivery_id': parent_delivery_id,
        'parent_position': parent_position,
        'prefix_key': prefix_key,
        'batches': documents,
    }


def _delivery_id(body: dict) -> str:
    return 'delivery-' + content_digest(canonical_json(body))


def freeze_manifest(
    baseline_id: str, baseline: WarehouseSnapshot, batches: Iterable[OriginalBatch]
) -> DeliveryManifest:
    """Freeze the supplied order. The publisher establishes its eligibility."""
    try:
        ordered = tuple(batches)
        baseline_key = snapshot_identity(baseline)
        body = _body(baseline_id, baseline_key, ordered)
        return DeliveryManifest(_delivery_id(body), baseline_id, baseline_key, ordered)
    except (TypeError, ValueError) as exc:
        raise InvalidManifest(str(exc)) from exc


def freeze_replacement(
    baseline_id: str,
    baseline: WarehouseSnapshot,
    parent_delivery_id: str,
    parent_position: int,
    prefix_state: WarehouseSnapshot,
    batches: Iterable[OriginalBatch],
) -> ReplacementManifest:
    """Freeze the planned replacement order. The publisher establishes its eligibility."""
    try:
        ordered = tuple(batches)
        baseline_key = snapshot_identity(baseline)
        prefix_key = snapshot_identity(prefix_state)
        body = _replacement_body(
            baseline_id, baseline_key, parent_delivery_id, parent_position, prefix_key, ordered
        )
        return ReplacementManifest(
            _delivery_id(body),
            baseline_id,
            baseline_key,
            parent_delivery_id,
            parent_position,
            prefix_key,
            ordered,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidManifest(str(exc)) from exc


def encode_manifest(manifest: DeliveryManifest | ReplacementManifest) -> str:
    try:
        if type(manifest) is DeliveryManifest:
            body = _body(manifest.baseline_id, manifest.baseline_key, manifest.batches)
        elif type(manifest) is ReplacementManifest:
            body = _replacement_body(
                manifest.baseline_id,
                manifest.baseline_key,
                manifest.parent_delivery_id,
                manifest.parent_position,
                manifest.prefix_key,
                manifest.batches,
            )
        else:
            raise ValueError('expected a DeliveryManifest')
        if manifest.delivery_id != _delivery_id(body):
            raise ValueError('delivery identity does not match its bound content')
        return canonical_json({'delivery_id': manifest.delivery_id, **body})
    except ValueError as exc:
        raise InvalidManifest(str(exc)) from exc


def _decode_normal(document: dict) -> DeliveryManifest:
    require_fields(document, ('version', 'delivery_id', 'baseline_id', 'baseline_key', 'batches'))
    if type(document['version']) is not int or document['version'] != 1:
        raise ValueError('unsupported manifest version')
    items = require_list(document['batches'], 'batches')
    if not 1 <= len(items) <= 8:
        raise ValueError('a manifest needs one to eight original batches')
    batches = tuple(batch_from_document(item) for item in items)
    body = _body(document['baseline_id'], document['baseline_key'], batches)
    expected_id = _delivery_id(body)
    if document['delivery_id'] != expected_id:
        raise ValueError('delivery identity does not match its bound content')
    return DeliveryManifest(expected_id, document['baseline_id'], document['baseline_key'], batches)


def _decode_replacement(document: dict) -> ReplacementManifest:
    require_fields(
        document,
        (
            'version',
            'delivery_id',
            'baseline_id',
            'baseline_key',
            'parent_delivery_id',
            'parent_position',
            'prefix_key',
            'batches',
        ),
    )
    if type(document['version']) is not int or document['version'] != 2:
        raise ValueError('unsupported manifest version')
    items = require_list(document['batches'], 'batches')
    if not 1 <= len(items) <= 8:
        raise ValueError('a manifest needs one to eight original batches')
    batches = tuple(batch_from_document(item) for item in items)
    body = _replacement_body(
        document['baseline_id'],
        document['baseline_key'],
        document['parent_delivery_id'],
        document['parent_position'],
        document['prefix_key'],
        batches,
    )
    expected_id = _delivery_id(body)
    if document['delivery_id'] != expected_id:
        raise ValueError('delivery identity does not match its bound content')
    return ReplacementManifest(
        expected_id,
        document['baseline_id'],
        document['baseline_key'],
        document['parent_delivery_id'],
        document['parent_position'],
        document['prefix_key'],
        batches,
    )


def decode_manifest(text: str) -> DeliveryManifest | ReplacementManifest:
    try:
        document = load_json(text)
        if type(document) is not dict:
            raise ValueError('expected a manifest object')
        if document.get('version') == 2:
            return _decode_replacement(document)
        return _decode_normal(document)
    except ValueError as exc:
        raise InvalidManifest(str(exc)) from exc
