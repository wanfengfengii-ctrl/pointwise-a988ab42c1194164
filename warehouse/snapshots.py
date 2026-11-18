from collections.abc import Iterable
from dataclasses import dataclass

from lake_codec import canonical_json, content_digest, decode_integer, encode_integer, load_json, require_fields, require_list, require_text


@dataclass(frozen=True, slots=True)
class EffectiveSource:
    source_id: str
    code: str


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    code: str
    value: int


@dataclass(frozen=True, slots=True)
class WarehouseSnapshot:
    sources: tuple[EffectiveSource, ...]
    observations: tuple[Observation, ...]


def make_snapshot(
    sources: Iterable[EffectiveSource] = (),
    observations: Iterable[Observation] = (),
) -> WarehouseSnapshot:
    sources_by_id = {}
    for source in sources:
        if type(source) is not EffectiveSource:
            raise ValueError('expected an EffectiveSource')
        source_id = require_text(source.source_id, 'source_id')
        require_text(source.code, 'code')
        if source_id in sources_by_id:
            raise ValueError(f'duplicate source identity {source_id!r}')
        sources_by_id[source_id] = source
    observations_by_id = {}
    for observation in observations:
        if type(observation) is not Observation:
            raise ValueError('expected an Observation')
        observation_id = require_text(observation.observation_id, 'observation_id')
        require_text(observation.code, 'code')
        if type(observation.value) is not int:
            raise ValueError('observation value must be an integer')
        if observation_id in observations_by_id:
            raise ValueError(f'duplicate observation identity {observation_id!r}')
        observations_by_id[observation_id] = observation
    return WarehouseSnapshot(
        tuple(sources_by_id[key] for key in sorted(sources_by_id)),
        tuple(observations_by_id[key] for key in sorted(observations_by_id)),
    )


def encode_snapshot(snapshot: WarehouseSnapshot) -> str:
    if type(snapshot) is not WarehouseSnapshot:
        raise ValueError('expected a WarehouseSnapshot')
    snapshot = make_snapshot(snapshot.sources, snapshot.observations)
    return canonical_json({
        'sources': [[source.source_id, source.code] for source in snapshot.sources],
        'observations': [
            [observation.observation_id, observation.code, encode_integer(observation.value)]
            for observation in snapshot.observations
        ],
    })


def _rows(document: dict, field: str, width: int):
    for row in require_list(document[field], field):
        if type(row) is not list or len(row) != width:
            raise ValueError(f'{field} row must have {width} fields')
        yield row


def decode_snapshot(text: str) -> WarehouseSnapshot:
    document = require_fields(load_json(text), ('sources', 'observations'))
    return make_snapshot(
        (EffectiveSource(source_id, code) for source_id, code in _rows(document, 'sources', 2)),
        (Observation(obs_id, code, decode_integer(value))
         for obs_id, code, value in _rows(document, 'observations', 3)),
    )


def snapshot_identity(snapshot: WarehouseSnapshot) -> str:
    return content_digest(encode_snapshot(snapshot))
