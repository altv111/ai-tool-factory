"use client";

import { useState } from "react";

export default function Page() {
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit() {
    setLoading(true);
    setError("");
    setOutput("");

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input }),
      });

      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Request failed");
      } else {
        setOutput(data.output || "");
      }
    } catch (e) {
      setError("Network error. Try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <h1>{{tool_name}}</h1>
      <p>{{description}}</p>
      <p className="small">Single-page, stateless tool. No auth, no database.</p>

      <textarea
        rows={9}
        placeholder="Enter text here..."
        value={input}
        onChange={(e) => setInput(e.target.value)}
      />
      <button onClick={onSubmit} disabled={loading || !input.trim()}>
        {loading ? "Processing..." : "Generate"}
      </button>

      {error && <pre>{error}</pre>}
      {output && <pre>{output}</pre>}
    </main>
  );
}
