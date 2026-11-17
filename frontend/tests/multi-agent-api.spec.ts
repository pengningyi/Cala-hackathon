import { expect, test } from "@playwright/test";
import { generateRunReport, getRunEvidenceStatus, streamRunChat } from "../lib/chat-api";

const source = { url: "https://evidence.example.test/investor", label: "Investor evidence", published_at: null, retrieved_at: "2026-11-17T10:00:00Z" };
const answer = { message: "Review this investor [s1].", citations: [source], citation_ids: ["s1"], candidate_ids: ["investor"], model_used: true, tool_steps: [] };

test("report requests bound conversation context and preserve validated source references", async () => {
  const original = globalThis.fetch;
  const controller = new AbortController();
  let requested: { input: string; init: RequestInit | undefined } | undefined;
  globalThis.fetch = async (input, init) => {
    requested = { input: String(input), init };
    return Response.json(answer);
  };
  try {
    const history = Array.from({ length: 12 }, (_, index) => ({ role: "user" as const, content: `${index}: ${"x".repeat(5000)}` }));
    const response = await generateRunReport("run/with space", history, controller.signal);
    expect(requested?.input).toBe("/api/runs/run%2Fwith%20space/report");
    expect(requested?.init?.signal).toBe(controller.signal);
    const body = JSON.parse(String(requested?.init?.body));
    expect(body.history).toHaveLength(8);
    expect(body.history[0].content).toMatch(/^4:/);
    expect(body.history.every((turn: { content: string }) => turn.content.length === 4000)).toBe(true);
    expect(response.citations).toEqual([source]);
    expect(response.citation_ids).toEqual(["s1"]);
    expect(response.evidence_revision).toBe(0);
  } finally { globalThis.fetch = original; }
});

test("report revisions preserve the exact server snapshot and reject malformed counters", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => Response.json({ ...answer, evidence_revision: 7 });
    expect((await generateRunReport("run", [])).evidence_revision).toBe(7);
    globalThis.fetch = async () => Response.json({ ...answer, evidence_revision: "7" });
    await expect(generateRunReport("run", [])).rejects.toThrow("Founder analysis is unavailable");
  } finally { globalThis.fetch = original; }
});

test("evidence status checks are read-only and validate the revision summary", async () => {
  const original = globalThis.fetch;
  const status = { revision: 3, observation_count: 8, updated_at: "2026-11-17T12:00:00Z", truncated: true };
  let requested: { input: string; init: RequestInit | undefined } | undefined;
  try {
    globalThis.fetch = async (input, init) => { requested = { input: String(input), init }; return Response.json(status); };
    expect(await getRunEvidenceStatus("run/id")).toEqual(status);
    expect(requested?.input).toBe("/api/runs/run%2Fid/evidence");
    expect(requested?.init?.method).toBeUndefined();
    expect(requested?.init?.body).toBeUndefined();
    expect(requested?.init?.cache).toBe("no-store");
    globalThis.fetch = async () => Response.json({ ...status, revision: -1 });
    await expect(getRunEvidenceStatus("run")).rejects.toThrow("Could not check for new findings");
  } finally { globalThis.fetch = original; }
});

test("report generation rejects disabled models and redacts server configuration errors", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => Response.json({ ...answer, model_used: false });
    await expect(generateRunReport("run", [])).rejects.toThrow("Founder analysis is unavailable");
    globalThis.fetch = async () => Response.json({ error: { message: "MODEL_PROVIDER is disabled; configure a private key" } }, { status: 503 });
    await expect(generateRunReport("run", [])).rejects.toThrow("You can still copy or download the report snapshot");
  } finally { globalThis.fetch = original; }
});

test("streamed activities accept research attribution and older steps without agent metadata", async () => {
  const original = globalThis.fetch;
  const base = { tool: "search_entities", label: "Finding investor evidence", arguments: {}, status: "done", count: 1, error: null };
  const steps = [{ ...base, id: "old" }, { ...base, id: "research", agent: "research" }, { ...base, id: "unknown", agent: "unknown" }];
  globalThis.fetch = async () => new Response(steps.map(step => `event: step\ndata: ${JSON.stringify(step)}\n\n`).join("")
    + `event: answer\ndata: ${JSON.stringify({ ...answer, tool_steps: steps })}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
  try {
    const received: string[] = [];
    const response = await streamRunChat("run", { message: "Why this investor?", history: [] }, step => received.push(step.id));
    expect(received).toEqual(["old", "research"]);
    expect(response.tool_steps.map(step => step.id)).toEqual(["old", "research"]);
    expect(response.tool_steps[1].agent).toBe("research");
  } finally { globalThis.fetch = original; }
});
