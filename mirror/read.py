from dataclasses import dataclass
from sqlite3 import Connection

from lake_db import transaction
from mirror.state import MirrorCursor, read_cursor
from mirror.storage import load_projection
from warehouse.snapshots import WarehouseSnapshot


@dataclass(frozen=True, slots=True)
class MirrorView:
    cursor: MirrorCursor
    snapshot: WarehouseSnapshot

    @property
    def state_id(self) -> str:
        if self.cursor.delivery_id is None:
            return f'baseline:{self.cursor.baseline_key}:{self.cursor.baseline_id}'
        return f'{self.cursor.delivery_id}:{self.cursor.position}'


def read_mirror(db: Connection) -> MirrorView:
    with transaction(db):
        cursor = read_cursor(db)
        return MirrorView(cursor, load_projection(db))
