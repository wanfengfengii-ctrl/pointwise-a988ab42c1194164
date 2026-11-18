from delivery.manifest import DeliveryManifest, ReplacementManifest, decode_manifest
from lake_codec import require_text
from mirror.state import MirrorCursor


class DeliveryBindingMismatch(ValueError):
    pass


class BatchOutsideDelivery(LookupError):
    pass


def open_delivery(package: str, batch_id: str) -> tuple[DeliveryManifest | ReplacementManifest, int]:
    require_text(batch_id, 'batch_id')
    manifest = decode_manifest(package)
    for position, batch in enumerate(manifest.batches, start=1):
        if batch.batch_id == batch_id:
            return manifest, position
    raise BatchOutsideDelivery(batch_id)


def check_delivery_binding(manifest: DeliveryManifest | ReplacementManifest, cursor: MirrorCursor) -> None:
    if (manifest.baseline_id, manifest.baseline_key) != (cursor.baseline_id, cursor.baseline_key):
        raise DeliveryBindingMismatch('delivery baseline does not match the installed mirror baseline')
    if cursor.delivery_id is not None and cursor.delivery_id != manifest.delivery_id:
        raise DeliveryBindingMismatch('mirror is already bound to a different delivery')
