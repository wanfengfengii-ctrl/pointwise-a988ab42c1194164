from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SourceChange:
    source_id: str
    operation: Literal['upsert', 'remove']
    code: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationChange:
    observation_id: str
    operation: Literal['upsert', 'remove']
    code: str | None = None
    value: int | None = None


@dataclass(frozen=True, slots=True)
class OriginalBatch:
    batch_id: str
    changes: tuple[SourceChange | ObservationChange, ...]
