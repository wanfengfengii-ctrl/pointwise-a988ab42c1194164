from sqlite3 import Connection

from delivery.manifest import encode_manifest
from delivery.registry import read_delivery


def export_delivery(db: Connection, delivery_id: str) -> str:
    """Return the canonical, self-contained offline package for a published delivery."""
    return encode_manifest(read_delivery(db, delivery_id))
