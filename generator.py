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

from deterministic_strategies import (
    TEXT_CLEANUP,
    DeterministicStrategy,
    select_specific_builtin_strategy,
)
from llm_profiles import select_llm_profile

BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"
TEMPLATES_DIR = BASE_DIR / "templates"
GENERATED_DIR = Path(
    os.getenv("GENERATED_TOOLS_DIR", str(BASE_DIR / "generated_tools"))
).expanduser()
APP_ROOT_DIR_ENV = os.getenv("APP_ROOT_DIR", "").strip()
REGISTRY_PATH = BASE_DIR / "tools_registry.json"

REQUIRED_OUTPUT_FILES = [
    "app/<tool-slug>/page.tsx",
    "app/<tool-slug>/<ToolName>Client.tsx",
    "app/api/<tool-slug>/route.ts",
]
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
    implementation_mode: str


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
        "implementation_mode",
    ]
    for key in required:
        if key not in data:
            raise GenerationError(f"Missing required idea field: {key}")

    tool_slug = str(data["tool_slug"]).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", tool_slug):
        raise GenerationError("tool_slug must match [a-z0-9][a-z0-9-]*")

    mode = str(data["implementation_mode"]).strip().lower()
    if mode not in {"llm", "deterministic"}:
        raise GenerationError("implementation_mode must be either 'llm' or 'deterministic'")

    return Idea(
        tool_name=str(data["tool_name"]).strip(),
        tool_slug=tool_slug,
        description=str(data["description"]).strip(),
        target_user=str(data["target_user"]).strip(),
        input_placeholder=str(data["input_placeholder"]).strip(),
        output_label=str(data["output_label"]).strip(),
        llm_task=str(data["llm_task"]).strip(),
        implementation_mode=mode,
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


def append_registry_entry(idea: Idea, deployed_url: str | None, template_name: str | None) -> None:
    registry = load_registry()
    entry = {
        "name": idea.tool_slug,
        "route": f"/{idea.tool_slug}",
        "url": deployed_url,
        "deployed": bool(deployed_url),
        "template": template_name,
        "mode": idea.implementation_mode,
        "created_at": date.today().isoformat(),
    }
    registry.append(entry)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")


def escape_ts_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def normalize_tool_display_name(name: str) -> str:
    cleaned = re.sub(r"[-_]+", " ", name.strip())
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return cleaned

    acronyms = {
        "ai",
        "api",
        "csv",
        "css",
        "git",
        "html",
        "http",
        "https",
        "id",
        "ip",
        "json",
        "jwt",
        "llm",
        "pdf",
        "pr",
        "seo",
        "sql",
        "ui",
        "url",
        "uuid",
        "xml",
        "yaml",
    }

    def normalize_word(word: str) -> str:
        lower = word.lower()
        if lower in acronyms:
            return lower.upper()
        if not re.fullmatch(r"[a-z0-9]+", lower):
            return word
        return lower.capitalize()

    parts = re.split(r"(\s+)", cleaned)
    return "".join(normalize_word(part) if not part.isspace() else part for part in parts)


def display_tool_name(idea: Idea) -> str:
    return normalize_tool_display_name(idea.tool_name)


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


def build_idea_prompt(
    existing_slugs: set[str],
    feedback: str,
    idea_hint: str,
    template_name: str | None,
    mode: str,
) -> str:
    prompt = load_prompt("idea_prompt.txt")
    rendered = (
        prompt.replace("{{existing_slugs}}", ", ".join(sorted(existing_slugs)) or "none")
        .replace("{{required_output_files}}", "\n".join(f"- {f}" for f in REQUIRED_OUTPUT_FILES))
        .replace("{{feedback}}", feedback or "none")
    )
    extra: list[str] = []
    if idea_hint.strip():
        extra.append(f"- User-specified idea direction: {idea_hint.strip()}")
    if template_name:
        extra.append(f"- You must shape this idea around template: {template_name}")
    if mode in {"deterministic", "llm-generate-deterministic"}:
        extra.append("- implementation_mode must be deterministic")
    elif mode == "llm":
        extra.append("- implementation_mode must be llm")
    else:
        extra.append(
            "- Prefer deterministic implementation_mode whenever the task is practical without an LLM"
        )
    if extra:
        rendered += "\n\nAdditional constraints:\n" + "\n".join(extra)
    return rendered


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
            "implementation_mode": idea.implementation_mode,
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


def generate_idea(
    existing_slugs: set[str],
    idea_hint: str = "",
    template_name: str | None = None,
    mode: str = "auto",
) -> tuple[Idea, IdeaReview]:
    max_attempts = max(1, int(os.getenv("IDEA_MAX_ATTEMPTS", "3")))
    latest_review: IdeaReview | None = None
    feedback = ""

    for attempt in range(1, max_attempts + 1):
        print(f"Idea generation attempt {attempt}/{max_attempts}...")
        raw_idea = call_openai_compatible(
            build_idea_prompt(existing_slugs, feedback, idea_hint, template_name, mode)
        )
        idea = parse_idea(raw_idea)

        if idea.tool_slug in existing_slugs:
            feedback = f"tool_slug '{idea.tool_slug}' already exists; choose a unique slug"
            print(f"Idea rejected on attempt {attempt}: {feedback}", file=sys.stderr)
            continue
        expected_mode = "deterministic" if mode == "llm-generate-deterministic" else mode
        if expected_mode in {"llm", "deterministic"} and idea.implementation_mode != expected_mode:
            feedback = (
                f"implementation_mode must be '{expected_mode}' but got '{idea.implementation_mode}'"
            )
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
    display_name = display_tool_name(idea)
    title = f"{display_name} | ToolDeck"
    return f'''import {client_component_name(idea.tool_slug)} from "./{client_component_name(idea.tool_slug)}";
import {{ toolMetadata }} from "@/lib/seo";

export const metadata = toolMetadata(
  "{escape_ts_string(title)}",
  "{escape_ts_string(idea.description)}",
  "/{idea.tool_slug}",
);

export default function {camel_component_name(idea.tool_slug)}Page() {{
  return <{client_component_name(idea.tool_slug)} />;
}}
'''


def render_client_page_tsx(idea: Idea) -> str:
    display_name = display_tool_name(idea)
    examples = [
        f"{idea.input_placeholder}",
        f"Give a concise {display_name.lower()} result for this input.",
        f"Show a practical example {clean_target_user_phrase(idea.target_user)} can use right away.",
        f"Provide a step-by-step output with best practices for this task.",
    ]
    examples_js = ",\n  ".join(json.dumps(example) for example in examples)
    return f'''"use client";

import {{ useState }} from "react";
import Turnstile from "react-turnstile";
import ToolLayout from "@/components/ToolLayout";
import {{ isTurnstileEnabledClient }} from "@/lib/turnstile-flags";

const EXAMPLES = [
  {examples_js}
];

export default function {camel_component_name(idea.tool_slug)}Page() {{
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [turnstileToken, setTurnstileToken] = useState("");
  const [turnstileKey, setTurnstileKey] = useState(0);
  const turnstileEnabled = isTurnstileEnabledClient();
  const siteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;
  const shouldUseTurnstile = turnstileEnabled && Boolean(siteKey);
  const showTurnstileDisabledMessage = process.env.NODE_ENV !== "production";

  async function submitWithToken(token: string | null) {{
    setLoading(true);
    setError("");
    setOutput("");

    try {{
      const res = await fetch("/api/{idea.tool_slug}", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ input, turnstileToken: token || "" }}),
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
      setTurnstileToken("");
      setTurnstileKey((v) => v + 1);
    }}
  }}

  async function onSubmit() {{
    if (loading || !input.trim()) {{
      return;
    }}
    if (turnstileEnabled && !siteKey) {{
      setError("Turnstile is not configured. Set NEXT_PUBLIC_TURNSTILE_SITE_KEY.");
      return;
    }}

    const widgetToken =
      (globalThis as any)?.turnstile?.getResponse?.()?.toString?.().trim?.() ||
      turnstileToken ||
      "";

    if (shouldUseTurnstile && !widgetToken) {{
      setError("Please wait for verification");
      return;
    }}

    await submitWithToken(widgetToken || null);
  }}

  return (
    <ToolLayout title="{escape_ts_string(display_name)}" description="{escape_ts_string(idea.description)}">
      <textarea
        rows={{10}}
        placeholder="{escape_ts_string(idea.input_placeholder)}"
        value={{input}}
        onChange={{(e) => setInput(e.target.value)}}
      />
      <div className="turnstile-wrap">
        {{shouldUseTurnstile ? (
          <Turnstile
            key={{turnstileKey}}
            sitekey={{siteKey!}}
            onVerify={{(token) => setTurnstileToken(token)}}
            onExpire={{() => setTurnstileToken("")}}
            onError={{() => setTurnstileToken("")}}
          />
        ) : turnstileEnabled ? (
          <p className="small">Turnstile is not configured. Set NEXT_PUBLIC_TURNSTILE_SITE_KEY.</p>
        ) : showTurnstileDisabledMessage ? (
          <p className="small">Turnstile disabled by configuration.</p>
        ) : null}}
      </div>
      <button onClick={{onSubmit}} disabled={{loading || !input.trim()}}>
        {{loading ? "Processing..." : "Generate"}}
      </button>

      <section className="tool-section">
        <h2>{escape_ts_string(idea.output_label)}</h2>
        {{error && <pre>{{error}}</pre>}}
        {{output && <pre>{{output}}</pre>}}
        {{!error && !output && <p className="small">Your output will appear here.</p>}}
      </section>

      <section className="tool-section">
        <h2>Example Inputs</h2>
        <p className="small">Click an example to populate the input box.</p>
        <div style={{{{ display: "grid", gap: "0.5rem" }}}}>
          {{EXAMPLES.map((example) => (
            <button
              key={{example}}
              type="button"
              onClick={{() => setInput(example)}}
              style={{{{
                textAlign: "left",
                background: "#ffffff",
                color: "#111827",
                border: "1px solid #d1d5db",
                marginTop: 0,
              }}}}
            >
              {{example}}
            </button>
          ))}}
        </div>
      </section>

      <section className="seo-section">
        <h2>What is {escape_ts_string(display_name)}?</h2>
        <p>
          {escape_ts_string(display_name)} helps {escape_ts_string(clean_target_user_phrase(idea.target_user))}
          {" "}solve a focused task quickly with concise, practical output.
        </p>
        <p>
          This tool is optimized for fast workflows: paste your input, generate output, and adapt it immediately.
        </p>
      </section>

      <section className="seo-section">
        <h2>Common Use Cases</h2>
        <ul>
          <li>Generate a first draft output from raw input in seconds</li>
          <li>Reduce repetitive manual transformations and formatting</li>
          <li>Create consistent outputs aligned to team expectations</li>
          <li>Speed up troubleshooting and decision-making workflows</li>
        </ul>
      </section>

      <section className="seo-section">
        <h2>How It Works</h2>
        <ol>
          <li>Enter your request or raw input.</li>
          <li>Click Generate.</li>
          <li>Review the output and adapt it to your context.</li>
        </ol>
      </section>
    </ToolLayout>
  );
}}
'''


def replace_tokens(text: str, variables: dict[str, Any]) -> str:
    out = text
    for key, value in variables.items():
        out = out.replace(f"{{{{{key}}}}}", str(value))
    return out


def read_template_defaults(template_path: Path) -> dict[str, Any]:
    cfg_path = template_path / "template_config.json"
    if not cfg_path.exists():
        return {}
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    default_cfg = data.get("default_configuration", {})
    return default_cfg if isinstance(default_cfg, dict) else {}


def requires_use_client(content: str) -> bool:
    return bool(
        re.search(
            r"\buse(State|Effect|Memo|Callback|Ref|Reducer|LayoutEffect|Transition|DeferredValue|Id|ImperativeHandle|SyncExternalStore|Optimistic|ActionState)\b",
            content,
        )
    )


def ensure_use_client_directive(content: str) -> str:
    stripped = content.lstrip()
    if stripped.startswith('"use client"') or stripped.startswith("'use client'"):
        return content
    if not requires_use_client(content):
        return content
    return '"use client";\n\n' + content


def normalize_frontend_api_path(content: str, slug: str) -> str:
    return re.sub(
        r"""(['"])\/api\/(?:generate|explain)\1""",
        rf"\1/api/{slug}\1",
        content,
    )


def render_api_route_ts(idea: Idea) -> str:
    return f'''import {{ NextRequest, NextResponse }} from "next/server";
import {{ createChatCompletion }} from "@/lib/openai";
import {{ blockedOriginResponse, isRequestFromAllowedOrigin }} from "@/lib/request-origin";
import {{ verifyTurnstileToken }} from "@/lib/turnstile";

const MAX_REQUESTS_PER_IP = 10;
const MAX_TOKENS = 900;

const DAY_MS = 24 * 60 * 60 * 1000;
const ipCounter = new Map<string, {{ count: number; resetAt: number }}>();

const SYSTEM_PROMPT = `{escape_template_prompt(idea)}`;
const TURNSTILE_ENABLED = process.env.TURNSTILE_ENABLED !== "false";

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

export async function POST(req: NextRequest) {{
  try {{
    if (!isRequestFromAllowedOrigin(req)) {{
      return blockedOriginResponse();
    }}

    const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";

    const ipError = checkIpLimit(ip);
    if (ipError) {{
      return NextResponse.json({{ error: ipError }}, {{ status: 429 }});
    }}

    const body = await req.json();
    const turnstileToken = (body?.turnstileToken || "").toString().trim();
    if (TURNSTILE_ENABLED) {{
      const turnstileResult = await verifyTurnstileToken(turnstileToken, ip);
      if (!turnstileResult.success) {{
        return NextResponse.json({{ error: turnstileResult.error }}, {{ status: 400 }});
      }}
    }}

    const input = (body?.input || "").toString().trim();
    if (!input) {{
      return NextResponse.json({{ error: "Input is required." }}, {{ status: 400 }});
    }}

    const result = await createChatCompletion(
      [
        {{ role: "system", content: SYSTEM_PROMPT }},
        {{ role: "user", content: input }},
      ],
      MAX_TOKENS,
    );

    return NextResponse.json({{ output: result.content }});
  }} catch (err) {{
    const message = err instanceof Error ? err.message : "Unknown server error.";
    const status = message.startsWith("Upstream LLM request failed") ? 502 : 500;
    return NextResponse.json({{ error: message }}, {{ status }});
  }}
}}
'''


def build_deterministic_strategy_prompt(idea: Idea) -> str:
    prompt = load_prompt("deterministic_strategy_prompt.txt")
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
    return prompt.replace("{{idea_json}}", idea_json)


def parse_deterministic_strategy(raw_content: str) -> DeterministicStrategy:
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise GenerationError("Deterministic strategy is not valid JSON") from exc

    for key in ["kind", "description", "transform_function_code"]:
        if key not in data:
            raise GenerationError(f"Deterministic strategy missing field: {key}")

    kind = str(data["kind"]).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_\\-]*", kind):
        raise GenerationError("Deterministic strategy kind is invalid")

    transform_function_code = str(data["transform_function_code"])
    if "function transformInput(input: string): string" not in transform_function_code:
        raise GenerationError(
            "transform_function_code must declare: function transformInput(input: string): string"
        )
    forbidden = [
        "fetch(",
        "createChatCompletion(",
        "process.env",
        "require(",
        "import ",
        "axios",
        "http://",
        "https://",
        "child_process",
        "eval(",
        "Function(",
    ]
    hit = [token for token in forbidden if token in transform_function_code]
    if hit:
        raise GenerationError(
            "Generated deterministic strategy contains forbidden operations: "
            + ", ".join(hit)
        )

    return DeterministicStrategy(
        kind=kind,
        description=str(data["description"]).strip(),
        transform_function_code=transform_function_code.strip(),
    )


def resolve_deterministic_strategy(
    idea: Idea, mode: str, template_name: str | None
) -> DeterministicStrategy:
    haystack = " ".join(
        [
            idea.tool_name.lower(),
            idea.tool_slug.lower(),
            idea.description.lower(),
            idea.llm_task.lower(),
            (template_name or "").lower(),
        ]
    )
    builtin = select_specific_builtin_strategy(haystack)
    if builtin:
        return builtin

    if mode == "llm-generate-deterministic":
        raw = call_openai_compatible(build_deterministic_strategy_prompt(idea))
        return parse_deterministic_strategy(raw)

    return TEXT_CLEANUP


def render_deterministic_api_route_ts(idea: Idea, strategy: DeterministicStrategy) -> str:
    return f'''import {{ NextRequest, NextResponse }} from "next/server";
import {{ blockedOriginResponse, isRequestFromAllowedOrigin }} from "@/lib/request-origin";
import {{ verifyTurnstileToken }} from "@/lib/turnstile";

const MAX_REQUESTS_PER_IP = 10;
const DAY_MS = 24 * 60 * 60 * 1000;
const ipCounter = new Map<string, {{ count: number; resetAt: number }}>();
const TURNSTILE_ENABLED = process.env.TURNSTILE_ENABLED !== "false";
const DETERMINISTIC_KIND = "{strategy.kind}";

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

// Strategy: {escape_ts_string(strategy.description)}
{strategy.transform_function_code}

export async function POST(req: NextRequest) {{
  try {{
    if (!isRequestFromAllowedOrigin(req)) {{
      return blockedOriginResponse();
    }}

    const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
    const ipError = checkIpLimit(ip);
    if (ipError) {{
      return NextResponse.json({{ error: ipError }}, {{ status: 429 }});
    }}

    const body = await req.json();
    const turnstileToken = (body?.turnstileToken || "").toString().trim();
    if (TURNSTILE_ENABLED) {{
      const turnstileResult = await verifyTurnstileToken(turnstileToken, ip);
      if (!turnstileResult.success) {{
        return NextResponse.json({{ error: turnstileResult.error }}, {{ status: 400 }});
      }}
    }}

    const input = (body?.input || "").toString().trim();
    if (!input) {{
      return NextResponse.json({{ error: "Input is required." }}, {{ status: 400 }});
    }}

    const output = transformInput(input);
    return NextResponse.json({{ output }});
  }} catch (err) {{
    const message = err instanceof Error ? err.message : "Unknown server error.";
    return NextResponse.json({{ error: message }}, {{ status: 500 }});
  }}
}}
'''


def render_api_for_idea(
    idea: Idea, mode: str, template_name: str | None
) -> tuple[str, DeterministicStrategy | None]:
    if idea.implementation_mode == "deterministic":
        strategy = resolve_deterministic_strategy(idea, mode, template_name)
        return render_deterministic_api_route_ts(idea, strategy), strategy
    return render_api_route_ts(idea), None


def escape_template_prompt(idea: Idea) -> str:
    lines = [
        f"You are {idea.tool_name}.",
        f"Description: {idea.description}",
        f"Target user: {idea.target_user}",
        f"Task: {idea.llm_task}",
        "Output must be concise, practical, and directly actionable.",
    ]
    profile = select_llm_profile(
        " ".join([idea.tool_name, idea.tool_slug, idea.description, idea.llm_task])
    )
    if profile:
        lines.append(f"Profile: {profile.name}. {profile.instructions}")
    return "\\n".join(lines).replace("`", "'")


def camel_component_name(slug: str) -> str:
    parts = slug.split("-")
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def client_component_name(slug: str) -> str:
    return f"{camel_component_name(slug)}Client"


def clean_target_user_phrase(value: str) -> str:
    return value.strip().rstrip(".!?").lower()


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


def available_templates() -> set[str]:
    if not TEMPLATES_DIR.exists():
        return set()
    return {p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()}


def validate_page_security(page_content: str) -> None:
    required_snippets = [
        'import Turnstile from "react-turnstile";',
        'import { isTurnstileEnabledClient } from "@/lib/turnstile-flags";',
        "const [turnstileToken, setTurnstileToken] = useState(\"\");",
        "const [turnstileKey, setTurnstileKey] = useState(0);",
        "const turnstileEnabled = isTurnstileEnabledClient();",
        "const siteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;",
        "const shouldUseTurnstile = turnstileEnabled && Boolean(siteKey);",
        "globalThis as any)?.turnstile?.getResponse?.()",
        "Please wait for verification",
        "JSON.stringify({ input, turnstileToken: token || \"\" })",
        "setTurnstileToken(\"\");",
        "setTurnstileKey((v) => v + 1);",
        "disabled={loading || !input.trim()}",
        "<Turnstile",
    ]
    missing = [snippet for snippet in required_snippets if snippet not in page_content]
    if missing:
        raise GenerationError(
            "Generated client tool page is missing mandatory Turnstile security requirements."
        )


def validate_client_seo_content(client_content: str) -> None:
    required_snippets = [
        "<ToolLayout title=",
        "<h2>Example Inputs</h2>",
        '<section className="seo-section">',
        "<h2>What is ",
        "<h2>Common Use Cases</h2>",
        "<h2>How It Works</h2>",
    ]
    missing = [snippet for snippet in required_snippets if snippet not in client_content]
    if missing:
        raise GenerationError(
            "Generated client page is missing required SEO sections/content blocks."
        )


def validate_page_seo(page_content: str, idea: Idea) -> None:
    required_snippets = [
        'import { toolMetadata } from "@/lib/seo";',
        "export const metadata = toolMetadata(",
        f'"/{idea.tool_slug}"',
        f'from "./{client_component_name(idea.tool_slug)}"',
    ]
    missing = [snippet for snippet in required_snippets if snippet not in page_content]
    if missing:
        raise GenerationError("Generated page.tsx is missing required SEO metadata wiring.")


def validate_api_security(api_content: str, mode: str) -> None:
    required_snippets = [
        'import { blockedOriginResponse, isRequestFromAllowedOrigin } from "@/lib/request-origin";',
        'import { verifyTurnstileToken } from "@/lib/turnstile";',
        "const TURNSTILE_ENABLED = process.env.TURNSTILE_ENABLED !== \"false\";",
        "if (!isRequestFromAllowedOrigin(req)) {",
        "return blockedOriginResponse();",
        "const turnstileToken = (body?.turnstileToken || \"\").toString().trim();",
        "if (TURNSTILE_ENABLED) {",
        "const turnstileResult = await verifyTurnstileToken(turnstileToken, ip);",
        "if (!turnstileResult.success) {",
        "return NextResponse.json({ error: turnstileResult.error }, { status: 400 });",
    ]
    if mode == "llm":
        required_snippets.extend(
            [
                'import { createChatCompletion } from "@/lib/openai";',
                "const result = await createChatCompletion(",
                'const status = message.startsWith("Upstream LLM request failed") ? 502 : 500;',
            ]
        )
    else:
        forbidden_snippets = [
            "createChatCompletion(",
            "Upstream LLM request failed",
        ]
        if any(snippet in api_content for snippet in forbidden_snippets):
            raise GenerationError(
                "Deterministic route.ts should not depend on LLM-specific helpers."
            )
        required_snippets.extend(
            [
                "const DETERMINISTIC_KIND =",
                "function transformInput(input: string): string",
                "const output = transformInput(input);",
            ]
        )
    missing = [snippet for snippet in required_snippets if snippet not in api_content]
    if missing:
        raise GenerationError(
            "Generated route.ts is missing mandatory origin/Turnstile security requirements."
        )


def resolve_template(template_name: str | None) -> str | None:
    if template_name is None:
        return None
    templates = available_templates()
    if not templates:
        raise GenerationError("No templates found in templates/")
    if template_name not in templates:
        raise GenerationError(f"Unknown template: {template_name}")
    return template_name


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
    updated = updated.replace("},];", "},\n];").replace("}, ];", "},\n];")
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
    updated = updated.replace("},];", "},\n];").replace("}, ];", "},\n];")
    file_path.write_text(updated, encoding="utf-8")
    return True


def validate_output_paths(written_relative_paths: set[str], slug: str) -> None:
    allowed = {
        f"app/{slug}/page.tsx",
        f"app/{slug}/{client_component_name(slug)}.tsx",
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


def update_integration_files(app_dir: Path, idea: Idea) -> None:
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
            f'    label: "{escape_ts_string(display_tool_name(idea))}",\n'
            f'    description: "{escape_ts_string(idea.description)}",\n'
            "  },"
        ),
    )
    updated_tools = upsert_tools_array_entry(
        tools_page,
        f"/{idea.tool_slug}",
        f'\n  {{ href: "/{idea.tool_slug}", label: "{escape_ts_string(display_tool_name(idea))}" }},',
    )
    updated_sitemap = upsert_sitemap_entry(sitemap, idea.tool_slug)

    if not updated_home or not updated_tools or not updated_sitemap:
        raise GenerationError(
            "Integration update failed. One or more files already contained this slug."
        )


def create_route_module(
    app_root: Path, idea: Idea, mode: str, template_name: str | None
) -> set[str]:
    app_dir = app_root / "app"
    route_page = app_dir / idea.tool_slug / "page.tsx"
    route_client = app_dir / idea.tool_slug / f"{client_component_name(idea.tool_slug)}.tsx"
    api_route = app_dir / "api" / idea.tool_slug / "route.ts"

    if route_page.exists() or route_client.exists() or api_route.exists():
        raise GenerationError(f"Route already exists for slug: {idea.tool_slug}")

    page_content = render_page_tsx(idea)
    client_content = render_client_page_tsx(idea)
    api_content, _strategy = render_api_for_idea(idea, mode, template_name)
    validate_page_seo(page_content, idea)
    validate_page_security(client_content)
    validate_client_seo_content(client_content)
    validate_api_security(api_content, idea.implementation_mode)

    write_file(route_page, page_content)
    write_file(route_client, client_content)
    write_file(api_route, api_content)

    update_integration_files(app_dir, idea)

    written = {
        f"app/{idea.tool_slug}/page.tsx",
        f"app/{idea.tool_slug}/{client_component_name(idea.tool_slug)}.tsx",
        f"app/api/{idea.tool_slug}/route.ts",
        "app/page.tsx",
        "app/tools/page.tsx",
        "app/sitemap.ts",
    }
    validate_output_paths(written, idea.tool_slug)
    return written


def create_route_module_from_template(
    app_root: Path, idea: Idea, template_name: str, mode: str
) -> set[str]:
    app_dir = app_root / "app"
    route_page = app_dir / idea.tool_slug / "page.tsx"
    route_client = app_dir / idea.tool_slug / f"{client_component_name(idea.tool_slug)}.tsx"
    api_route = app_dir / "api" / idea.tool_slug / "route.ts"
    if route_page.exists() or route_client.exists() or api_route.exists():
        raise GenerationError(f"Route already exists for slug: {idea.tool_slug}")

    template_dir = TEMPLATES_DIR / template_name
    frontend_src = template_dir / "frontend" / "page.tsx"
    api_src = template_dir / "api" / "route.ts"
    if not frontend_src.exists() or not api_src.exists():
        raise GenerationError(
            f"Template missing required files: {template_name} (frontend/page.tsx and api/route.ts)"
        )

    defaults = read_template_defaults(template_dir)
    variables: dict[str, Any] = {
        **defaults,
        "tool_name": display_tool_name(idea),
        "tool_slug": idea.tool_slug,
        "description": idea.description,
        "target_user": idea.target_user,
        "template": template_name,
    }

    page_content = render_page_tsx(idea)
    client_content = replace_tokens(frontend_src.read_text(encoding="utf-8"), variables)
    client_content = normalize_frontend_api_path(client_content, idea.tool_slug)
    client_content = ensure_use_client_directive(client_content)

    api_content = replace_tokens(api_src.read_text(encoding="utf-8"), variables)

    # Security requirements are mandatory: if template content omits them,
    # fall back to the secure scaffold implementations.
    try:
        validate_page_security(client_content)
        validate_client_seo_content(client_content)
    except GenerationError:
        client_content = render_client_page_tsx(idea)
    try:
        validate_api_security(api_content, idea.implementation_mode)
    except GenerationError:
        api_content, _strategy = render_api_for_idea(idea, mode, template_name)

    validate_page_seo(page_content, idea)

    write_file(route_page, page_content)
    write_file(route_client, client_content)
    write_file(api_route, api_content)
    update_integration_files(app_dir, idea)

    written = {
        f"app/{idea.tool_slug}/page.tsx",
        f"app/{idea.tool_slug}/{client_component_name(idea.tool_slug)}.tsx",
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
    parser.add_argument(
        "--template",
        help="Use a specific template from templates/<name> for page/api generation.",
    )
    parser.add_argument(
        "--idea",
        default="",
        help="Optional idea direction to steer the LLM (for example: 'accessibility checker for forms').",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "deterministic", "llm", "llm-generate-deterministic"],
        default="llm-generate-deterministic",
        help=(
            "Implementation mode. Default is 'llm-generate-deterministic'. "
            "'auto' prefers deterministic routes when feasible. "
            "'llm-generate-deterministic' asks the model to synthesize deterministic transform code "
            "when no builtin strategy matches."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        app_root = resolve_app_root()
        template_name = resolve_template(args.template)
        existing_slugs = existing_tool_slugs(app_root)

        print("Generating idea...")
        idea, _review = generate_idea(
            existing_slugs,
            idea_hint=args.idea,
            template_name=template_name,
            mode=args.mode,
        )

        print(f"Creating child route: /{idea.tool_slug}")
        if template_name:
            written = create_route_module_from_template(
                app_root, idea, template_name, args.mode
            )
        else:
            written = create_route_module(app_root, idea, args.mode, template_name)
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

        append_registry_entry(idea, deployed_url, template_name)
        print("Done.")
        return 0
    except GenerationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
