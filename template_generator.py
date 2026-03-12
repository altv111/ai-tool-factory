#!/usr/bin/env python3
"""Generate new app templates for startup-factory using an LLM."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib import error, request


BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"
TEMPLATES_DIR = BASE_DIR / "templates"

REQUIRED_FILES = [
    "template_config.json",
    "prompt.txt",
    "package.json",
    "tsconfig.json",
    "next.config.js",
    "next-env.d.ts",
    "frontend/page.tsx",
    "api/route.ts",
    "app/layout.tsx",
    "app/globals.css",
]


class TemplateGenerationError(Exception):
    pass


@dataclass
class TemplateManifest:
    template_name: str
    rationale: str
    files: dict[str, str]


@dataclass
class ManifestReview:
    score: int
    decision: str
    reason: str
    issues: list[str]


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise TemplateGenerationError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def call_openai_compatible(prompt: str) -> str:
    openai_key = os.getenv("OPENAI_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    api_key = openai_key or gemini_key
    if not api_key:
        raise TemplateGenerationError("OPENAI_API_KEY or GEMINI_API_KEY is required")

    using_gemini_fallback = bool(gemini_key and not openai_key)
    default_base_url = (
        "https://generativelanguage.googleapis.com/v1beta/openai"
        if using_gemini_fallback
        else "https://api.openai.com/v1"
    )
    default_model = "gemini-2.0-flash" if using_gemini_fallback else "gpt-4o-mini"

    base_url = os.getenv("OPENAI_BASE_URL", default_base_url).rstrip("/")
    model = os.getenv("OPENAI_MODEL", default_model)

    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=f"{base_url}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise TemplateGenerationError(
            f"LLM API HTTP {exc.code}: {message}"
        ) from exc
    except error.URLError as exc:
        raise TemplateGenerationError(f"LLM API request failed: {exc}") from exc

    try:
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise TemplateGenerationError(f"Unexpected API response: {raw[:500]}") from exc


def parse_manifest(raw: str) -> TemplateManifest:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TemplateGenerationError(f"Manifest is not valid JSON: {raw[:500]}") from exc

    for key in ["template_name", "rationale", "files"]:
        if key not in data:
            raise TemplateGenerationError(f"Missing manifest field: {key}")

    files = data["files"]
    if not isinstance(files, dict):
        raise TemplateGenerationError("Manifest 'files' must be an object mapping path->content")

    normalized_files: dict[str, str] = {}
    for raw_path, raw_content in files.items():
        if not isinstance(raw_path, str):
            raise TemplateGenerationError("Manifest file path keys must be strings")
        if not isinstance(raw_content, str):
            raise TemplateGenerationError(f"File content must be string for: {raw_path}")
        normalized_path = normalize_rel_path(raw_path)
        if not raw_content.strip():
            raise TemplateGenerationError(f"File content is empty: {normalized_path}")
        normalized_files[normalized_path] = raw_content

    return TemplateManifest(
        template_name=str(data["template_name"]).strip(),
        rationale=str(data["rationale"]).strip(),
        files=normalized_files,
    )


def parse_review(raw: str) -> ManifestReview:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TemplateGenerationError(f"Review is not valid JSON: {raw[:500]}") from exc

    for key in ["score", "decision", "reason", "issues"]:
        if key not in data:
            raise TemplateGenerationError(f"Missing review field: {key}")

    try:
        score = int(data["score"])
    except (TypeError, ValueError) as exc:
        raise TemplateGenerationError("Review score must be an integer") from exc

    if score < 0 or score > 10:
        raise TemplateGenerationError("Review score must be in range 0..10")

    decision = str(data["decision"]).strip().lower()
    if decision not in {"accept", "reject"}:
        raise TemplateGenerationError("Review decision must be 'accept' or 'reject'")

    issues_raw = data["issues"]
    if not isinstance(issues_raw, list) or not all(
        isinstance(item, str) for item in issues_raw
    ):
        raise TemplateGenerationError("Review issues must be a list of strings")

    return ManifestReview(
        score=score,
        decision=decision,
        reason=str(data["reason"]).strip(),
        issues=[issue.strip() for issue in issues_raw if issue.strip()],
    )


def normalize_rel_path(path: str) -> str:
    candidate = path.strip().replace("\\", "/")
    if not candidate:
        raise TemplateGenerationError("File path cannot be empty")
    if candidate.startswith("/"):
        raise TemplateGenerationError(f"Absolute paths are not allowed: {path}")

    pure = PurePosixPath(candidate)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise TemplateGenerationError(f"Invalid relative path: {path}")

    return pure.as_posix()


def validate_template_name(template_name: str) -> None:
    if not template_name:
        raise TemplateGenerationError("template_name cannot be empty")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", template_name):
        raise TemplateGenerationError(
            "template_name must match: [a-z0-9][a-z0-9_-]*"
        )


def validate_manifest(manifest: TemplateManifest) -> None:
    validate_template_name(manifest.template_name)

    missing = [path for path in REQUIRED_FILES if path not in manifest.files]
    if missing:
        raise TemplateGenerationError(
            "Manifest missing required files: " + ", ".join(sorted(missing))
        )

    invalid_config = validate_template_config(manifest.files["template_config.json"])
    if invalid_config:
        raise TemplateGenerationError(f"Invalid template_config.json: {invalid_config}")


def validate_template_config(raw_json: str) -> str | None:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return f"not valid JSON ({exc})"

    if not isinstance(data, dict):
        return "must be a JSON object"
    if "default_configuration" not in data:
        return "missing 'default_configuration'"
    if not isinstance(data["default_configuration"], dict):
        return "'default_configuration' must be an object"
    return None


def existing_templates() -> list[str]:
    if not TEMPLATES_DIR.exists():
        return []
    return sorted([p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()])


def build_generate_prompt(idea: str, feedback: str, attempt: int) -> str:
    prompt = load_prompt("template_generate_prompt.txt")
    return (
        prompt.replace("{{idea}}", idea.strip())
        .replace("{{attempt}}", str(attempt))
        .replace("{{existing_templates}}", ", ".join(existing_templates()))
        .replace("{{required_files}}", "\n".join(f"- {f}" for f in REQUIRED_FILES))
        .replace("{{feedback}}", feedback.strip() or "None")
    )


def build_review_prompt(idea: str, manifest: TemplateManifest) -> str:
    prompt = load_prompt("template_review_prompt.txt")
    manifest_json = json.dumps(
        {
            "template_name": manifest.template_name,
            "rationale": manifest.rationale,
            "files": manifest.files,
        },
        indent=2,
    )
    return (
        prompt.replace("{{idea}}", idea.strip())
        .replace("{{required_files}}", "\n".join(f"- {f}" for f in REQUIRED_FILES))
        .replace("{{manifest_json}}", manifest_json)
    )


def review_manifest(idea: str, manifest: TemplateManifest) -> ManifestReview:
    raw = call_openai_compatible(build_review_prompt(idea, manifest))
    return parse_review(raw)


def generate_manifest(idea: str, max_attempts: int) -> tuple[TemplateManifest, ManifestReview]:
    feedback = ""
    latest_review: ManifestReview | None = None
    latest_error = ""

    for attempt in range(1, max_attempts + 1):
        print(f"Template generation attempt {attempt}/{max_attempts}...")
        raw = call_openai_compatible(build_generate_prompt(idea, feedback, attempt))

        try:
            manifest = parse_manifest(raw)
            validate_manifest(manifest)
        except TemplateGenerationError as exc:
            latest_error = str(exc)
            feedback = f"Manifest failed validation: {latest_error}"
            print(f"Manifest rejected: {latest_error}", file=sys.stderr)
            continue

        review = review_manifest(idea, manifest)
        latest_review = review
        print(f"Review score: {review.score}/10 ({review.decision})")

        if review.decision == "accept":
            return manifest, review

        issues = "; ".join(review.issues) or "No issues listed"
        feedback = f"Review rejected manifest. Reason: {review.reason}. Issues: {issues}"
        print(f"Review rejected: {review.reason}", file=sys.stderr)

    if latest_review:
        raise TemplateGenerationError(
            f"No acceptable template after {max_attempts} attempts. "
            f"Last review: {latest_review.reason}"
        )
    raise TemplateGenerationError(
        f"No acceptable template after {max_attempts} attempts. Last error: {latest_error or 'none'}"
    )


def write_template(manifest: TemplateManifest, overwrite: bool) -> Path:
    target_dir = TEMPLATES_DIR / manifest.template_name

    if target_dir.exists():
        if not overwrite:
            raise TemplateGenerationError(
                f"Template already exists: {target_dir} (use --overwrite to replace)"
            )
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in manifest.files.items():
        out_path = target_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content.rstrip() + "\n", encoding="utf-8")

    return target_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a new startup-factory template from an idea."
    )
    parser.add_argument(
        "--idea",
        required=True,
        help="Short plain-language description of the template to generate.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.getenv("TEMPLATE_MAX_ATTEMPTS", "3")),
        help="Maximum generation retries (default: TEMPLATE_MAX_ATTEMPTS or 3).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing template with the same name.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and review manifest but do not write files.",
    )
    parser.add_argument(
        "--print-manifest",
        action="store_true",
        help="Print manifest JSON to stdout before writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    max_attempts = max(1, args.max_attempts)

    try:
        manifest, review = generate_manifest(args.idea, max_attempts=max_attempts)

        if args.print_manifest:
            print(
                json.dumps(
                    {
                        "template_name": manifest.template_name,
                        "rationale": manifest.rationale,
                        "files": manifest.files,
                        "review": {
                            "score": review.score,
                            "decision": review.decision,
                            "reason": review.reason,
                            "issues": review.issues,
                        },
                    },
                    indent=2,
                )
            )

        if args.dry_run:
            print(f"Dry run complete. Proposed template: {manifest.template_name}")
            print("Files:")
            for file_path in sorted(manifest.files):
                print(f"- {file_path}")
            return 0

        out_dir = write_template(manifest, overwrite=args.overwrite)
        print(f"Template created: {out_dir}")
        print("Required files:")
        for file_path in REQUIRED_FILES:
            print(f"- {file_path}")
        return 0
    except TemplateGenerationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
