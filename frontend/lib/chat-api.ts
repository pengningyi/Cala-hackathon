import { ApiError } from "./api";
import type { Source } from "./types";

export interface ChatHistoryMessage {
  role: "user" | "assistant";
  content: string;
}

export interface RunChatRequest {
  message: string;
  history: ChatHistoryMessage[];
  selected_investor_id?: string | null;
}

export interface ChatToolStep {
  id: string;
  tool: string;
  label: string;
  arguments: Record<string, unknown>;
  status: "started" | "done" | "failed";
  count: number | null;
  error: string | null;
}

export interface RunChatResponse {
  message: string;
  citations: Source[];
  citation_ids: string[];
  candidate_ids: string[];
  model_used: boolean;
  tool_steps: ChatToolStep[];
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function source(value: unknown): value is Source {
  if (!object(value) || typeof value.url !== "string" || typeof value.retrieved_at !== "string"
      || typeof value.label !== "string" || !(value.published_at === null || typeof value.published_at === "string")) {
    return false;
  }
  try {
    return ["https:", "http:"].includes(new URL(value.url).protocol);
  } catch {
    return false;
  }
}

/** Chat reuses this run's retrieved evidence; model credentials stay on the server. */
export async function askRunChat(
  runId: string,
  request: RunChatRequest,
  signal?: AbortSignal,
): Promise<RunChatResponse> {
  let response: Response;
  try {
    response = await fetch(`/api/runs/${encodeURIComponent(runId)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      signal,
      body: JSON.stringify(request),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("network_error", "Could not reach your round assistant. Please try again.");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError("invalid_chat_response", "The assistant returned an unreadable response. Please try again.", response.status);
  }
  if (!response.ok) {
    const problem = object(payload) && object(payload.error) ? payload.error : null;
    throw new ApiError(
      typeof problem?.code === "string" ? problem.code : "chat_unavailable",
      typeof problem?.message === "string" ? problem.message : `The assistant is unavailable (${response.status}).`,
      response.status,
      problem?.details ?? null,
    );
  }
  return chatResponse(payload, response.status);
}

function toolStep(value: unknown): value is ChatToolStep {
  return object(value) && typeof value.id === "string" && typeof value.tool === "string"
    && typeof value.label === "string" && ["started", "done", "failed"].includes(value.status as string);
}

function chatResponse(payload: unknown, status: number): RunChatResponse {
  if (!object(payload) || typeof payload.message !== "string" || !payload.message.trim()
      || typeof payload.model_used !== "boolean" || !Array.isArray(payload.citations)
      || !payload.citations.every(source) || !Array.isArray(payload.candidate_ids)
      || !payload.candidate_ids.every(id => typeof id === "string")) {
    throw new ApiError("invalid_chat_response", "The assistant response could not be read. Please try again.", status);
  }
  if (!payload.model_used) {
    throw new ApiError("chat_model_unavailable", "Live model chat is unavailable. Configure Codex CLI or a model API on the server and retry.", status);
  }
  const steps = Array.isArray(payload.tool_steps) ? payload.tool_steps.filter(toolStep) : [];
  // Older backends omit citation_ids; inline references then stay as plain text.
  const citationIds = Array.isArray(payload.citation_ids) && payload.citation_ids.every(id => typeof id === "string")
    && payload.citation_ids.length === payload.citations.length ? payload.citation_ids as string[] : [];
  return { ...(payload as unknown as RunChatResponse), tool_steps: steps, citation_ids: citationIds };
}

/** Parse complete `event:`/`data:` frames from an SSE buffer; returns the unparsed remainder. */
export function parseSseFrames(buffer: string): { frames: { event: string; data: string }[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  const frames = parts.map(part => {
    let event = "message";
    const data: string[] = [];
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    return { event, data: data.join("\n") };
  }).filter(frame => frame.data);
  return { frames, rest };
}

/** Streams the agent's Cala lookups as they happen, then resolves with the validated answer. */
export async function streamRunChat(
  runId: string,
  request: RunChatRequest,
  onStep: (step: ChatToolStep) => void,
  signal?: AbortSignal,
): Promise<RunChatResponse> {
  let response: Response;
  try {
    response = await fetch(`/api/runs/${encodeURIComponent(runId)}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      credentials: "same-origin",
      cache: "no-store",
      signal,
      body: JSON.stringify(request),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("network_error", "Could not reach your round assistant. Please try again.");
  }
  if (!response.ok || !response.body) {
    let payload: unknown = null;
    try { payload = await response.json(); } catch { /* unreadable error body */ }
    const problem = object(payload) && object(payload.error) ? payload.error : null;
    // A backend without the streaming route (older build) answers 404 http_error, not run_not_found:
    // fall back to the single-response endpoint instead of failing the question.
    if (response.status === 404 && problem?.code === "http_error") {
      return askRunChat(runId, request, signal);
    }
    throw new ApiError(
      typeof problem?.code === "string" ? problem.code : "chat_unavailable",
      typeof problem?.message === "string" ? problem.message : `The assistant is unavailable (${response.status}).`,
      response.status,
      problem?.details ?? null,
    );
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const { frames, rest } = parseSseFrames(done ? buffer + "\n\n" : buffer);
      buffer = rest;
      for (const frame of frames) {
        let data: unknown;
        try { data = JSON.parse(frame.data); } catch { continue; }
        if (frame.event === "step" && toolStep(data)) onStep(data);
        else if (frame.event === "answer") return chatResponse(data, response.status);
        else if (frame.event === "error" && object(data)) {
          throw new ApiError(typeof data.code === "string" ? data.code : "chat_unavailable",
            typeof data.message === "string" ? data.message : "The assistant could not finish. Please try again.",
            typeof data.status === "number" ? data.status : response.status);
        }
      }
      if (done) break;
    }
  } catch (error) {
    if (error instanceof ApiError || (error instanceof Error && error.name === "AbortError")) throw error;
    throw new ApiError("network_error", "The connection to your round assistant was interrupted. Please try again.");
  } finally {
    reader.releaseLock();
  }
  throw new ApiError("invalid_chat_response", "The assistant ended without an answer. Please try again.", response.status);
}
