import { expect, test } from "@playwright/test";
import { buildResultGraph, graphFocus } from "../lib/result-graph";
import type { CandidateCard, Entity, FundingRound, RunResult, Source } from "../lib/types";

/** Deliberately synthetic records; these tests never call Cala or launch a browser. */
function source(label = "Synthetic test evidence"): Source {
  return {
    url: "https://synthetic.example.test/funding-event",
    retrieved_at: "2026-11-14T00:00:00Z",
    published_at: "2026-10-01",
    label,
  };
}

function entity(id: string, name = `Synthetic ${id}`): Entity {
  return {
    id, name, kind: "unknown", country: null, website: null, description: null,
    aliases: [], related_entity_ids: [], sources: [source()],
  };
}

function funding(id: string, participants: string[], overrides: Partial<FundingRound> = {}): FundingRound {
  return {
    id, company: entity("portfolio-company"), event_date: "2026-10-01", event_date_precision: "day",
    stage: "seed", sector: null, company_country: null, total_amount_eur: null,
    participants: participants.map(investorId => ({ investor: entity(investorId), is_lead: null })),
    sources: [source()], ...overrides,
  };
}

function card(id: string, sharedDeals: FundingRound[]): CandidateCard {
  const investor = entity(id);
  return {
    investor, fit_score: 50, profile_completeness: .35,
    explanation: "Synthetic prioritization record, not a real recommendation.",
    shared_round_count: sharedDeals.length,
    shared_company_count: new Set(sharedDeals.map(deal => deal.company.id)).size,
    shared_deals: sharedDeals, recent_rounds_12m: null, most_recent_investment: null,
    components: [], considerations: [], statuses: [],
    profile: { investor, rounds: [], observations: [], activity_window_complete: false,
      ticket_criteria: [], mandates: [], assessments: [], sources: [source()] },
    sources: [source()], action: "introduction", draft: "Synthetic test draft.",
  };
}

function result(cards: CandidateCard[], overrides: Partial<RunResult> = {}): RunResult {
  return {
    run_id: "synthetic-run", state: "partial", lead: entity("lead"),
    profile: {
      founder_name: "Synthetic Founder",
      startup: { cala_entity_id: null, company_name: "Synthetic Startup", website: null,
        product_description: "Synthetic software for graph regression tests", sector: "Synthetic software",
        target_customer: "Synthetic teams", problem_solved: "Synthetic test problem", country: "Finland", known_competitors: [] },
      round: { stage: "seed", target_eur: "2000000", firm_commitments_eur: "1000000",
        conditional_interest_eur: "0", lead_status: "conditional", lead_ticket_eur: null },
      lead_name: "Synthetic lead", investor_statuses: [],
      preferences: { include_angels: true, include_corporate_vcs: true, investor_countries: [] },
    },
    confirmed_at: "2026-11-14T00:00:00Z", finished_at: "2026-11-14T00:00:01Z",
    remaining_allocation_eur: "1000000", lookback_months: 60,
    candidates: cards, exclusions: [], incomplete: [],
    counts: { discovered: cards.length, profiled: cards.length, shortlisted: cards.length, excluded: 0, incomplete: 0 },
    notices: ["Synthetic partial coverage"], combined_draft: "Synthetic combined draft.",
    processing_ms: 1000, cala_calls: 0, cache_hits: 0,
    retrieval_timestamps: ["2026-11-14T00:00:00Z"], scoring_version: "synthetic-test", error: null,
    ...overrides,
  };
}

test("same-company investments in different rounds do not create a shared-round path", () => {
  const shared = funding("shared", ["lead", "eligible"]);
  const differentRound = funding("different-round", ["unconnected"]);
  const graph = buildResultGraph(result([card("eligible", [shared]), card("unconnected", [differentRound])]));

  expect(graph.eventCount).toBe(1);
  expect(graph.investorCount).toBe(1);
  expect(graph.nodes.map(node => node.id)).not.toContain("event:different-round");
  expect(graph.nodes.map(node => node.id)).not.toContain("investor:unconnected");
  expect(graph.edges.some(edge => edge.source === "investor:unconnected")).toBe(false);
});

test("unverified participant identities and unsourced events cannot create solid edges", () => {
  const absentCandidate = funding("absent-candidate", ["lead", "someone-else"]);
  const absentLead = funding("absent-lead", ["candidate"]);
  const unsourced = funding("unsourced", ["lead", "candidate"], { sources: [] });
  const graph = buildResultGraph(result([card("candidate", [absentCandidate, absentLead, unsourced])]));

  expect(graph.eventCount).toBe(0);
  expect(graph.investorCount).toBe(0);
  expect(graph.edges.filter(edge => edge.kind !== "founder_route")).toEqual([]);
  expect(graph.edges.every(edge => edge.kind === "founder_route" && edge.sources.length === 0)).toBe(true);
});

test("repeated event references deduplicate rounds, issuer companies and participation edges", () => {
  const first = funding("shared", ["lead", "a", "not-shortlisted"]);
  const repeated = funding("shared", ["lead", "b"]);
  const another = funding("follow-on", ["lead", "b"], { event_date: "2026-11-01" });
  const graph = buildResultGraph(result([card("a", [first, first]), card("b", [repeated, another])]));

  expect(graph.eventCount).toBe(2);
  expect(graph.nodes.filter(node => node.kind === "company")).toHaveLength(1);
  expect(graph.edges.filter(edge => edge.source === "investor:lead" && edge.target === "event:shared")).toHaveLength(1);
  expect(graph.edges.filter(edge => edge.source === "investor:a" && edge.target === "event:shared")).toHaveLength(1);
  expect(graph.edges.filter(edge => edge.source === "investor:b" && edge.target === "event:shared")).toHaveLength(1);
  expect(graph.nodes.map(node => node.id)).not.toContain("investor:not-shortlisted");
  expect(graph.edges.filter(edge => edge.kind !== "founder_route").every(edge => edge.sources.length > 0)).toBe(true);
  expect(new Set(graph.nodes.map(node => node.id)).size).toBe(graph.nodes.length);
  expect(new Set(graph.edges.map(edge => edge.id)).size).toBe(graph.edges.length);
});

for (const conflict of ["issuer", "date"] as const) {
  test(`conflicting ${conflict} for the same funding-event ID discards that event`, () => {
    const original = funding("conflicting", ["lead", "a"]);
    const contradiction = funding("conflicting", ["lead", "b"], conflict === "issuer"
      ? { company: entity("different-issuer") } : { event_date: "2026-10-02" });
    const valid = funding("valid", ["lead", "b"]);
    const graph = buildResultGraph(result([card("a", [original]), card("b", [contradiction, valid])]));

    expect(graph.eventCount).toBe(1);
    expect(graph.nodes.map(node => node.id)).not.toContain("event:conflicting");
    expect(graph.nodes.map(node => node.id)).toContain("event:valid");
    expect(graph.edges.some(edge => edge.eventId === "conflicting")).toBe(false);
  });
}

test("a selected investor highlights only their lead-event-issuer path", () => {
  const shared = funding("shared", ["lead", "a", "b"]);
  const unrelated = funding("other", ["lead", "b"], { company: entity("other-company") });
  const graph = buildResultGraph(result([card("a", [shared]), card("b", [shared, unrelated])]));
  const focus = graphFocus(graph, "a");

  expect(focus).not.toBeNull();
  expect([...focus!.nodes]).toEqual(expect.arrayContaining([
    "founder:synthetic-run", "investor:lead", "investor:a", "event:shared", "company:portfolio-company",
  ]));
  expect(focus!.nodes.has("investor:b")).toBe(false);
  expect(focus!.nodes.has("event:other")).toBe(false);
  expect(focus!.nodes.has("company:other-company")).toBe(false);
  expect([...focus!.edges]).not.toContain("investor:b:event:shared");
  expect(graphFocus(graph, null)).toBeNull();
  expect(graphFocus(graph, "missing-investor")).toBeNull();
});

test("a founder-confirmed Cala startup identity reuses the historical issuer node", () => {
  const base = result([card("a", [funding("shared", ["lead", "a"])])]);
  const graph = buildResultGraph({ ...base, profile: { ...base.profile,
    startup: { ...base.profile.startup, cala_entity_id: "portfolio-company" } } });
  const issuer = graph.nodes.filter(node => node.calaId === "portfolio-company");

  expect(issuer).toHaveLength(1);
  expect(issuer[0].kind).toBe("founder");
  expect(issuer[0].label).toBe("Synthetic Startup");
  expect(issuer[0].sources.length).toBeGreaterThan(0);
  expect(graph.edges.some(edge => edge.kind === "issuer" && edge.target === issuer[0].id)).toBe(true);
  expect(graph.edges.some(edge => edge.kind === "founder_route" && edge.source === issuer[0].id)).toBe(true);
});

test("a matching company name alone never merges the founder and historical issuer", () => {
  const round = funding("shared", ["lead", "a"], { company: entity("portfolio-company", "Synthetic Startup") });
  const graph = buildResultGraph(result([card("a", [round])]));

  expect(graph.nodes.filter(node => node.label === "Synthetic Startup")).toHaveLength(2);
  expect(graph.nodes.some(node => node.id === "founder:synthetic-run" && node.calaId === null)).toBe(true);
  expect(graph.nodes.some(node => node.id === "company:portfolio-company" && node.calaId === "portfolio-company")).toBe(true);
});

test("empty and no-allocation results never manufacture investor evidence", () => {
  const empty = buildResultGraph(result([], { state: "no_match" }));
  expect(empty.eventCount).toBe(0);
  expect(empty.investorCount).toBe(0);
  expect(empty.edges.filter(edge => edge.kind !== "founder_route")).toEqual([]);

  const noLead = buildResultGraph(result([], { lead: null }));
  expect(noLead.nodes).toEqual([]);
  expect(noLead.edges).toEqual([]);

  const noAllocation = buildResultGraph(result([card("a", [funding("shared", ["lead", "a"])])], {
    state: "no_open_allocation", remaining_allocation_eur: "0",
  }));
  expect(noAllocation.nodes).toEqual([]);
  expect(noAllocation.edges).toEqual([]);
});

test("graph display remains bounded to five shortlisted investors and three shared deals each", () => {
  const cards = Array.from({ length: 6 }, (_, index) => card(`candidate-${index}`,
    Array.from({ length: 4 }, (_, eventIndex) => funding(`event-${index}-${eventIndex}`, ["lead", `candidate-${index}`]))));
  const graph = buildResultGraph(result(cards));

  expect(graph.investorCount).toBe(5);
  expect(graph.eventCount).toBe(15);
  expect(graph.nodes.some(node => node.id === "investor:candidate-5")).toBe(false);
  expect(graph.nodes.some(node => node.id === "event:event-0-3")).toBe(false);
});
