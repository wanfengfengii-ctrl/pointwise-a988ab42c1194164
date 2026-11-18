from collections.abc import Iterable

from sources.records import ObservationChange
from warehouse.snapshots import Observation


def apply_observation_changes(
    current: Iterable[Observation], changes: Iterable[ObservationChange]
) -> tuple[Observation, ...]:
    by_id = {observation.observation_id: observation for observation in current}
    for change in changes:
        if change.operation == 'remove':
            by_id.pop(change.observation_id, None)
        else:
            by_id[change.observation_id] = Observation(change.observation_id, change.code, change.value)
    return tuple(by_id.values())


def referenced_codes(observations: Iterable[Observation]) -> set[str]:
    return {observation.code for observation in observations}
