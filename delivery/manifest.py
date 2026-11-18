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


def _body(baseline_id: str, baseline_key: str, batches: tuple[OriginalBatch, ...]) -> dict:
    require_text(baseline_id, 'baseline_id')
    if type(baseline_key) is not str or len(baseline_key) != 64 or any(
        char not in '0123456789abcdef' for char in baseline_key
    ):
        raise ValueError('invalid baseline content key')
    if type(batches) is not tuple:
        raise ValueError('manifest batches must be an ordered tuple')
    documents = [batch_document(batch) for batch in batches]
    check_round_batches(batches)
    return {'version': 1, 'baseline_id': baseline_id, 'baseline_key': baseline_key, 'batches': documents}


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


def encode_manifest(manifest: DeliveryManifest) -> str:
    try:
        if type(manifest) is not DeliveryManifest:
            raise ValueError('expected a DeliveryManifest')
        body = _body(manifest.baseline_id, manifest.baseline_key, manifest.batches)
        if manifest.delivery_id != _delivery_id(body):
            raise ValueError('delivery identity does not match its bound content')
        return canonical_json({'delivery_id': manifest.delivery_id, **body})
    except ValueError as exc:
        raise InvalidManifest(str(exc)) from exc


def decode_manifest(text: str) -> DeliveryManifest:
    try:
        document = require_fields(load_json(text), ('version', 'delivery_id', 'baseline_id', 'baseline_key', 'batches'))
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
    except ValueError as exc:
        raise InvalidManifest(str(exc)) from exc
