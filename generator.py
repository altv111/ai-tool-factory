#!/usr/bin/env python3
"""startup-factory generator.

Pipeline:
1. generate_idea()
2. select_template()
3. instantiate_template()
4. create_project()
5. deploy_project()
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
PROMPTS_DIR = BASE_DIR / "prompts"
GENERATED_DIR = Path(
    os.getenv("GENERATED_TOOLS_DIR", str(BASE_DIR / "generated_tools"))
).expanduser()
REGISTRY_PATH = BASE_DIR / "tools_registry.json"


@dataclass
class Idea:
    tool_name: str
    template: str
    description: str
    target_user: str
    configuration: dict[str, Any]


@dataclass
class IdeaReview:
    score: int
    reason: str
    decision: str


class GenerationError(Exception):
    pass


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "generated-tool"


def load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise GenerationError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def call_openai_compatible(prompt: str) -> str:
    openai_key = os.getenv("OPENAI_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    api_key = openai_key or gemini_key
    if not api_key:
        raise GenerationError("OPENAI_API_KEY or GEMINI_API_KEY is required")

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
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise GenerationError(f"LLM API HTTP {exc.code}: {message}") from exc
    except error.URLError as exc:
        raise GenerationError(f"LLM API request failed: {exc}") from exc

    try:
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Unexpected API response: {raw[:500]}") from exc


def parse_idea(raw_content: str) -> Idea:
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Model did not return valid JSON: {raw_content}") from exc

    for key in ["tool_name", "template", "description", "target_user", "configuration"]:
        if key not in data:
            raise GenerationError(f"Missing required idea field: {key}")

    configuration = data["configuration"]
    if not isinstance(configuration, dict):
        raise GenerationError("'configuration' must be a JSON object")

    return Idea(
        tool_name=str(data["tool_name"]).strip(),
        template=str(data["template"]).strip(),
        description=str(data["description"]).strip(),
        target_user=str(data["target_user"]).strip(),
        configuration=configuration,
    )


def parse_idea_review(raw_content: str) -> IdeaReview:
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Review model did not return valid JSON: {raw_content}") from exc

    for key in ["score", "reason", "decision"]:
        if key not in data:
            raise GenerationError(f"Missing required review field: {key}")

    try:
        score = int(data["score"])
    except (TypeError, ValueError) as exc:
        raise GenerationError("Review 'score' must be an integer from 0 to 10") from exc
    if score < 0 or score > 10:
        raise GenerationError("Review 'score' must be in range 0..10")

    decision = str(data["decision"]).strip().lower()
    if decision not in {"accept", "reject"}:
        raise GenerationError("Review 'decision' must be 'accept' or 'reject'")

    return IdeaReview(score=score, reason=str(data["reason"]).strip(), decision=decision)


def build_idea_prompt() -> str:
    template_list = ", ".join(sorted(available_templates()))
    prompt = load_prompt("idea_prompt.txt")
    return prompt.replace("{{allowed_templates}}", template_list)


def build_review_prompt(idea: Idea) -> str:
    prompt = load_prompt("review_prompt.txt")
    idea_json = json.dumps(
        {
            "tool_name": idea.tool_name,
            "template": idea.template,
            "description": idea.description,
            "target_user": idea.target_user,
            "configuration": idea.configuration,
        },
        indent=2,
    )
    return prompt.replace("{{idea_json}}", idea_json)


def review_idea(idea: Idea) -> IdeaReview:
    review_prompt = build_review_prompt(idea)
    raw = call_openai_compatible(review_prompt)
    return parse_idea_review(raw)


def generate_idea() -> tuple[Idea, IdeaReview]:
    max_attempts = max(1, int(os.getenv("IDEA_MAX_ATTEMPTS", "3")))
    latest_review: IdeaReview | None = None

    for attempt in range(1, max_attempts + 1):
        raw_idea = call_openai_compatible(build_idea_prompt())
        idea = parse_idea(raw_idea)
        review = review_idea(idea)
        latest_review = review
        print(f"Idea review: {review.score}/10 ({review.decision})")

        if review.decision == "accept":
            return idea, review

        print(f"Idea rejected on attempt {attempt}: {review.reason}", file=sys.stderr)

    raise GenerationError(
        f"No acceptable idea after {max_attempts} attempts. "
        f"Last review: {latest_review.reason if latest_review else 'none'}"
    )


def available_templates() -> set[str]:
    if not TEMPLATES_DIR.exists():
        return set()
    return {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()}


def select_template(idea: Idea) -> str:
    templates = available_templates()
    if not templates:
        raise GenerationError("No templates found")
    if idea.template in templates:
        return idea.template
    raise GenerationError(f"Unknown template: {idea.template}")


def read_template_defaults(template_path: Path) -> dict[str, Any]:
    cfg_path = template_path / "template_config.json"
    if not cfg_path.exists():
        return {}
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return data.get("default_configuration", {}) if isinstance(data, dict) else {}


def merge_configuration(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(override)
    return merged


def replace_tokens(text: str, variables: dict[str, Any]) -> str:
    out = text
    for key, value in variables.items():
        out = out.replace(f"{{{{{key}}}}}", str(value))
    return out


def instantiate_template(source_dir: Path, destination_dir: Path, variables: dict[str, Any]) -> None:
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    shutil.copytree(source_dir, destination_dir)

    for path in destination_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated = replace_tokens(content, variables)
        if updated != content:
            path.write_text(updated, encoding="utf-8")


def ensure_nextjs_project(project_dir: Path) -> None:
    frontend_src = project_dir / "frontend" / "page.tsx"
    api_src = project_dir / "api" / "route.ts"

    app_dir = project_dir / "app"
    api_dir = app_dir / "api" / "generate"
    api_dir.mkdir(parents=True, exist_ok=True)

    if frontend_src.exists():
        shutil.copyfile(frontend_src, app_dir / "page.tsx")
    if api_src.exists():
        shutil.copyfile(api_src, api_dir / "route.ts")


def create_project(idea: Idea, template_name: str) -> Path:
    template_dir = TEMPLATES_DIR / template_name
    if not template_dir.exists():
        raise GenerationError(f"Template not found: {template_dir}")

    defaults = read_template_defaults(template_dir)
    config = merge_configuration(defaults, idea.configuration)

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(idea.tool_name)
    project_dir = GENERATED_DIR / slug

    variables = {
        **config,
        "tool_name": idea.tool_name,
        "tool_slug": slug,
        "description": idea.description,
        "target_user": idea.target_user,
        "template": template_name,
    }

    instantiate_template(template_dir, project_dir, variables)
    ensure_nextjs_project(project_dir)

    (project_dir / "generated_config.json").write_text(
        json.dumps(
            {
                "tool_name": idea.tool_name,
                "tool_slug": slug,
                "template": template_name,
                "description": idea.description,
                "target_user": idea.target_user,
                "configuration": config,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return project_dir


def extract_url(text: str) -> str | None:
    match = re.search(r"https://[^\s]+", text)
    return match.group(0) if match else None


def deploy_project(project_dir: Path) -> str:
    cmd = ["vercel", "deploy", "--yes"]
    proc = subprocess.run(
        cmd,
        cwd=project_dir,
        text=True,
        capture_output=True,
        check=False,
    )

    combined = f"{proc.stdout}\n{proc.stderr}"
    url = extract_url(combined)

    if proc.returncode != 0:
        raise GenerationError(
            f"Vercel deploy failed (exit {proc.returncode}).\n{combined}"
        )
    if not url:
        raise GenerationError(f"Deploy succeeded but no URL detected.\n{combined}")

    return url


def load_registry() -> list[dict[str, Any]]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Invalid registry JSON: {REGISTRY_PATH}") from exc
    if not isinstance(data, list):
        raise GenerationError(f"Registry must be a JSON list: {REGISTRY_PATH}")
    return data


def append_registry_entry(template_name: str, project_dir: Path, deployed_url: str) -> None:
    registry = load_registry()
    entry = {
        "name": project_dir.name,
        "url": deployed_url,
        "template": template_name,
        "created_at": date.today().isoformat(),
    }
    registry.append(entry)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    try:
        print("Generating idea...")
        idea, _review = generate_idea()

        template_name = select_template(idea)
        print(f"Idea selected: {idea.tool_name}")

        print("Creating project...")
        project_dir = create_project(idea, template_name)

        print("Deploying to Vercel...")
        url = deploy_project(project_dir)
        append_registry_entry(template_name, project_dir, url)

        print("Deployment URL:")
        print(url)
        return 0
    except GenerationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
