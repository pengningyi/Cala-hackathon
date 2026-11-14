import { expect, test, type Page } from "@playwright/test";
import type { CandidateCard, Entity, FounderInput, FundingRound, RunResult, ScoreComponent, Source, StartRunRequest } from "../lib/types";

const source: Source = { url: "https://synthetic.example.test/v2", label: "Synthetic v2 evidence",
  published_at: "2026-10-01", retrieved_at: "2026-11-14T00:00:00Z" };
const entity = (id: string, name: string, entity_type?: string): Entity => ({ id, name, kind: "vc", country: "Finland",
  website: null, description: "Synthetic fixture.", aliases: [], related_entity_ids: [], sources: [source], entity_type });
const lead = entity("lead", "Synthetic Lead");
const company = entity("startup", "Cala AI");
const shared: FundingRound = { id: "event", company: entity("portfolio", "Portfolio Co"), event_date: "2026-03-01",
  event_date_precision: "day", stage: "seed", sector: null, company_country: "Finland", total_amount_eur: null,
  participants: [lead, entity("kibo", "Kibo Ventures"), entity("other", "Other Fund")].map(investor => ({ investor, is_lead: null })),
  sources: [source] };

const GROUP: Record<string, string> = { lead_coinvestor: "Connection", repeat_partner: "Connection", recent_partner: "Connection",
  your_investor: "Connection", backed_your_founder: "Connection", warm_path: "Connection", lead_closed_path: "Connection",
  recently_active: "Activity", stage_match: "Fit", sector_match: "Fit", country_match: "Fit", no_competitor: "Risk" };
/** A rule result: true = met, false = not met, null = not checked. */
const rule = (name: string, met: boolean | null, detail = "Synthetic rule detail"): ScoreComponent => ({
  name, weight: 1, value: met ? 1 : 0, known: met !== null, numerator: null, denominator: null, group: GROUP[name],
  explanation: met === null ? `Not checked: ${detail}` : detail, sources: met === null ? [] : [source] });
function rules(met: string[], notMet: string[] = []): ScoreComponent[] {
  return Object.keys(GROUP).map(name => rule(name, met.includes(name) ? true : notMet.includes(name) ? false : null));
}

function card(id: string, name: string, met: string[], withLoop: boolean, notMet: string[] = []): CandidateCard {
  const investor = entity(id, name);
  const components = rules(met, notMet);
  const fit = met.length;
  const sonia = entity("sonia", "Sonia Fernandez", "Person");
  return { investor, fit_score: fit, profile_completeness: .62, explanation: "Synthetic v2 card.", shared_round_count: 1,
    shared_company_count: 1, shared_deals: [shared], recent_rounds_12m: 1, most_recent_investment: shared.event_date, components,
    considerations: [], statuses: [], profile: { investor, rounds: [shared], observations: [], activity_window_complete: false,
      ticket_criteria: [], mandates: [], assessments: [], sources: [source] }, sources: [source], action: "introduction",
    draft: `Hi Synthetic Lead, could you introduce us to ${name}? This lead commitment is provisional.`, review_status: "short",
    flags: [], estimated_ticket_eur: null, ticket_estimate_basis: null,
    trust: withLoop ? .42 : null,
    recommended_action: withLoop ? "reach_out_via_contact" : met.includes("lead_coinvestor") ? "ask_lead_intro" : "skip",
    action_contact: withLoop ? sonia : null,
    loops: withLoop ? [{ id: "loop-1", kind: "known", contact: sonia, strength: .42, confirmed: false,
      label: "Public professional overlap; personal relationship unconfirmed", hops: [
        { from_id: "elisenda", from_name: "Elisenda", to_id: "sonia", to_name: "Sonia Fernandez", edge_type: "board",
          org: entity("vilynx", "Vilynx"), since: "2014-01-01", until: "2017-01-01", strength: .42, sources: [source] },
        { from_id: "sonia", from_name: "Sonia Fernandez", to_id: id, to_name: name, edge_type: "person_at_fund",
          org: investor, since: "2017-06-01", until: null, strength: .9, sources: [source] }] }] : [] };
}

/** An existing investor of your company with no shared round with the lead. */
function capTable(): CandidateCard {
  const base = card("capfund", "Cap Table Fund", ["your_investor", "warm_path", "recently_active"], false, ["lead_coinvestor"]);
  const own: FundingRound = { ...shared, id: "own-round", company, stage: "pre_seed", event_date: "2024-03-01",
    participants: [{ investor: base.investor, is_lead: false }] };
  return { ...base, shared_deals: [], shared_round_count: 0, shared_company_count: 0, origins: ["existing_investor"],
    origin_rounds: [own], action: "follow_up", recommended_action: "reach_out_directly", trust: .71, action_contact: null,
    explanation: "Existing investor in Cala AI", draft: "Hi Cap Table Fund, thank you again for backing Cala AI. This lead commitment is provisional.",
    loops: [{ id: "own-loop", kind: "existing_investor", contact: null, strength: .9, confirmed: false, closes_through_lead: false,
      label: "Identified funding event; investor relationship confirmed by Cala", hops: [{ from_id: "startup", from_name: "Cala AI",
        to_id: "capfund", to_name: "Cap Table Fund", edge_type: "invested_in", org: company, since: "2024-03-01", until: "2024-03-01",
        strength: .9, sources: [source] }] }] };
}

function result(profile: FounderInput): RunResult {
  return { run_id: "v2-run", state: "partial", profile, lead, confirmed_at: source.retrieved_at, finished_at: source.retrieved_at,
    remaining_allocation_eur: "2000000", lookback_months: 60,
    candidates: [
      card("kibo", "Kibo Ventures", ["lead_coinvestor", "recent_partner", "warm_path", "recently_active", "stage_match", "country_match"], true, ["repeat_partner"]),
      card("other", "Other Fund", ["lead_coinvestor", "recent_partner", "recently_active"], false, ["repeat_partner", "stage_match"]),
      card("third", "Third Fund", ["lead_coinvestor"], false, ["recently_active"]), capTable()],
    exclusions: [], incomplete: [], counts: { discovered: 9, profiled: 8, shortlisted: 3, flagged: 0, returned: 3 },
    notices: [], combined_draft: "Combined request.", processing_ms: 8000, cala_calls: 30, cache_hits: 1,
    retrieval_timestamps: [source.retrieved_at], scoring_version: "rules-2026-11", error: null, allocation: [] };
}

async function mockApi(page: Page) {
  let runRequest: StartRunRequest | null = null;
  await page.route("**/api/health", route => route.fulfill({ json: { status: "healthy", cala_configured: true, model_provider: "disabled" } }));
  await page.route("**/api/onboarding/matches", route => route.fulfill({ json: { id: "m", lead_matches: [lead], startup_matches: [company],
    resolver_ms: 100, created_at: source.retrieved_at, expires_at: new Date(Date.now() + 3600000).toISOString(), notices: [] } }));
  await page.route("**/api/onboarding/matches/m/confirm", route => route.fulfill({ json: { resolution_id: "c", startup: company, lead,
    prefill: { company_name: company.name, website: "", sector: "AI infrastructure", country: "Spain" }, prefill_sources: {},
    expires_at: new Date(Date.now() + 3600000).toISOString(), notices: [] } }));
  await page.route("**/api/runs", route => {
    runRequest = route.request().postDataJSON() as StartRunRequest;
    return route.fulfill({ status: 202, json: { id: "v2-run", state: "running", confirmed_at: source.retrieved_at,
      event_count: 1, result_url: "/api/runs/v2-run/result", events_url: "/api/runs/v2-run/events" } });
  });
  await page.route("**/api/runs/v2-run/result", route => route.fulfill({ json: result(runRequest!.profile) }));
  await page.route("**/api/runs/v2-run/events", route => route.fulfill({ contentType: "text/event-stream", body:
    `id: 1\nevent: complete\ndata: ${JSON.stringify({ id: 1, type: "complete", at: source.retrieved_at, step: "complete", message: "done" })}\n\n` }));
  return () => runRequest;
}

test("rule scoring is explained: rules met, checklist, trust loops and the action ladder", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const run = await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Find matching companies", exact: true }).click();
  await page.locator(".entity-choice").filter({ hasText: company.name }).click();
  await page.locator(".entity-choice").filter({ hasText: lead.name }).click();
  await page.getByRole("button", { name: "Confirm companies", exact: true }).click();
  await page.getByLabel("Lead partner name").fill("Jane Partner");
  await page.getByLabel("Round stage", { exact: true }).selectOption("seed");
  await page.getByLabel(/^Round target/).fill("4M");
  await page.getByLabel(/^Already committed/).fill("2M");
  await page.getByLabel("I have reviewed my round details.", { exact: true }).check();
  await page.getByRole("button", { name: "Find my co-investors", exact: true }).click();

  const shortlist = page.locator(".workspace-shortlist");
  await expect(shortlist.getByRole("heading", { name: "Your top investors" })).toBeVisible();
  expect(run()!.profile.lead_partner_name).toBe("Jane Partner");
  const kibo = shortlist.locator(".workspace-investor-row").filter({ hasText: "Kibo Ventures" });
  await expect(kibo.locator(".workspace-rules-score")).toHaveText("6rules met");
  await expect(kibo).toContainText("Reach out via contact · Sonia Fernandez · trust 0.42");
  // The score is a count of rules met, never "x of y" or a 0-100 number.
  await expect(shortlist).not.toContainText("/100");
  await expect(shortlist.locator(".workspace-rules-score").filter({ hasText: " of " })).toHaveCount(0);
  await shortlist.screenshot({ path: ".qa/score-explainer-shortlist.png" });

  const guide = shortlist.locator("details.ranking-guide");
  await guide.locator("summary").click();
  await expect(guide).toContainText("rules-2026-11");
  await expect(guide.locator("tr").filter({ hasText: "Lead co-investor" })).toContainText("3");
  await expect(guide).toContainText("most rules met, then most Connection rules met");
  await page.screenshot({ path: ".qa/score-explainer-workspace.png", fullPage: true });

  await page.getByRole("button", { name: "View details for Kibo Ventures", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".fit-score")).toContainText("6rules met");
  await expect(dialog.locator(".rule-total")).toContainText("6 rules met");
  await expect(dialog.locator(".rule-group")).toHaveCount(4);
  await expect(dialog.locator(".rule-row").filter({ hasText: "Lead co-investor" })).toContainText("Met");
  await expect(dialog.locator(".rule-row").filter({ hasText: "Repeat partner" })).toContainText("Not met");
  await expect(dialog.locator(".rule-row").filter({ hasText: "Sector match" })).toContainText("Not checked");
  await expect(dialog.locator(".trust-loops")).toContainText("Board overlap at Vilynx");
  await expect(dialog.locator(".action-ladder li.is-current")).toContainText("Reach out via contact");
  await expect(dialog).not.toContainText("/100");
  await dialog.screenshot({ path: ".qa/score-explainer-details.png" });
  await page.getByRole("button", { name: "Close investor details", exact: true }).click();

  // A founder-network investor with no shared round with the lead.
  const cap = shortlist.locator(".workspace-investor-row").filter({ hasText: "Cap Table Fund" });
  await expect(cap).toContainText("Your investor");
  await page.getByRole("button", { name: "Select Cap Table Fund", exact: true }).click();
  await expect(cap).toContainText("Reach out directly");
  await expect(page.locator(".kg-edge-trust")).not.toHaveCount(0);
  await page.getByRole("button", { name: "View details for Cap Table Fund", exact: true }).click();
  const capDialog = page.getByRole("dialog");
  await expect(capDialog).toContainText("Your founder network");
  await expect(capDialog).toContainText("No shared round with your lead was found");
  await expect(capDialog.locator(".trust-loops")).toContainText("Cap Table Fund invested in Cala AI");
  await expect(capDialog.locator(".action-ladder li.is-current")).toContainText("Reach out directly");
  await capDialog.screenshot({ path: ".qa/score-explainer-captable.png" });
  expect(errors).toEqual([]);
});
