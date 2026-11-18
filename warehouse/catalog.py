from collections.abc import Iterable

from sources.records import SourceChange
from warehouse.snapshots import EffectiveSource


def apply_source_changes(
    current: Iterable[EffectiveSource], changes: Iterable[SourceChange]
) -> tuple[EffectiveSource, ...]:
    by_id = {source.source_id: source for source in current}
    for change in changes:
        if change.operation == 'remove':
            by_id.pop(change.source_id, None)
        else:
            by_id[change.source_id] = EffectiveSource(change.source_id, change.code)
    return tuple(by_id.values())


def sources_by_code(sources: Iterable[EffectiveSource]) -> dict[str, frozenset[str]]:
    grouped: dict[str, set[str]] = {}
    for source in sources:
        grouped.setdefault(source.code, set()).add(source.source_id)
    return {code: frozenset(identities) for code, identities in grouped.items()}
