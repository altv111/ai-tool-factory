# Deterministic Strategy Contribution Guide

Use this guide when adding a new deterministic strategy for `generator.py`.

## Goal

Keep deterministic routes:

- predictable
- safe
- cheap to run
- easy to review and test

## File layout

Create one file per strategy in this directory:

- `deterministic_strategies/<strategy_name>.py`

Each file should export one `DeterministicStrategy` object.

## Required shape

Example:

```python
from __future__ import annotations

from .types import DeterministicStrategy

MY_STRATEGY = DeterministicStrategy(
    kind="my_strategy",
    description="One sentence summary.",
    transform_function_code=\"\"\"function transformInput(input: string): string {
  // deterministic transformation
  return input;
}\"\"\",
)
```

## Rules for `transform_function_code`

Must:

- define exactly: `function transformInput(input: string): string`
- be deterministic (same input => same output)
- throw `Error` for invalid input
- be pure local logic (string/object/array processing)

Must not:

- make network calls (`fetch`, `axios`, URLs)
- call LLM helpers (`createChatCompletion`)
- read env vars (`process.env`)
- use dynamic code execution (`eval`, `Function`)
- use child processes
- include imports

## Registration steps

1. Add your strategy file.
2. Export it from `deterministic_strategies/__init__.py`.
3. Add selection logic in `deterministic_strategies/selector.py`.
4. Keep `selector.py` keyword matching simple and explicit.

## Selection guidance

- Match intent conservatively to avoid wrong transforms.
- If no strong match exists, let selector return `None` so fallback logic applies.

## Error contract expectations

When strategy is used in generated route:

- Invalid input should produce an `Error` message surfaced as API `500`.
- Missing input is handled by route-level validation as `400`.
- Turnstile/origin/rate-limit behavior is handled by route scaffold, not strategy code.

## Suggested checklist before commit

- [ ] `kind` is lowercase snake_case and unique.
- [ ] `transform_function_code` compiles as TypeScript function body in route context.
- [ ] No forbidden operations or dependencies.
- [ ] Representative sample inputs produce expected outputs.
- [ ] Edge cases return clear errors.
- [ ] `python3 -m py_compile generator.py deterministic_strategies/*.py` passes.

