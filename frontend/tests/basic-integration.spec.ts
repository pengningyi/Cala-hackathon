import { expect, test, type Page } from "@playwright/test";
import type { CandidateCard, Entity, FounderInput, FundingRound, ResolveRequest, RunResult, Source, StartRunRequest } from "../lib/types";

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

async function mockApi(page: Page) {
  let resolutionRequest: ResolveRequest | null = null;
  let runRequest: StartRunRequest | null = null;
  let resultReads = 0;
  await page.route("**/api/health", route => route.fulfill({ json: { status: "healthy", cala_configured: true, model_provider: "disabled" } }));
  await page.route("**/api/resolve", route => {
    resolutionRequest = route.request().postDataJSON() as ResolveRequest;
    return route.fulfill({ json: { id: "basic-resolution", profile: { ...resolutionRequest.profile, lead_name: lead.name },
      lead_matches: [lead], startup_matches: [company], resolver_ms: 100, created_at: source.retrieved_at,
      expires_at: "2026-11-14T01:00:00Z", no_open_allocation: false, notices: [], detected_lead: {
        lead, company_id: company.id, event_id: shared.id, stage: "seed", event_date: shared.event_date,
        event_date_precision: "day", sources: [source] } } });
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
  return { resolution: () => resolutionRequest!, run: () => runRequest! };
}

test("main step-one fields accept shorthand and preserve detected lead only after explicit confirmation", async ({ page }) => {
  const captured = await mockApi(page);
  await page.goto("/");
  await expect(page.getByLabel("Company name", { exact: true })).toHaveValue("Cala AI");
  await expect(page.getByLabel(/^Company website/)).toHaveValue("cala.ai");
  await expect(page.getByLabel(/^Lead investor name/)).toHaveValue("");
  await expect(page.getByLabel(/^Headquarters country/)).not.toHaveAttribute("required");
  await expect(page.getByLabel("What does your product do?", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Round stage").locator("option")).toHaveText(["Pre-seed", "Seed", "Series A", "Series B"]);
  await page.getByLabel(/^Round target/).fill("4M");
  await page.getByLabel(/^Already committed/).fill("2,000K");
  await page.getByLabel(/^Investors already in/).fill("Existing One, Existing Two");
  await page.getByLabel(/^Known competitors/).fill("Competitor One");
  await page.getByRole("button", { name: "Match entities in Cala", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Choose your lead", exact: true })).toBeVisible();
  expect(captured.resolution().profile.round.target_eur).toBe("4000000.00");
  expect(captured.resolution().profile.round.firm_commitments_eur).toBe("2000000.00");
  expect(captured.resolution().profile.round.lead_status).toBe("unconfirmed");
  expect(captured.resolution().profile.startup.website).toBe("https://cala.ai/");
  expect(captured.resolution().profile.excluded_investors).toEqual(["Existing One", "Existing Two"]);
  await expect(page.locator(".detected-lead-note")).toContainText("historical participation does not establish a current-round commitment");
  await expect(page.locator(".confirmation-section").first().locator(".entity-choice")).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("button", { name: "Find my co-investors", exact: true })).toBeDisabled();
  await page.locator(".confirmation-section").first().locator(".entity-choice").click();
  await page.locator(".startup-matches .entity-choice").click();
  await page.getByLabel("I confirm this is the correct lead and startup profile.").check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await expect(page.locator(".shortlist-heading")).toBeVisible();
  expect(captured.run().profile.lead_name).toBe(lead.name);
  expect(captured.run().profile.startup.cala_entity_id).toBe(company.id);
});

test("combined workspace retains graph and copy requests while showing main allocation and review flags", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Match entities in Cala", exact: true }).click();
  await page.locator(".confirmation-section").first().locator(".entity-choice").click();
  await page.getByLabel("I confirm this is the correct lead and startup profile.").check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Estimated allocation", exact: true })).toBeVisible();
  await expect(page.locator(".workspace-allocation-row")).toHaveCount(3);
  await expect(page.getByRole("progressbar", { name: "First Fund: estimated share of open allocation" })).toHaveAttribute("aria-valuenow", "50");
  await expect(page.locator(".workspace-allocation-note")).toContainText("Actual individual checks and willingness to invest are unconfirmed");
  await expect(page.locator(".workspace-allocation")).not.toContainText("Review Fund");
  await expect(page.locator(".result-meta")).toContainText("2 ready");
  await expect(page.locator(".workspace-investor-row").last()).toContainText("Needs review");
  await expect(page.getByRole("region", { name: "Founder and investor knowledge graph" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Current understanding", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".agent-insight-understanding")).toContainText("2 shortlisted, 1 requiring review");
  await expect(page.locator(".rc-chat-composer")).toHaveCount(0);
  await page.getByRole("tab", { name: "Agent activity", exact: true }).click();
  await expect(page.locator(".agent-insight-activity .trace-event").first()).toContainText("cache hit");
  await page.locator(".agent-insight-activity .trace-event").first().locator("summary").click();
  await expect(page.locator(".agent-insight-activity pre")).toContainText("synthetic-lead");
  await page.getByRole("tab", { name: "Current understanding", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Agent activity", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(page.getByRole("tab", { name: "Current understanding", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "Select Second Fund", exact: true }).click();
  await expect(page.locator(".agent-insight-selection")).toContainText("Second Fund");
  await expect(page.locator(".agent-insight-selected-explanation h4")).toHaveText("Second Fund");
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

test("negative main-form amounts are rejected before calling Cala", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/resolve", route => { calls += 1; return route.abort(); });
  await page.goto("/");
  await page.getByLabel(/^Round target/).fill("-4M");
  await page.getByRole("button", { name: "Match entities in Cala", exact: true }).click();
  await expect(page.locator(".error-banner[role='alert']")).toContainText("Round target must be a non-negative EUR amount");
  expect(calls).toBe(0);
});
