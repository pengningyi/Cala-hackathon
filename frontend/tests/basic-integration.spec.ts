import { expect, test, type Page } from "@playwright/test";
import type { RunChatRequest } from "../lib/chat-api";
import type { CandidateCard, Entity, FounderInput, FundingRound, RunResult, Source, StartRunRequest } from "../lib/types";

/** SSE body: one `step` frame per tool step, then the `answer` (or an `error`). */
function sse(answer: unknown, steps: unknown[] = [], error?: unknown): string {
  return steps.map(step => `event: step\ndata: ${JSON.stringify(step)}\n\n`).join("")
    + (error ? `event: error\ndata: ${JSON.stringify(error)}\n\n` : `event: answer\ndata: ${JSON.stringify(answer)}\n\n`);
}

const source: Source = { url: "https://synthetic.example.test/shared-round", label: "Synthetic round evidence",
  published_at: "2026-10-01", retrieved_at: "2026-11-14T00:00:00Z" };
const entity = (id: string, name: string): Entity => ({ id, name, kind: "vc", country: "Finland",
  website: null, description: "Synthetic integration fixture.", aliases: [], related_entity_ids: [], sources: [source] });
const lead = entity("lead", "Synthetic Lead");
const company = entity("startup", "Cala AI");
const shared: FundingRound = { id: "identified-event", company: entity("portfolio", "Synthetic Portfolio"),
  event_date: "2026-10-01", event_date_precision: "day", stage: "seed", sector: "AI infrastructure",
  company_country: "Finland", total_amount_eur: "6000000",
  participants: [lead, entity("first", "First Fund"), entity("second", "Second Fund"), entity("review", "Review Fund")]
    .map(investor => ({ investor, is_lead: investor.id === "lead" })), sources: [source] };

function card(id: string, name: string, flagged = false): CandidateCard {
  const investor = entity(id, name);
  return { investor, fit_score: flagged ? 90 : id === "first" ? 80 : 65, profile_completeness: .7,
    explanation: "Synthetic main-scoring result.", shared_round_count: 1, shared_company_count: 1, shared_deals: [shared],
    recent_rounds_12m: 2, most_recent_investment: shared.event_date, components: [{ name: "sector_fit", weight: 15,
      value: 0, known: false, numerator: null, denominator: null, explanation: "Sector is unknown.", sources: [] }],
    considerations: [], statuses: [], profile: { investor, rounds: [shared], observations: [], activity_window_complete: false,
      ticket_criteria: [], mandates: [], assessments: [], sources: [source] }, sources: [source], action: "introduction",
    draft: `Please introduce Cala AI to ${name}; current-round lead commitment is unconfirmed.`,
    review_status: flagged ? "flagged" : "short", flags: flagged ? [{ kind: "stage", severity: "grey",
      reason: "Rarely invests at Seed in the retrieved history.", company: null, sources: [source] }] : [],
    estimated_ticket_eur: id === "first" ? "1000000" : id === "second" ? "500000" : null,
    ticket_estimate_basis: "equal-share historical EUR round estimate" };
}

function result(profile: FounderInput): RunResult {
  return { run_id: "basic-run", state: "partial", profile, lead,
    confirmed_at: "2026-11-14T00:00:00Z", finished_at: "2026-11-14T00:00:01Z", remaining_allocation_eur: "2000000",
    lookback_months: 60, candidates: [card("first", "First Fund"), card("second", "Second Fund"), card("review", "Review Fund", true)],
    exclusions: [], incomplete: [], counts: { discovered: 3, profiled: 3, shortlisted: 2, flagged: 1, returned: 3 },
    notices: ["Synthetic partial coverage."], combined_draft: "Please introduce Cala AI to First Fund and Second Fund.",
    processing_ms: 1000, cala_calls: 0, cache_hits: 0, retrieval_timestamps: [source.retrieved_at],
    scoring_version: "main-1fed306", error: null,
    allocation: [{ investor_id: "first", name: "First Fund", amount_eur: "1000000", percentage: 50, basis: "equal-share estimate" },
      { investor_id: "second", name: "Second Fund", amount_eur: "500000", percentage: 25, basis: "equal-share estimate" },
      { investor_id: null, name: "Still open", amount_eur: "500000", percentage: 25, basis: "unallocated" }] };
}

async function mockApi(page: Page, prefillOverrides: Partial<{ website: string; sector: string; country: string }> = {}) {
  let matchingRequest: { company_name: string; lead_name: string } | null = null;
  let confirmationRequest: { startup_id: string; lead_id: string } | null = null;
  let runRequest: StartRunRequest | null = null;
  let resultReads = 0;
  const chatRequests: RunChatRequest[] = [];
  await page.route("**/api/health", route => route.fulfill({ json: { status: "healthy", cala_configured: true, model_provider: "disabled" } }));
  await page.route("**/api/onboarding/matches", route => {
    matchingRequest = route.request().postDataJSON();
    return route.fulfill({ json: { id: "basic-matches", lead_matches: [lead], startup_matches: [company],
      resolver_ms: 100, created_at: source.retrieved_at, expires_at: new Date(Date.now() + 3600000).toISOString(), notices: [] } });
  });
  await page.route("**/api/onboarding/matches/basic-matches/confirm", route => {
    confirmationRequest = route.request().postDataJSON();
    return route.fulfill({ json: { resolution_id: "basic-confirmation", startup: company, lead,
      prefill: { company_name: company.name, website: "https://cala.ai/", sector: "AI infrastructure", country: "Finland", ...prefillOverrides },
      prefill_sources: { company_name: [source], website: [source], sector: [source], country: [source] },
      expires_at: new Date(Date.now() + 3600000).toISOString(), notices: [] } });
  });
  await page.route("**/api/runs", route => {
    runRequest = route.request().postDataJSON() as StartRunRequest;
    return route.fulfill({ status: 202, json: { id: "basic-run", state: "running", confirmed_at: source.retrieved_at,
      event_count: 1, result_url: "/api/runs/basic-run/result", events_url: "/api/runs/basic-run/events" } });
  });
  await page.route("**/api/runs/basic-run/result", route => {
    resultReads += 1;
    return resultReads === 1 ? route.fulfill({ status: 202, json: { id: "basic-run", state: "running" } })
      : route.fulfill({ json: result(runRequest!.profile) });
  });
  await page.route("**/api/runs/basic-run/events", route => route.fulfill({ contentType: "text/event-stream", body:
    `id: 1\nevent: tool_result\ndata: ${JSON.stringify({ id: 1, type: "tool_result", at: source.retrieved_at, step: "profiles", message: "Using cached Cala response", tool: "entity_retrieval", query: { entity_id: "synthetic-lead" }, cached: true, count: 1 })}\n\n`
    + `id: 2\nevent: complete\ndata: ${JSON.stringify({ id: 2, type: "complete", at: source.retrieved_at, step: "complete", message: "Synthetic run complete" })}\n\n` }));
  await page.route("**/api/runs/basic-run/chat/stream", route => {
    const request = route.request().postDataJSON() as RunChatRequest;
    chatRequests.push(request);
    return route.fulfill({ contentType: "text/event-stream", body: sse({ message: `Live fixture answer about ${request.selected_investor_id || "the round"}.`,
      citations: [source], candidate_ids: request.selected_investor_id ? [request.selected_investor_id] : ["first"], model_used: true, tool_steps: [] }) });
  });
  return { matching: () => matchingRequest!, confirmation: () => confirmationRequest!, run: () => runRequest, chat: () => chatRequests };
}

async function confirmCompanies(page: Page) {
  await page.getByRole("button", { name: "Find matching companies", exact: true }).click();
  await page.locator(".entity-choice").filter({ hasText: company.name }).click();
  await page.locator(".entity-choice").filter({ hasText: lead.name }).click();
  await page.getByRole("button", { name: "Confirm companies", exact: true }).click();
  await expect(page.getByLabel("Sector", { exact: true })).toBeVisible();
}

async function completeRound(page: Page) {
  await page.getByLabel("Sector", { exact: true }).fill("AI infrastructure");
  await page.getByLabel("Round stage", { exact: true }).selectOption("seed");
  await page.getByLabel(/^Round target/).fill("4M");
  await page.getByLabel(/^Already committed/).fill("2M");
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
}

test("identity confirmation precedes sourced prefills and the completed round submission", async ({ page }) => {
  const captured = await mockApi(page);
  await page.goto("/");
  await expect(page.getByLabel("Company name", { exact: true })).toHaveValue("Cala AI");
  await expect(page.getByLabel("Lead investor company name", { exact: true })).toHaveValue("Lightspeed Venture Partners");
  await expect(page.getByLabel(/^Company website/)).toHaveCount(0);
  await expect(page.getByLabel("Sector", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel(/^Round target/)).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Workflow" }).getByRole("button", { name: "Connections", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Find matching companies", exact: true }).click();
  expect(captured.matching()).toEqual({ company_name: "Cala AI", lead_name: "Lightspeed Venture Partners" });
  await expect(page.locator(".entity-choice").first()).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".entity-choice").last()).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("button", { name: "Confirm companies", exact: true })).toBeDisabled();
  await expect(page.getByLabel(/^Company website/)).toHaveCount(0);
  expect(captured.run()).toBeNull();
  await page.locator(".entity-choice").filter({ hasText: company.name }).click();
  await expect(page.getByRole("button", { name: "Confirm companies", exact: true })).toBeDisabled();
  await page.locator(".entity-choice").filter({ hasText: lead.name }).click();
  await expect(page.getByLabel("I confirm these are the correct companies.", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Confirm companies", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Confirm companies", exact: true }).click();
  expect(captured.confirmation()).toEqual({ startup_id: company.id, lead_id: lead.id });
  await expect(page.getByLabel(/^Company website/)).toHaveValue("https://cala.ai/");
  await expect(page.getByLabel("Sector", { exact: true })).toHaveValue("AI infrastructure");
  await expect(page.getByLabel(/^Headquarters country/)).toHaveValue("Finland");
  await expect(page.getByLabel("Round stage", { exact: true })).toHaveValue("");
  await expect(page.getByLabel(/^Round target/)).toHaveValue("");
  await expect(page.getByLabel(/^Already committed/)).toHaveValue("");
  await completeRound(page);
  await page.getByLabel(/^Company website/).fill("cala.ai");
  await page.getByLabel(/^Already committed/).fill("2,000K");
  await page.getByLabel(/^Investors already in/).fill("Existing One, Existing Two");
  await page.getByLabel(/^Known competitors/).fill("Competitor One");
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await expect(page.locator(".shortlist-heading")).toBeVisible();
  expect(captured.run()!.profile.lead_name).toBe(lead.name);
  expect(captured.run()!.profile.startup.cala_entity_id).toBe(company.id);
  expect(captured.run()!.profile.startup.website).toBe("https://cala.ai/");
  expect(captured.run()!.profile.round.target_eur).toBe("4000000.00");
  expect(captured.run()!.profile.round.firm_commitments_eur).toBe("2000000.00");
  expect(captured.run()!.profile.excluded_investors).toEqual(["Existing One", "Existing Two"]);
});

test("unavailable company facts remain empty and editable after confirmation", async ({ page }) => {
  const captured = await mockApi(page, { website: "", sector: "", country: "" });
  await page.goto("/");
  await confirmCompanies(page);
  for (const title of [/^Company website/, /^Sector$/, /^Headquarters country/, /^Round target/, /^Already committed/]) {
    await expect(page.getByLabel(title)).toHaveValue("");
  }
  await page.getByLabel(/^Company website/).fill("founder-supplied.example");
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await expect(page.locator(".shortlist-heading")).toBeVisible();
  expect(captured.run()!.profile.startup.website).toBe("https://founder-supplied.example/");
});

test("changing companies clears the confirmed identity and old prefills", async ({ page }) => {
  const captured = await mockApi(page);
  await page.goto("/");
  await confirmCompanies(page);
  await page.getByLabel(/^Company website/).fill("old-company.example");
  await page.getByLabel(/^Round target/).fill("7M");
  await page.getByRole("button", { name: "Change companies", exact: true }).click();
  await expect(page.getByRole("button", { name: "Find matching companies", exact: true })).toBeVisible();
  await expect(page.getByLabel(/^Round target/)).toHaveCount(0);
  await confirmCompanies(page);
  await expect(page.getByLabel(/^Company website/)).toHaveValue("https://cala.ai/");
  await expect(page.getByLabel(/^Round target/)).toHaveValue("");
  expect(captured.run()).toBeNull();
});

test("confirmation errors preserve choices without applying partial prefills", async ({ page }) => {
  await mockApi(page);
  let confirmations = 0;
  await page.route("**/api/onboarding/matches/basic-matches/confirm", route => {
    confirmations += 1;
    return confirmations === 1
      ? route.fulfill({ status: 503, json: { error: { code: "temporary_confirmation_error", message: "Please retry company confirmation." } } })
      : route.fallback();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Find matching companies", exact: true }).click();
  await page.locator(".entity-choice").filter({ hasText: company.name }).click();
  await page.locator(".entity-choice").filter({ hasText: lead.name }).click();
  await page.getByRole("button", { name: "Confirm companies", exact: true }).click();
  await expect(page.locator(".error-banner[role='alert']")).toContainText("Please retry company confirmation.");
  await expect(page.getByLabel(/^Company website/)).toHaveCount(0);
  await expect(page.locator(".entity-choice").first()).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Confirm companies", exact: true }).click();
  await expect(page.getByLabel(/^Company website/)).toHaveValue("https://cala.ai/");
});

test("combined workspace retains graph and copy requests while showing main allocation and review flags", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const captured = await mockApi(page);
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  const shortlist = page.locator(".workspace-shortlist");
  await expect(shortlist.getByRole("heading", { name: "Your top investors", exact: true })).toBeVisible();
  await expect(shortlist.locator(".shortlist-heading p")).toContainText("estimated allocation");
  await expect(page.locator(".workspace-allocation")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Estimated allocation", exact: true })).toHaveCount(0);
  const firstAllocation = shortlist.locator(".workspace-investor-row").filter({ hasText: "First Fund" }).locator(".workspace-investor-allocation");
  const secondAllocation = shortlist.locator(".workspace-investor-row").filter({ hasText: "Second Fund" }).locator(".workspace-investor-allocation");
  await expect(firstAllocation).toContainText("€1,000,000.00");
  await expect(firstAllocation).toContainText(/50(?:\.0)?%/);
  await expect(firstAllocation.getByRole("progressbar", { name: "First Fund: estimated share of open allocation" })).toHaveAttribute("aria-valuenow", "50");
  await expect(secondAllocation).toContainText("€500,000.00");
  await expect(secondAllocation).toContainText(/25(?:\.0)?%/);
  await expect(secondAllocation.getByRole("progressbar", { name: "Second Fund: estimated share of open allocation" })).toHaveAttribute("aria-valuenow", "25");
  await expect(shortlist.locator(".workspace-investor-row").filter({ hasText: "Review Fund" }).locator(".workspace-investor-allocation")).toContainText("Not allocated");
  const allocationSummary = shortlist.locator(".workspace-allocation-summary");
  await expect(allocationSummary.locator(".workspace-allocation-row")).toHaveCount(1);
  await expect(allocationSummary).toContainText("Still open");
  await expect(allocationSummary).toContainText("€500,000.00");
  await expect(allocationSummary).toContainText(/25(?:\.0)?%/);
  await expect(allocationSummary.getByRole("progressbar", { name: "Still open: unallocated share of open allocation" })).toHaveAttribute("aria-valuenow", "25");
  const allocationNote = allocationSummary.locator("details.workspace-allocation-note");
  await allocationNote.locator("summary").click();
  await expect(allocationNote).toContainText("Actual individual checks and willingness to invest are unconfirmed");
  await allocationNote.locator("summary").click();
  await expect(page.locator(".result-meta")).toContainText("2 ready");
  await expect(page.locator(".workspace-investor-row").last()).toContainText("Needs review");
  await expect(page.getByRole("region", { name: "Founder and investor knowledge graph" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Chat", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Your round assistant", exact: true })).toBeVisible();
  await expect(page.getByLabel("Ask about your investors", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Agent activity", exact: true }).click();
  await expect(page.locator(".agent-insight-activity .trace-event").first()).toContainText("cache hit");
  await page.locator(".agent-insight-activity .trace-event").first().locator("summary").click();
  await expect(page.locator(".agent-insight-activity pre")).toContainText("synthetic-lead");
  await page.getByRole("tab", { name: "Chat", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Agent activity", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(page.getByRole("tab", { name: "Chat", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "Select Second Fund", exact: true }).click();
  await expect(page.locator(".rc-chat-selection")).toContainText("Second Fund");
  await page.getByLabel("Ask about your investors", { exact: true }).fill("Why is this investor a fit?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".rc-chat-message-assistant").last()).toContainText("Live fixture answer about second.");
  await expect(page.locator(".rc-chat-answer-meta")).toContainText("Live model response");
  expect(captured.chat()[0]).toEqual({ message: "Why is this investor a fit?", selected_investor_id: "second", history: [] });
  await page.getByRole("button", { name: "Copy 2-investor request", exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("Please introduce Cala AI to First Fund and Second Fund.");
  await page.getByRole("button", { name: "View details for First Fund", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Unknown components contribute zero points");
  await expect(dialog.locator(".component-track > span")).toHaveAttribute("style", "width: 0%;");
  await expect(dialog).toContainText("Equal-share ticket estimate");
  await dialog.getByRole("button", { name: "Copy introduction request", exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("Please introduce Cala AI to First Fund");
  await page.getByRole("button", { name: "Close investor details", exact: true }).click();
  await page.getByRole("button", { name: "View details for Review Fund", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Rarely invests at Seed");
  await page.getByRole("button", { name: "Close investor details", exact: true }).click();
  await page.screenshot({ path: ".qa/basic-workspace-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: ".qa/basic-workspace-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("negative round amounts are rejected before starting investor discovery", async ({ page }) => {
  const captured = await mockApi(page);
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel(/^Round target/).fill("-4M");
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await expect(page.locator(".error-banner[role='alert']")).toContainText("Round target must be a non-negative EUR amount");
  expect(captured.run()).toBeNull();
});

test("live chat preserves history and draft across tabs and retries model failures without duplicate turns", async ({ page }) => {
  await mockApi(page);
  const requests: RunChatRequest[] = [];
  await page.route("**/api/runs/basic-run/chat/stream", async route => {
    const request = route.request().postDataJSON() as RunChatRequest;
    requests.push(request);
    if (requests.length === 2) {
      return route.fulfill({ status: 503, json: { error: { code: "chat_provider_unavailable", message: "The model is temporarily unavailable. Retry your question." } } });
    }
    return route.fulfill({ contentType: "text/event-stream", body: sse({ message: requests.length === 1 ? "First live model answer." : "Follow-up live model answer.",
      citations: [source], candidate_ids: ["first"], model_used: true, tool_steps: [] }) });
  });
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  const input = page.getByLabel("Ask about your investors", { exact: true });
  await input.fill("Which introduction should I request first?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".rc-chat-message-assistant").last()).toContainText("First live model answer.");
  await input.fill("What should I ask them next?");
  await page.getByRole("tab", { name: "Agent activity", exact: true }).click();
  await page.getByRole("tab", { name: "Chat", exact: true }).click();
  await expect(input).toHaveValue("What should I ask them next?");
  await expect(page.locator(".rc-chat-message-assistant").last()).toContainText("First live model answer.");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".rc-chat-error")).toContainText("The model is temporarily unavailable");
  await expect(input).toHaveValue("What should I ask them next?");
  await page.getByRole("button", { name: "Retry question", exact: true }).click();
  await expect(page.locator(".rc-chat-message-assistant").last()).toContainText("Follow-up live model answer.");
  expect(requests).toHaveLength(3);
  expect(requests[1].history).toEqual([
    { role: "user", content: "Which introduction should I request first?" },
    { role: "assistant", content: "First live model answer." },
  ]);
  expect(requests[2]).toEqual(requests[1]);
  await expect(page.locator(".rc-chat-message-user")).toHaveCount(2);
  await expect(page.locator(".rc-chat-message-failed")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 900 });
  await expect(input).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
});

test("agent chat shows the Cala lookups it ran and surfaces streamed errors", async ({ page }) => {
  await mockApi(page);
  const step = (id: string, status: string, label: string, count: number | null, error: string | null = null) =>
    ({ id, tool: "search_entities", label, arguments: {}, status, count, error });
  const steps = [step("t1", "started", "Searching Cala for investors named \u201cAcme\u201d", null),
    step("t1", "done", "Searching Cala for investors named \u201cAcme\u201d", 3),
    step("t2", "started", "Reading investor profile of Acme Capital", null),
    step("t2", "failed", "Reading investor profile of Acme Capital", null, "Cala lookup budget for this chat is used up")];
  let calls = 0;
  await page.route("**/api/runs/basic-run/chat/stream", route => {
    calls += 1;
    if (calls === 2) {
      return route.fulfill({ contentType: "text/event-stream", body: sse(null, steps.slice(0, 2),
        { code: "chat_timeout", message: "The assistant took too long to answer. Please retry.", status: 504 }) });
    }
    return route.fulfill({ contentType: "text/event-stream", body: sse({ message: "Acme Capital co-invested with your lead (live Cala lookup).",
      citations: [source], candidate_ids: ["first"], model_used: true, tool_steps: [steps[1], steps[3]] }, steps) });
  });
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  const input = page.getByLabel("Ask about your investors", { exact: true });
  await input.fill("Who else co-invests with my lead?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  const answer = page.locator(".rc-chat-message-assistant").last();
  await expect(answer).toContainText("Acme Capital co-invested");
  await expect(answer.locator(".rc-chat-answer-meta")).toContainText("Live model + research activity");
  const lookups = answer.locator(".rc-chat-lookups");
  await expect(lookups).toContainText("2 activities during this answer");
  await lookups.locator("summary").click();
  await expect(lookups.locator(".rc-chat-step-done")).toContainText("3 results");
  await expect(lookups.locator(".rc-chat-step-failed")).toContainText("budget");
  await input.fill("And who led their last round?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".rc-chat-error")).toContainText("took too long");
  await expect(input).toHaveValue("And who led their last round?");
});

test("chat falls back to the single-response endpoint when the backend has no streaming route", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/runs/basic-run/chat/stream", route =>
    route.fulfill({ status: 404, json: { error: { code: "http_error", message: "Not Found", details: null } } }));
  await page.route("**/api/runs/basic-run/chat", route => route.fulfill({ json: {
    message: "Answer from the older chat endpoint.", citations: [source], candidate_ids: [], model_used: true } }));
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await page.getByLabel("Ask about your investors", { exact: true }).fill("How do I reach or contact K Fund?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".rc-chat-message-assistant").last()).toContainText("Answer from the older chat endpoint.");
  await expect(page.locator(".rc-chat-message-failed")).toHaveCount(0);
});

test("chat attributes delegated research activities without counting them as Cala lookups", async ({ page }) => {
  await mockApi(page);
  const steps = [
    { id: "delegate", tool: "request_research", label: "Checking missing portfolio evidence", arguments: {}, status: "done", count: 1, error: null, agent: "chat" },
    { id: "research", tool: "entity_retrieval", label: "Reading the investor profile", arguments: {}, status: "done", count: 1, error: null, agent: "research" },
  ];
  await page.route("**/api/runs/basic-run/chat/stream", route => route.fulfill({ contentType: "text/event-stream", body: sse({
    message: "The retrieved profile supports this introduction route.", citations: [source], citation_ids: ["s1"], candidate_ids: [], model_used: true, tool_steps: steps }, steps) }));
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await page.getByLabel("Ask about your investors", { exact: true }).fill("Can you check their portfolio?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  const activity = page.locator(".rc-chat-message-assistant").last().locator(".rc-chat-lookups");
  await expect(activity).toContainText("2 activities during this answer");
  await activity.locator("summary").click();
  await expect(activity).toContainText("Round assistant: Checking missing portfolio evidence");
  await expect(activity).toContainText("Research assistant: Reading the investor profile");
  await expect(activity).not.toContainText("request_research");
});

test("assistant answers render markdown with numbered inline citations", async ({ page }) => {
  await mockApi(page);
  const markdown = "**Short answer: use your existing relationship.**\n\n**Contact details**\n- Website: https://kfund.vc/\n"
    + "- Registered address: Madrid ([s1]).\n\n1. Message your contact\n2. Reference the pre-seed\n\n> Subject: Cala AI seed\n>\n> Hi [name],";
  await page.route("**/api/runs/basic-run/chat/stream", route => route.fulfill({ contentType: "text/event-stream", body: sse({
    message: markdown, citations: [source], citation_ids: ["s1"], candidate_ids: [], model_used: true, tool_steps: [] }) }));
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await page.getByLabel("Ask about your investors", { exact: true }).fill("How do I contact Kfund?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  const answer = page.locator(".rc-chat-message-assistant").last().locator(".rc-chat-markdown");
  await expect(answer.locator("strong").first()).toHaveText("Short answer: use your existing relationship.");
  await expect(answer.locator("ul > li")).toHaveCount(2);
  await expect(answer.locator("ol > li")).toHaveCount(2);
  await expect(answer.locator("blockquote")).toContainText("Hi [name],");
  await expect(answer.locator("a[href='https://kfund.vc/']")).toHaveAttribute("target", "_blank");
  await expect(answer.locator(".rc-cite a")).toHaveText("1");
  await expect(answer.locator(".rc-cite a")).toHaveAttribute("href", source.url);
  await expect(answer).not.toContainText("**");
  await expect(answer).not.toContainText("[s1]");
});

test("single newlines in an answer render as line breaks", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/runs/basic-run/chat/stream", route => route.fulfill({ contentType: "text/event-stream", body: sse({
    message: "**Public contact channel**\nWebsite: https://kfund.vc/\nSubject: Cala AI seed", citations: [], citation_ids: [],
    candidate_ids: [], model_used: true, tool_steps: [] }) }));
  await page.goto("/");
  await confirmCompanies(page);
  await completeRound(page);
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await page.getByLabel("Ask about your investors", { exact: true }).fill("How do I contact Kfund?");
  await page.getByRole("button", { name: "Send question", exact: true }).click();
  await expect(page.locator(".rc-chat-message-assistant").last().locator(".rc-chat-markdown br")).toHaveCount(2);
});
