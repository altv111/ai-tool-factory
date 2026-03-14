from __future__ import annotations

from .types import DeterministicStrategy


TEXT_CLEANUP = DeterministicStrategy(
    kind="text_cleanup",
    description="Clean up input text deterministically.",
    transform_function_code="""function transformInput(input: string): string {
  return input
    .split("\\n")
    .map((line) => line.trimEnd())
    .filter((line, idx, arr) => line || (idx > 0 && arr[idx - 1] !== ""))
    .join("\\n")
    .trim();
}""",
)

