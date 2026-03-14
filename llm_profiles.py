from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LlmProfile:
    name: str
    instructions: str


DEV_DOC_WRITER = LlmProfile(
    name="dev_doc_writer",
    instructions=(
        "Return output in clear Markdown with these sections: "
        "## Summary, ## Proposed Output, ## Notes. "
        "Keep tone professional and concise. "
        "For commit/PR/release docs, include both short and detailed variants when useful."
    ),
)


def select_llm_profile(text: str) -> LlmProfile | None:
    haystack = text.lower()
    keywords = [
        "commit message",
        "commit-message",
        "pr description",
        "pull request description",
        "release notes",
        "readme",
        "api doc",
        "api documentation",
    ]
    if any(keyword in haystack for keyword in keywords):
        return DEV_DOC_WRITER
    return None

