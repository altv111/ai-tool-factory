from __future__ import annotations

from .base64_codec import BASE64_CODEC
from .table_format_converter import TABLE_FORMAT_CONVERTER
from .types import DeterministicStrategy


def select_specific_builtin_strategy(text: str) -> DeterministicStrategy | None:
    haystack = text.lower()
    if "json" in haystack and "csv" in haystack:
        return TABLE_FORMAT_CONVERTER
    if "base64" in haystack and ("encode" in haystack or "decode" in haystack or "codec" in haystack):
        return BASE64_CODEC
    return None
