# startup-factory

Minimal Python + Next.js generator that creates and deploys small stateless AI web tools.

## Requirements

- Python 3.11
- Node.js 18+
- Vercel CLI installed and authenticated (`vercel login`)
- OpenAI-compatible API credentials

## Environment variables

```bash
# Option A: Gemini (good first manual run)
export GEMINI_API_KEY="..."
# Optional overrides when using Gemini:
# export OPENAI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai"
# export OPENAI_MODEL="gemini-2.0-flash"

# Option B: OpenAI-compatible provider
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o-mini"
# Optional for OpenAI-compatible providers:
export OPENAI_BASE_URL="https://api.openai.com/v1"

# Optional: number of idea generation/review attempts before failing
export IDEA_MAX_ATTEMPTS="3"

# Optional: where generated Next.js tools are created
# Default: ./generated_tools
export GENERATED_TOOLS_DIR="/home/alpha/Workspace/toolforge-site/apps"
```

## Run

```bash
cd startup-factory
python generator.py
```

Expected console flow:

- Generating idea...
- Idea review: ...
- Idea selected: ...
- Creating project...
- Deploying to Vercel...
- Deployment URL: ...

Generated project output is written to `generated_tools/<tool-name>/`.
Deployment metadata is appended to `tools_registry.json`.

To generate directly into another repo folder (example `toolforge-site/apps`):

```bash
cd startup-factory
export GENERATED_TOOLS_DIR="/home/alpha/Workspace/toolforge-site/apps"
python generator.py
```

## Notes

- Template variables like `{{target_role}}` are replaced during generation.
- Idea JSON schema includes `tool_name`, `template`, `description`, `target_user`, and `configuration`.
- Generator uses a second LLM pass to score and accept/reject ideas before build/deploy.
- Generated apps are single-page, stateless, no-auth, no-database.
- API route enforces:
  - `MAX_REQUESTS_PER_IP = 5` per day
  - `MAX_TOKENS = 800`
  - `GLOBAL_MONTHLY_LIMIT = 5000`
- Registry entries include:
  - `name`
  - `url`
  - `template`
  - `created_at`
