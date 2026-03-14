from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeterministicStrategy:
    kind: str
    description: str
    transform_function_code: str

