import { NextRequest, NextResponse } from "next/server";

const MAX_REQUESTS_PER_IP = 5;
const MAX_TOKENS = 800;
const GLOBAL_MONTHLY_LIMIT = 5000;

const DAY_MS = 24 * 60 * 60 * 1000;
const MONTH_MS = 30 * DAY_MS;

const ipCounter = new Map<string, { count: number; resetAt: number }>();
let monthCounter = { count: 0, resetAt: Date.now() + MONTH_MS };

const PROMPT_TEMPLATE = `You are {{tool_name}}.
Description: {{description}}
Template: {{template}}
Target role: {{target_role}}
Error type: {{error_type}}
Audience: {{audience}}
Conversion style: {{conversion_style}}
Respond with helpful, concise output.`;

function applyTemplate(raw: string, values: Record<string, string>) {
  return Object.entries(values).reduce(
    (acc, [key, value]) => acc.replaceAll(`{{${key}}}`, value),
    raw,
  );
}

function checkIpLimit(ip: string): string | null {
  const now = Date.now();
  const current = ipCounter.get(ip);

  if (!current || now > current.resetAt) {
    ipCounter.set(ip, { count: 1, resetAt: now + DAY_MS });
    return null;
  }

  if (current.count >= MAX_REQUESTS_PER_IP) {
    return `Rate limit exceeded: max ${MAX_REQUESTS_PER_IP} requests per IP per day.`;
  }

  current.count += 1;
  ipCounter.set(ip, current);
  return null;
}

function checkGlobalLimit(): string | null {
  const now = Date.now();
  if (now > monthCounter.resetAt) {
    monthCounter = { count: 0, resetAt: now + MONTH_MS };
  }

  if (monthCounter.count >= GLOBAL_MONTHLY_LIMIT) {
    return `Service limit exceeded: max ${GLOBAL_MONTHLY_LIMIT} requests per month.`;
  }

  monthCounter.count += 1;
  return null;
}

async function callLLM(userInput: string): Promise<string> {
  const openaiKey = process.env.OPENAI_API_KEY;
  const geminiKey = process.env.GEMINI_API_KEY;
  const apiKey = openaiKey || geminiKey;
  if (!apiKey) {
    return "LLM placeholder: set OPENAI_API_KEY or GEMINI_API_KEY (plus optional OPENAI_MODEL/OPENAI_BASE_URL) to enable real responses.";
  }

  const usingGeminiFallback = Boolean(geminiKey && !openaiKey);
  const baseUrl = (
    process.env.OPENAI_BASE_URL ||
    (usingGeminiFallback
      ? "https://generativelanguage.googleapis.com/v1beta/openai"
      : "https://api.openai.com/v1")
  ).replace(/\/$/, "");
  const model = process.env.OPENAI_MODEL || (usingGeminiFallback ? "gemini-2.0-flash" : "gpt-4o-mini");

  // Exactly one LLM call per API request.
  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      max_tokens: MAX_TOKENS,
      messages: [
        {
          role: "system",
          content: applyTemplate(PROMPT_TEMPLATE, {
            tool_name: "{{tool_name}}",
            description: "{{description}}",
            template: "{{template}}",
            target_role: "{{target_role}}",
            error_type: "{{error_type}}",
            audience: "{{audience}}",
            conversion_style: "{{conversion_style}}",
          }),
        },
        { role: "user", content: userInput },
      ],
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`LLM error: ${errorText}`);
  }

  const data = await response.json();
  return data?.choices?.[0]?.message?.content || "No output generated.";
}

export async function POST(req: NextRequest) {
  try {
    const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";

    const ipError = checkIpLimit(ip);
    if (ipError) {
      return NextResponse.json({ error: ipError }, { status: 429 });
    }

    const globalError = checkGlobalLimit();
    if (globalError) {
      return NextResponse.json({ error: globalError }, { status: 429 });
    }

    const body = await req.json();
    const input = (body?.input || "").toString().trim();

    if (!input) {
      return NextResponse.json({ error: "Input is required." }, { status: 400 });
    }

    const output = await callLLM(input);
    return NextResponse.json({ output });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown server error.";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
