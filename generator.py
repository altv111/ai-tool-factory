#!/usr/bin/env python3
"""startup-factory route generator.

Pipeline:
1. generate_idea()
2. write_route_module()
3. update site integration files
4. optionally deploy host app
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib import error, request


BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"
GENERATED_DIR = Path(
    os.getenv("GENERATED_TOOLS_DIR", str(BASE_DIR / "generated_tools"))
).expanduser()
APP_ROOT_DIR_ENV = os.getenv("APP_ROOT_DIR", "").strip()
REGISTRY_PATH = BASE_DIR / "tools_registry.json"

REQUIRED_OUTPUT_FILES = ["app/<tool-slug>/page.tsx", "app/api/<tool-slug>/route.ts"]
FORBIDDEN_FILENAMES = {
    "package.json",
    "next.config.js",
    "tsconfig.json",
    "next-env.d.ts",
    ".vercel",
    ".gitignore",
    "layout.tsx",
}


@dataclass
class Idea:
    tool_name: str
    tool_slug: str
    description: str
    target_user: str
    input_placeholder: str
    output_label: str
    llm_task: str


@dataclass
class IdeaReview:
    score: int
    reason: str
    decision: str


class GenerationError(Exception):
    pass


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

    required = [
        "tool_name",
        "tool_slug",
        "description",
        "target_user",
        "input_placeholder",
        "output_label",
        "llm_task",
    ]
    for key in required:
        if key not in data:
            raise GenerationError(f"Missing required idea field: {key}")

    tool_slug = str(data["tool_slug"]).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", tool_slug):
        raise GenerationError("tool_slug must match [a-z0-9][a-z0-9-]*")

    return Idea(
        tool_name=str(data["tool_name"]).strip(),
        tool_slug=tool_slug,
        description=str(data["description"]).strip(),
        target_user=str(data["target_user"]).strip(),
        input_placeholder=str(data["input_placeholder"]).strip(),
        output_label=str(data["output_label"]).strip(),
        llm_task=str(data["llm_task"]).strip(),
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


def append_registry_entry(idea: Idea, deployed_url: str | None) -> None:
    registry = load_registry()
    entry = {
        "name": idea.tool_slug,
        "route": f"/{idea.tool_slug}",
        "url": deployed_url,
        "deployed": bool(deployed_url),
        "created_at": date.today().isoformat(),
    }
    registry.append(entry)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def escape_ts_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def existing_tool_slugs(app_root: Path) -> set[str]:
    app_dir = app_root / "app"
    if not app_dir.exists():
        return set()

    ignore = {"api", "about", "privacy", "contact", "tools"}
    slugs = {
        page.parent.name
        for page in app_dir.glob("*/page.tsx")
        if page.parent.name not in ignore
    }

    for entry in load_registry():
        if not isinstance(entry, dict):
            continue
        route = entry.get("route")
        if isinstance(route, str) and route.startswith("/") and len(route) > 1:
            slugs.add(route[1:])

    return slugs


def build_idea_prompt(existing_slugs: set[str], feedback: str) -> str:
    prompt = load_prompt("idea_prompt.txt")
    return (
        prompt.replace("{{existing_slugs}}", ", ".join(sorted(existing_slugs)) or "none")
        .replace("{{required_output_files}}", "\n".join(f"- {f}" for f in REQUIRED_OUTPUT_FILES))
        .replace("{{feedback}}", feedback or "none")
    )


def build_review_prompt(idea: Idea, existing_slugs: set[str]) -> str:
    prompt = load_prompt("review_prompt.txt")
    idea_json = json.dumps(
        {
            "tool_name": idea.tool_name,
            "tool_slug": idea.tool_slug,
            "description": idea.description,
            "target_user": idea.target_user,
            "input_placeholder": idea.input_placeholder,
            "output_label": idea.output_label,
            "llm_task": idea.llm_task,
        },
        indent=2,
    )
    return (
        prompt.replace("{{idea_json}}", idea_json)
        .replace("{{existing_slugs}}", ", ".join(sorted(existing_slugs)) or "none")
        .replace("{{required_output_files}}", "\n".join(f"- {f}" for f in REQUIRED_OUTPUT_FILES))
    )


def review_idea(idea: Idea, existing_slugs: set[str]) -> IdeaReview:
    review_prompt = build_review_prompt(idea, existing_slugs)
    raw = call_openai_compatible(review_prompt)
    return parse_idea_review(raw)


def generate_idea(existing_slugs: set[str]) -> tuple[Idea, IdeaReview]:
    max_attempts = max(1, int(os.getenv("IDEA_MAX_ATTEMPTS", "3")))
    latest_review: IdeaReview | None = None
    feedback = ""

    for attempt in range(1, max_attempts + 1):
        print(f"Idea generation attempt {attempt}/{max_attempts}...")
        raw_idea = call_openai_compatible(build_idea_prompt(existing_slugs, feedback))
        idea = parse_idea(raw_idea)

        if idea.tool_slug in existing_slugs:
            feedback = f"tool_slug '{idea.tool_slug}' already exists; choose a unique slug"
            print(f"Idea rejected on attempt {attempt}: {feedback}", file=sys.stderr)
            continue

        review = review_idea(idea, existing_slugs)
        latest_review = review
        print(f"Idea review: {review.score}/10 ({review.decision})")

        if review.decision == "accept":
            return idea, review

        feedback = review.reason
        print(f"Idea rejected on attempt {attempt}: {review.reason}", file=sys.stderr)

    raise GenerationError(
        f"No acceptable idea after {max_attempts} attempts. "
        f"Last review: {latest_review.reason if latest_review else 'none'}"
    )


def render_page_tsx(idea: Idea) -> str:
    return f'''"use client";

import {{ useState }} from "react";
import ToolLayout from "@/components/ToolLayout";

export default function {camel_component_name(idea.tool_slug)}Page() {{
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit() {{
    setLoading(true);
    setError("");
    setOutput("");

    try {{
      const res = await fetch("/api/{idea.tool_slug}", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ input }}),
      }});

      const data = await res.json();
      if (!res.ok) {{
        setError(data.error || "Request failed");
      }} else {{
        setOutput(data.output || "");
      }}
    }} catch (_err) {{
      setError("Network error. Try again.");
    }} finally {{
      setLoading(false);
    }}
  }}

  return (
    <ToolLayout title="{escape_ts_string(idea.tool_name)}" description="{escape_ts_string(idea.description)}">
      <textarea
        rows={{10}}
        placeholder="{escape_ts_string(idea.input_placeholder)}"
        value={{input}}
        onChange={{(e) => setInput(e.target.value)}}
      />
      <button onClick={{onSubmit}} disabled={{loading || !input.trim()}}>
        {{loading ? "Processing..." : "Generate"}}
      </button>

      {{error && <pre>{{error}}</pre>}}
      {{output && (
        <section>
          <h2>{escape_ts_string(idea.output_label)}</h2>
          <pre>{{output}}</pre>
        </section>
      )}}
    </ToolLayout>
  );
}}
'''


def render_api_route_ts(idea: Idea) -> str:
    return f'''import {{ NextRequest, NextResponse }} from "next/server";

const MAX_REQUESTS_PER_IP = 10;
const MAX_TOKENS = 900;

const DAY_MS = 24 * 60 * 60 * 1000;
const ipCounter = new Map<string, {{ count: number; resetAt: number }}>();

const SYSTEM_PROMPT = `{escape_template_prompt(idea)}`;

function checkIpLimit(ip: string): string | null {{
  const now = Date.now();
  const current = ipCounter.get(ip);

  if (!current || now > current.resetAt) {{
    ipCounter.set(ip, {{ count: 1, resetAt: now + DAY_MS }});
    return null;
  }}

  if (current.count >= MAX_REQUESTS_PER_IP) {{
    return `Rate limit exceeded: max ${{MAX_REQUESTS_PER_IP}} requests per IP per day.`;
  }}

  current.count += 1;
  ipCounter.set(ip, current);
  return null;
}}

async function callLLM(userInput: string): Promise<string> {{
  const openaiKey = process.env.OPENAI_API_KEY;
  const geminiKey = process.env.GEMINI_API_KEY;
  const apiKey = openaiKey || geminiKey;
  if (!apiKey) {{
    return "LLM placeholder: set OPENAI_API_KEY or GEMINI_API_KEY (plus optional OPENAI_MODEL/OPENAI_BASE_URL) to enable real responses.";
  }}

  const usingGeminiFallback = Boolean(geminiKey && !openaiKey);
  const baseUrl = (
    process.env.OPENAI_BASE_URL ||
    (usingGeminiFallback
      ? "https://generativelanguage.googleapis.com/v1beta/openai"
      : "https://api.openai.com/v1")
  ).replace(/\\/$/, "");
  const model = process.env.OPENAI_MODEL || (usingGeminiFallback ? "gemini-2.0-flash" : "gpt-4o-mini");

  const response = await fetch(`${{baseUrl}}/chat/completions`, {{
    method: "POST",
    headers: {{
      "Content-Type": "application/json",
      Authorization: `Bearer ${{apiKey}}`,
    }},
    body: JSON.stringify({{
      model,
      max_tokens: MAX_TOKENS,
      messages: [
        {{ role: "system", content: SYSTEM_PROMPT }},
        {{ role: "user", content: userInput }},
      ],
    }}),
  }});

  if (!response.ok) {{
    const errorText = await response.text();
    throw new Error(`LLM error: ${{errorText}}`);
  }}

  const data = await response.json();
  return data?.choices?.[0]?.message?.content || "No output generated.";
}}

export async function POST(req: NextRequest) {{
  try {{
    const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";

    const ipError = checkIpLimit(ip);
    if (ipError) {{
      return NextResponse.json({{ error: ipError }}, {{ status: 429 }});
    }}

    const body = await req.json();
    const input = (body?.input || "").toString().trim();

    if (!input) {{
      return NextResponse.json({{ error: "Input is required." }}, {{ status: 400 }});
    }}

    const output = await callLLM(input);
    return NextResponse.json({{ output }});
  }} catch (err) {{
    const message = err instanceof Error ? err.message : "Unknown server error.";
    return NextResponse.json({{ error: message }}, {{ status: 500 }});
  }}
}}
'''


def escape_template_prompt(idea: Idea) -> str:
    lines = [
        f"You are {idea.tool_name}.",
        f"Description: {idea.description}",
        f"Target user: {idea.target_user}",
        f"Task: {idea.llm_task}",
        "Output must be concise, practical, and directly actionable.",
    ]
    return "\\n".join(lines).replace("`", "'")


def camel_component_name(slug: str) -> str:
    parts = slug.split("-")
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def resolve_app_root() -> Path:
    if APP_ROOT_DIR_ENV:
        app_root = Path(APP_ROOT_DIR_ENV).expanduser()
    else:
        generated = GENERATED_DIR
        if generated.name == "app" and (generated / "layout.tsx").exists():
            app_root = generated.parent
        elif (generated / "app" / "layout.tsx").exists():
            app_root = generated
        else:
            raise GenerationError(
                "Unable to infer app root. Set APP_ROOT_DIR to your Next.js app root "
                "(directory containing app/layout.tsx)."
            )

    if not (app_root / "app" / "layout.tsx").exists():
        raise GenerationError(f"Not a valid app root (missing app/layout.tsx): {app_root}")
    return app_root


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def upsert_tools_array_entry(
    file_path: Path,
    href: str,
    entry_text: str,
    *,
    array_name: str = "TOOLS",
) -> bool:
    text = file_path.read_text(encoding="utf-8")
    if f'href: "{href}"' in text:
        return False

    marker = f"const {array_name} = ["
    start = text.find(marker)
    if start == -1:
        raise GenerationError(f"Could not find '{marker}' in {file_path}")

    array_start = text.find("[", start)
    array_end = text.find("];", array_start)
    if array_start == -1 or array_end == -1:
        raise GenerationError(f"Could not parse {array_name} array in {file_path}")

    updated = text[: array_end] + entry_text + text[array_end:]
    file_path.write_text(updated, encoding="utf-8")
    return True


def upsert_sitemap_entry(file_path: Path, slug: str) -> bool:
    text = file_path.read_text(encoding="utf-8")
    needle = f"${{baseUrl}}/{slug}"
    if needle in text:
        return False

    return_start = text.find("return [")
    if return_start == -1:
        raise GenerationError(f"Could not find sitemap return array in {file_path}")

    array_start = text.find("[", return_start)
    array_end = text.find("];", array_start)
    if array_start == -1 or array_end == -1:
        raise GenerationError(f"Could not parse sitemap array in {file_path}")

    entry = (
        "\n    {\n"
        f"      url: `${{baseUrl}}/{slug}`,\n"
        "      lastModified,\n"
        "    },"
    )
    updated = text[:array_end] + entry + text[array_end:]
    file_path.write_text(updated, encoding="utf-8")
    return True


def validate_output_paths(written_relative_paths: set[str], slug: str) -> None:
    allowed = {
        f"app/{slug}/page.tsx",
        f"app/api/{slug}/route.ts",
        "app/page.tsx",
        "app/tools/page.tsx",
        "app/sitemap.ts",
    }

    extra = sorted(p for p in written_relative_paths if p not in allowed)
    if extra:
        raise GenerationError("Generated forbidden file paths: " + ", ".join(extra))

    for path in written_relative_paths:
        parts = Path(path).parts
        if any(part in FORBIDDEN_FILENAMES for part in parts):
            raise GenerationError(f"Forbidden filename generated: {path}")


def create_route_module(app_root: Path, idea: Idea) -> set[str]:
    app_dir = app_root / "app"
    route_page = app_dir / idea.tool_slug / "page.tsx"
    api_route = app_dir / "api" / idea.tool_slug / "route.ts"

    if route_page.exists() or api_route.exists():
        raise GenerationError(f"Route already exists for slug: {idea.tool_slug}")

    write_file(route_page, render_page_tsx(idea))
    write_file(api_route, render_api_route_ts(idea))

    home_page = app_dir / "page.tsx"
    tools_page = app_dir / "tools" / "page.tsx"
    sitemap = app_dir / "sitemap.ts"

    for required in [home_page, tools_page, sitemap]:
        if not required.exists():
            raise GenerationError(f"Required integration file not found: {required}")

    updated_home = upsert_tools_array_entry(
        home_page,
        f"/{idea.tool_slug}",
        (
            "\n  {\n"
            f'    href: "/{idea.tool_slug}",\n'
            f'    label: "{escape_ts_string(idea.tool_name)}",\n'
            f'    description: "{escape_ts_string(idea.description)}",\n'
            "  },"
        ),
    )
    updated_tools = upsert_tools_array_entry(
        tools_page,
        f"/{idea.tool_slug}",
        f'\n  {{ href: "/{idea.tool_slug}", label: "{escape_ts_string(idea.tool_name)}" }},',
    )
    updated_sitemap = upsert_sitemap_entry(sitemap, idea.tool_slug)

    if not updated_home or not updated_tools or not updated_sitemap:
        raise GenerationError(
            "Integration update failed. One or more files already contained this slug."
        )

    written = {
        f"app/{idea.tool_slug}/page.tsx",
        f"app/api/{idea.tool_slug}/route.ts",
        "app/page.tsx",
        "app/tools/page.tsx",
        "app/sitemap.ts",
    }
    validate_output_paths(written, idea.tool_slug)
    return written


def extract_url(text: str) -> str | None:
    match = re.search(r"https://[^\s]+", text)
    return match.group(0) if match else None


def deploy_host_app(app_root: Path) -> str:
    proc = subprocess.run(
        ["vercel", "deploy", "--yes"],
        cwd=app_root,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an in-app child route tool for a Next.js app."
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy host app to Vercel after generating route files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        app_root = resolve_app_root()
        existing_slugs = existing_tool_slugs(app_root)

        print("Generating idea...")
        idea, _review = generate_idea(existing_slugs)

        print(f"Creating child route: /{idea.tool_slug}")
        written = create_route_module(app_root, idea)
        print("Updated files:")
        for path in sorted(written):
            print(f"- {path}")

        deployed_url: str | None = None
        if args.deploy:
            print("Deploying host app to Vercel...")
            deployed_url = deploy_host_app(app_root)
            print("Deployment URL:")
            print(deployed_url)
        else:
            print("Skipping deploy. Pass --deploy to enable Vercel deployment.")

        append_registry_entry(idea, deployed_url)
        print("Done.")
        return 0
    except GenerationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
