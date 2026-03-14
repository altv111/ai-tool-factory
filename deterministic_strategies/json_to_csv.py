from __future__ import annotations

from .types import DeterministicStrategy


JSON_TO_CSV = DeterministicStrategy(
    kind="json_to_csv",
    description="Convert JSON arrays/objects into CSV output.",
    transform_function_code="""function transformInput(input: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(input);
  } catch (_err) {
    throw new Error("Input must be valid JSON for this deterministic route.");
  }

  const rows = Array.isArray(parsed)
    ? parsed
    : parsed && typeof parsed === "object"
      ? Object.values(parsed as Record<string, unknown>).find((v) => Array.isArray(v)) || []
      : [];

  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error("Expected a non-empty JSON array of objects.");
  }

  const objectRows = rows.filter((row) => row && typeof row === "object") as Array<Record<string, unknown>>;
  if (objectRows.length === 0) {
    throw new Error("Expected array items to be JSON objects.");
  }

  const headers = Array.from(new Set(objectRows.flatMap((row) => Object.keys(row))));
  if (headers.length === 0) {
    throw new Error("Unable to infer CSV headers from input.");
  }

  function valueToCsv(value: unknown): string {
    const text = value === null || value === undefined ? "" : String(value);
    const escaped = text.replaceAll('"', '""');
    return /[",\\n]/.test(escaped) ? `"${escaped}"` : escaped;
  }

  const lines = [
    headers.map((h) => valueToCsv(h)).join(","),
    ...objectRows.map((row) => headers.map((h) => valueToCsv(row[h])).join(",")),
  ];
  return lines.join("\\n");
}""",
)

