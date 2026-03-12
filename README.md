# startup-factory

Python + Next.js generator that adds small AI tools as child routes inside an existing app.

## Requirements

- Python 3.11+
- Node.js 18+
- OpenAI-compatible API credentials
- Optional: Vercel CLI authenticated (`vercel login`) if you use `--deploy`

## Environment variables

```bash
# Option A: Gemini
export GEMINI_API_KEY="..."
# Optional Gemini overrides:
# export OPENAI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai"
# export OPENAI_MODEL="gemini-2.0-flash"

# Option B: OpenAI-compatible provider
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o-mini"
# Optional provider override:
# export OPENAI_BASE_URL="https://api.openai.com/v1"

# Optional: max idea/review retries
export IDEA_MAX_ATTEMPTS="3"

# Optional Turnstile toggles for generated routes
# Client-side: disable widget + client token requirement
export NEXT_PUBLIC_TURNSTILE_ENABLED="false"
# Server-side: disable token verification
export TURNSTILE_ENABLED="false"

# Preferred: host app root containing app/layout.tsx
export APP_ROOT_DIR="/home/alpha/Workspace/tooldeck-site"

# Backward-compatible fallback if APP_ROOT_DIR is unset:
# if this points to /path/to/site/app, generator infers app root as /path/to/site
export GENERATED_TOOLS_DIR="/home/alpha/Workspace/tooldeck-site/app"
```

## Run

```bash
cd startup-factory
python3 generator.py
```

Default behavior:

- Generates one route module:
  - `app/<tool-slug>/page.tsx`
  - `app/api/<tool-slug>/route.ts`
- Updates host integration files:
  - `app/page.tsx`
  - `app/tools/page.tsx`
  - `app/sitemap.ts`
- Skips deployment unless `--deploy` is passed.

Template-driven behavior:

- Pass `--template <name>` to generate the child route from `templates/<name>/frontend/page.tsx` and `templates/<name>/api/route.ts`.
- Template token placeholders are filled using generated idea fields plus template defaults.
- API calls in template frontend are normalized to `/api/<tool-slug>`.

Steer specific ideas:

```bash
python3 generator.py --template regex_explainer --idea "Regex debugging helper for interview prep"
```

Deploy host app:

```bash
python3 generator.py --deploy
```

## Generate new templates with LLM

Use the template generator to create a new reusable template under `templates/<template_name>/`.

```bash
python3 template_generator.py --idea "Regex explainer for junior developers"
```

Useful flags:

- `--dry-run`
- `--print-manifest`
- `--overwrite`
- `--max-attempts 5`

## Notes

- Generation contract is constrained to child-route modules, not standalone app skeletons.
- Duplicate slugs are rejected.
- Forbidden standalone files are blocked (`package.json`, `next.config.js`, `tsconfig.json`, `next-env.d.ts`, nested `layout.tsx`, etc.).
- Security requirements are mandatory in generated files:
  - same-origin API gate via `@/lib/request-origin`
  - Turnstile verification via `@/lib/turnstile`
  - frontend Turnstile token flow via `react-turnstile`
- Registry entries include `name`, `route`, `url`, `deployed`, `created_at`.
