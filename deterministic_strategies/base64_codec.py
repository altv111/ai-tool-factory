from __future__ import annotations

from .types import DeterministicStrategy


BASE64_CODEC = DeterministicStrategy(
    kind="base64_codec",
    description="Encode plain text to base64 or decode base64 to text.",
    transform_function_code="""function transformInput(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    throw new Error("Input is required.");
  }

  const looksBase64 = /^[A-Za-z0-9+/=\\r\\n]+$/.test(trimmed) && trimmed.length % 4 === 0;
  if (looksBase64) {
    try {
      const decoded = Buffer.from(trimmed, "base64").toString("utf-8");
      const reencoded = Buffer.from(decoded, "utf-8").toString("base64").replace(/=+$/, "");
      const normalized = trimmed.replace(/=+$/, "").replace(/\\s+/g, "");
      if (reencoded === normalized) {
        return decoded;
      }
    } catch (_err) {
      // Fall through to encode branch.
    }
  }

  return Buffer.from(input, "utf-8").toString("base64");
}""",
)

