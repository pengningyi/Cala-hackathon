import type {
  FounderInput,
  CompanyMatches,
  OnboardingConfirmation,
  Health,
  ProgressEvent,
  Resolution,
  ResolveRequest,
  RunResult,
  RunStatus,
  StartRunRequest,
} from "./types";

/** The browser uses the Next.js /api rewrite; credentials stay in the backend. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;

  constructor(code: string, message: string, status = 0, details: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

async function fetchApi(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, cache: "no-store", credentials: "same-origin" });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("network_error", "Could not reach RoundCloser. Check the connection and try again.");
  }
  if (!response.ok) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    const problem = object(payload) && object(payload.error) ? payload.error : null;
    throw new ApiError(
      typeof problem?.code === "string" ? problem.code : "http_error",
      typeof problem?.message === "string" ? problem.message : `Backend request failed (${response.status}).`,
      response.status,
      problem?.details ?? null,
    );
  }
  return response;
}

async function readJson<T>(response: Response): Promise<T> {
  try {
    const payload: unknown = await response.json();
    if (!object(payload)) throw new Error("Expected an object");
    return payload as T;
  } catch {
    throw new ApiError("invalid_response", "The backend returned an unreadable response. Please try again.", response.status);
  }
}

export async function resolveProfile(profile: FounderInput, lookupStartup = false): Promise<Resolution> {
  const body: ResolveRequest = { profile, lookup_startup: lookupStartup };
  return readJson<Resolution>(await fetchApi("/api/resolve", { method: "POST", body: JSON.stringify(body) }));
}

export async function findCompanyMatches(companyName: string, leadName: string, signal?: AbortSignal): Promise<CompanyMatches> {
  return readJson<CompanyMatches>(await fetchApi("/api/onboarding/matches", {
    method: "POST", body: JSON.stringify({ company_name: companyName, lead_name: leadName }), signal,
  }));
}

export async function confirmCompanyMatches(matchId: string, startupId: string, leadId: string, signal?: AbortSignal): Promise<OnboardingConfirmation> {
  return readJson<OnboardingConfirmation>(await fetchApi(`/api/onboarding/matches/${encodeURIComponent(matchId)}/confirm`, {
    method: "POST", body: JSON.stringify({ startup_id: startupId, lead_id: leadId }), signal,
  }));
}

export async function startRun(resolutionId: string, leadId: string | null, profile: FounderInput): Promise<RunStatus> {
  const body: StartRunRequest = { resolution_id: resolutionId, lead_id: leadId, profile_confirmed: true, profile };
  return readJson<RunStatus>(await fetchApi("/api/runs", { method: "POST", body: JSON.stringify(body) }));
}

export async function getRunResult(id: string): Promise<RunResult | null> {
  const response = await fetchApi(`/api/runs/${encodeURIComponent(id)}/result`);
  return response.status === 202 ? null : readJson<RunResult>(response);
}

export async function getRunStatus(id: string): Promise<RunStatus> {
  return readJson<RunStatus>(await fetchApi(`/api/runs/${encodeURIComponent(id)}`));
}

export async function getHealth(): Promise<Health> {
  return readJson<Health>(await fetchApi("/api/health"));
}

const eventTypes = ["progress", "tool_call", "tool_result", "retry", "notice", "loops", "complete"] as const;

function parseEvent(message: MessageEvent<string>): ProgressEvent {
  const value: unknown = JSON.parse(message.data);
  if (
    !object(value) || !Number.isInteger(value.id) || (value.id as number) <= 0 ||
    typeof value.type !== "string" || typeof value.message !== "string" ||
    typeof value.step !== "string" || typeof value.at !== "string"
  ) {
    throw new Error("Invalid progress event");
  }
  if (message.lastEventId && Number(message.lastEventId) !== value.id) {
    throw new Error("Inconsistent progress cursor");
  }
  return value as unknown as ProgressEvent;
}

/**
 * EventSource retains Last-Event-ID across reconnects. Local deduplication also
 * protects against proxy replay. The complete event is delivered before closing.
 */
export function subscribeRunEvents(
  id: string,
  onEvent: (event: ProgressEvent) => void,
  onError?: (message: string) => void,
): () => void {
  if (typeof EventSource === "undefined") {
    onError?.("Live progress is unavailable in this browser. The run can still be retrieved.");
    return () => {};
  }
  const source = new EventSource(`/api/runs/${encodeURIComponent(id)}/events`);
  let closed = false;
  let lastId = 0;
  let connectionErrorReported = false;

  function close() {
    if (closed) return;
    closed = true;
    source.close();
    for (const name of eventTypes) source.removeEventListener(name, receive);
    source.onopen = null;
    source.onerror = null;
  }

  function receive(raw: Event) {
    if (closed) return;
    let event: ProgressEvent;
    try {
      event = parseEvent(raw as MessageEvent<string>);
    } catch {
      close();
      onError?.("A progress update could not be read. Retrieve the run result to continue.");
      return;
    }
    if (event.id <= lastId) return;
    lastId = event.id;
    if (event.type === "complete") {
      try {
        onEvent(event);
      } finally {
        close();
      }
    } else {
      onEvent(event);
    }
  }

  for (const name of eventTypes) source.addEventListener(name, receive);
  source.onopen = () => { connectionErrorReported = false; };
  source.onerror = () => {
    if (closed || connectionErrorReported) return;
    connectionErrorReported = true;
    onError?.(source.readyState === EventSource.CLOSED
      ? "Live progress disconnected. The run result can still be retrieved."
      : "Live progress interrupted; reconnecting automatically.");
  };
  return close;
}
