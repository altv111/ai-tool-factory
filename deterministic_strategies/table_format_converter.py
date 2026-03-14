from __future__ import annotations

from .types import DeterministicStrategy


TABLE_FORMAT_CONVERTER = DeterministicStrategy(
    kind="table_format_converter",
    description="Convert JSON arrays of objects to CSV, or CSV data to JSON array.",
    transform_function_code="""function transformInput(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    throw new Error("Input is required.");
  }

  function valueToCsv(value: unknown): string {
    const text = value === null || value === undefined ? "" : String(value);
    const escaped = text.replaceAll('"', '""');
    return /[",\\n]/.test(escaped) ? `"${escaped}"` : escaped;
  }

  function parseCsvRows(text: string): string[][] {
    const rows: string[][] = [];
    let row: string[] = [];
    let field = "";
    let inQuotes = false;

    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      const next = text[i + 1];

      if (inQuotes) {
        if (ch === '"' && next === '"') {
          field += '"';
          i += 1;
          continue;
        }
        if (ch === '"') {
          inQuotes = false;
          continue;
        }
        field += ch;
        continue;
      }

      if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        row.push(field);
        field = "";
      } else if (ch === "\\n") {
        row.push(field);
        field = "";
        rows.push(row);
        row = [];
      } else if (ch === "\\r") {
        // Ignore CR in CRLF.
      } else {
        field += ch;
      }
    }

    row.push(field);
    rows.push(row);
    return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ""));
  }

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch (_err) {
      throw new Error("Input JSON is invalid.");
    }

    const rows = Array.isArray(parsed)
      ? parsed
      : parsed && typeof parsed === "object"
        ? Object.values(parsed as Record<string, unknown>).find((v) => Array.isArray(v)) || []
        : [];

    if (!Array.isArray(rows) || rows.length === 0) {
      throw new Error("Expected JSON array of objects for JSON -> CSV conversion.");
    }

    const objectRows = rows.filter((row) => row && typeof row === "object") as Array<Record<string, unknown>>;
    if (objectRows.length === 0) {
      throw new Error("Expected array items to be JSON objects.");
    }

    const headers = Array.from(new Set(objectRows.flatMap((row) => Object.keys(row))));
    if (headers.length === 0) {
      throw new Error("Unable to infer CSV headers from JSON.");
    }

    const lines = [
      headers.map((h) => valueToCsv(h)).join(","),
      ...objectRows.map((row) => headers.map((h) => valueToCsv(row[h])).join(",")),
    ];
    return lines.join("\\n");
  }

  const rows = parseCsvRows(trimmed);
  if (rows.length < 2) {
    throw new Error("Expected CSV with a header row and at least one data row.");
  }

  const headers = rows[0].map((h) => h.trim());
  if (headers.some((h) => !h)) {
    throw new Error("CSV headers must be non-empty.");
  }

  const objects = rows.slice(1).map((row) => {
    const obj: Record<string, string> = {};
    headers.forEach((header, idx) => {
      obj[header] = (row[idx] ?? "").trim();
    });
    return obj;
  });

  return JSON.stringify(objects, null, 2);
}""",
)

