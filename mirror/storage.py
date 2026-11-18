from sqlite3 import Connection

from lake_codec import decode_integer, encode_integer
from warehouse.snapshots import EffectiveSource, Observation, WarehouseSnapshot, make_snapshot


def ensure_projection_schema(db: Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS mirror_sources (
            source_id TEXT PRIMARY KEY NOT NULL,
            code TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS mirror_observations (
            observation_id TEXT PRIMARY KEY NOT NULL,
            code TEXT NOT NULL,
            value_hex TEXT NOT NULL CHECK (typeof(value_hex) = 'text')
        )
    ''')


def load_projection(db: Connection) -> WarehouseSnapshot:
    """Load both projections in the caller's SQLite snapshot."""
    sources = db.execute('SELECT source_id, code FROM mirror_sources')
    observations = db.execute('SELECT observation_id, code, value_hex FROM mirror_observations')
    return make_snapshot(
        (EffectiveSource(source_id, code) for source_id, code in sources),
        (Observation(observation_id, code, decode_integer(value))
         for observation_id, code, value in observations),
    )


def write_projection(db: Connection, snapshot: WarehouseSnapshot) -> None:
    """Replace the active projection within the caller's write transaction."""
    db.execute('DELETE FROM mirror_sources')
    db.executemany(
        'INSERT INTO mirror_sources VALUES (?, ?)',
        ((source.source_id, source.code) for source in snapshot.sources),
    )
    db.execute('DELETE FROM mirror_observations')
    db.executemany(
        'INSERT INTO mirror_observations VALUES (?, ?, ?)',
        ((observation.observation_id, observation.code, encode_integer(observation.value))
         for observation in snapshot.observations),
    )
