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

export interface RunChatResponse {
  message: string;
  citations: Source[];
  candidate_ids: string[];
  model_used: boolean;
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
  if (!object(payload) || typeof payload.message !== "string" || !payload.message.trim()
      || typeof payload.model_used !== "boolean" || !Array.isArray(payload.citations)
      || !payload.citations.every(source) || !Array.isArray(payload.candidate_ids)
      || !payload.candidate_ids.every(id => typeof id === "string")) {
    throw new ApiError("invalid_chat_response", "The assistant response could not be read. Please try again.", response.status);
  }
  if (!payload.model_used) {
    throw new ApiError("chat_model_unavailable", "Live model chat is unavailable. Configure Codex CLI or a model API on the server and retry.", response.status);
  }
  return payload as unknown as RunChatResponse;
}
