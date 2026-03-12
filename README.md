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
- Registry entries include `name`, `route`, `url`, `deployed`, `created_at`.
