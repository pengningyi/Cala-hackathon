import { expect, test } from "@playwright/test";
import { buildResultGraph, graphFocus, resultNodeBounds, RESULT_GRAPH_LIMITS } from "../lib/result-graph";
import type { CandidateCard, Entity, FundingRound, Role, RunResult, Source } from "../lib/types";

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
  expect(graph.nodes.map(node => node.id)).not.toContain("entity:unconnected");
  expect(graph.edges.some(edge => edge.source === "entity:unconnected")).toBe(false);
  expect(graph.edges.filter(edge => edge.kind === "investment").every(edge => edge.target === "entity:portfolio-company")).toBe(true);
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
  expect(graph.edges.filter(edge => edge.source === "entity:lead" && edge.eventId === "shared")).toHaveLength(1);
  expect(graph.edges.filter(edge => edge.source === "entity:a" && edge.eventId === "shared")).toHaveLength(1);
  expect(graph.edges.filter(edge => edge.source === "entity:b" && edge.eventId === "shared")).toHaveLength(1);
  expect(graph.nodes.map(node => node.id)).not.toContain("entity:not-shortlisted");
  expect(graph.nodes.some(node => node.id.startsWith("event:"))).toBe(false);
  expect(graph.edges.find(edge => edge.eventId === "shared")?.funding?.event_date).toBe("2026-10-01");
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
    expect(graph.edges.some(edge => edge.eventId === "valid")).toBe(true);
    expect(graph.edges.some(edge => edge.eventId === "conflicting")).toBe(false);
  });
}

test("any selected entity highlights its immediate neighbors without invented path expansion", () => {
  const shared = funding("shared", ["lead", "a", "b"]);
  const unrelated = funding("other", ["lead", "b"], { company: entity("other-company") });
  const graph = buildResultGraph(result([card("a", [shared]), card("b", [shared, unrelated])]));
  const focus = graphFocus(graph, "a");

  expect(focus).not.toBeNull();
  expect([...focus!.nodes]).toEqual(expect.arrayContaining([
    "entity:a", "entity:portfolio-company",
  ]));
  expect(focus!.nodes.has("entity:b")).toBe(false);
  expect(focus!.nodes.has("entity:lead")).toBe(false);
  expect(focus!.nodes.has("entity:other-company")).toBe(false);
  expect([...focus!.edges].every(id => graph.edges.find(edge => edge.id === id)?.source === "entity:a")).toBe(true);
  const companyFocus = graphFocus(graph, "entity:portfolio-company");
  expect([...companyFocus!.nodes]).toEqual(expect.arrayContaining(["entity:lead", "entity:a", "entity:b"]));
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
  expect(graph.edges.some(edge => edge.kind === "investment" && edge.target === issuer[0].id)).toBe(true);
  expect(graph.edges.some(edge => edge.kind === "founder_route" && edge.source === issuer[0].id)).toBe(true);
});

test("a matching company name alone never merges the founder and historical issuer", () => {
  const round = funding("shared", ["lead", "a"], { company: entity("portfolio-company", "Synthetic Startup") });
  const graph = buildResultGraph(result([card("a", [round])]));

  expect(graph.nodes.filter(node => node.label === "Synthetic Startup")).toHaveLength(2);
  expect(graph.nodes.some(node => node.id === "founder:synthetic-run" && node.calaId === null)).toBe(true);
  expect(graph.nodes.some(node => node.id === "entity:portfolio-company" && node.calaId === "portfolio-company")).toBe(true);
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
  expect(graph.nodes.some(node => node.id === "entity:candidate-5")).toBe(false);
  expect(graph.edges.some(edge => edge.eventId === "event-0-3")).toBe(false);
});

function role(personId: string, orgId: string, kind: Role["kind"] = "works_at", overrides: Partial<Role> = {}): Role {
  return { person: { ...entity(personId), entity_type: "Person" }, org: { ...entity(orgId), entity_type: "Company" },
    kind, valid_since: "2020-01-01", valid_until: null, current: false, sources: [source()], ...overrides };
}

test("existing investors have sourced recipient edges without a shared lead round or a trust loop", () => {
  const own = funding("own-round", ["cap-table"], { company: entity("startup"), event_date: null });
  const candidate = { ...card("cap-table", []), origins: ["existing_investor" as const], origin_rounds: [own] };
  const base = result([candidate]);
  base.profile.startup.cala_entity_id = "startup";
  const graph = buildResultGraph(base);
  expect(graph.investorCount).toBe(1);
  expect(graph.nodes.filter(node => node.calaId === "startup")).toHaveLength(1);
  const edge = graph.edges.find(edge => edge.kind === "investment")!;
  expect(edge.source).toBe("entity:cap-table");
  expect(edge.target).toBe("entity:startup");
  expect(edge.funding?.event_date).toBeNull();
  expect(graph.edges.filter(edge => edge.kind === "investment" && edge.source === "entity:lead")).toEqual([]);
});

test("founders and staff are sourced person nodes with affiliations, never personal investment shortcuts", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  candidate.profile.people = [role("staff", "fund"), role("staff", "fund"), role("staff", "former-employer")];
  const base = result([candidate], { people: [role("founder-person", "startup", "founded"), role("lead-person", "lead")] });
  base.profile.startup.cala_entity_id = "startup";
  const graph = buildResultGraph(base);
  expect(graph.nodes.filter(node => node.calaId === "staff")).toHaveLength(1);
  expect(graph.nodes.find(node => node.calaId === "founder-person")?.kind).toBe("person");
  expect(graph.nodes.find(node => node.calaId === "staff")?.degree).toBe(1);
  expect(graph.nodes.some(node => node.calaId === "former-employer")).toBe(false);
  const staffEdge = graph.edges.find(edge => edge.role?.person.id === "staff")!;
  expect(staffEdge.role?.current).toBe(false);
  expect(staffEdge.role?.valid_since).toBe("2020-01-01");
  expect(staffEdge.label).toBe("Employment affiliation");
  expect(graph.edges.filter(edge => edge.kind === "investment").some(edge => edge.source === "entity:staff" || edge.source === "entity:founder-person")).toBe(false);
  expect(graphFocus(graph, "staff")?.nodes.has("entity:fund")).toBe(true);
});

test("previous-company contacts remain companies and exact loop hops retain their endpoints", () => {
  const previous = { ...entity("previous"), entity_type: "Company" };
  const candidate = { ...card("fund", []), origins: ["previous_company_backer" as const],
    origin_rounds: [funding("previous-round", ["fund"], { company: previous })], loops: [{
      id: "loop", kind: "previous_company" as const, contact: previous, confirmed: false, strength: .4,
      label: "Public relationship", hops: [
        { from_id: "founder-person", from_name: "Founder", to_id: "previous", to_name: "Previous", edge_type: "founded" as const,
          org: previous, since: "2015-01-01", until: "2019-01-01", strength: .8, sources: [source()] },
        { from_id: "previous", from_name: "Previous", to_id: "fund", to_name: "Fund", edge_type: "invested_in" as const,
          org: previous, since: "2026-10-01", until: "2026-10-01", strength: .5, sources: [source()] },
      ],
    }] };
  const graph = buildResultGraph(result([candidate]));
  expect(graph.nodes.filter(node => node.calaId === "previous")).toHaveLength(1);
  expect(graph.nodes.find(node => node.calaId === "previous")?.kind).toBe("company");
  expect(graph.nodes.find(node => node.calaId === "founder-person")?.kind).toBe("person");
  expect(graph.edges.some(edge => edge.source === "entity:founder-person" && edge.target === "entity:previous")).toBe(true);
  expect(graph.edges.some(edge => edge.source === "entity:fund" && edge.target === "entity:previous" && edge.funding?.id === "previous-round")).toBe(true);
  expect(graph.edges.some(edge => edge.source === "entity:founder-person" && edge.target === "entity:fund")).toBe(false);
});

test("source-free, future and malformed affiliations never create people or relationships", () => {
  const graph = buildResultGraph(result([], { people: [role("unsourced", "lead", "works_at", { sources: [] }),
    role("future", "lead", "works_at", { valid_since: "2027-01-01" }),
    role("malformed", "lead", "works_at", { valid_since: "not-a-date" })] }));
  expect(graph.peopleCount).toBe(0);
  expect(graph.edges.filter(edge => edge.kind === "role")).toEqual([]);
});

test("funding-edge focus retains only that evidence's two actual endpoint entities", () => {
  const graph = buildResultGraph(result([card("fund", [funding("shared", ["lead", "fund"])])]));
  const edge = graph.edges.find(edge => edge.kind === "investment" && edge.source === "entity:fund")!;
  const focus = graphFocus(graph, edge.id)!;
  expect([...focus.nodes].sort()).toEqual(["entity:fund", "entity:portfolio-company"]);
  expect([...focus.edges]).toEqual([edge.id]);
  expect(edge.funding?.sources).toEqual([source()]);
});

test("degree sizes count distinct visible entities rather than duplicate funding or role records", () => {
  const shared = funding("shared", ["lead", "fund"]);
  const one = buildResultGraph(result([card("fund", [shared])]));
  const repeated = buildResultGraph(result([card("fund", [shared, shared, { ...shared, id: "another-round" }])]));
  const node = (graph: ReturnType<typeof buildResultGraph>, id: string) => graph.nodes.find(node => node.calaId === id)!;
  expect(node(one, "fund").degree).toBe(1);
  expect(node(repeated, "fund").degree).toBe(1);
  expect(node(one, "fund").radius).toBe(node(repeated, "fund").radius);
  expect(node(one, "lead").degree).toBe(2);
  expect(node(one, "lead").radius).toBeGreaterThan(node(one, "fund").radius);
});

test("dense graphs retain founder people and all candidates, report omissions, and lay out deterministically without shape collisions", () => {
  const cards = Array.from({ length: 5 }, (_, index) => card(`fund-${index}`, [funding(`round-${index}`, ["lead", `fund-${index}`],
    { company: entity(`company-${index}`) })]));
  cards.forEach((candidate, index) => { candidate.profile.people = Array.from({ length: 14 }, (_, person) => role(`staff-${index}-${person}`, candidate.investor.id)); });
  const base = result(cards, { people: [role("founder-person", "startup", "founded")] });
  base.profile.startup.cala_entity_id = "startup";
  const graph = buildResultGraph(base);
  expect(graph.nodes.length).toBeLessThanOrEqual(RESULT_GRAPH_LIMITS.nodes);
  expect(graph.peopleCount).toBeLessThanOrEqual(RESULT_GRAPH_LIMITS.people);
  expect(graph.investorCount).toBe(5);
  expect(graph.nodes.find(node => node.calaId === "founder-person")?.kind).toBe("person");
  expect(graph.truncated).toBe(true);
  expect(graph.omittedNodeCount).toBeGreaterThan(0);
  expect(buildResultGraph(base)).toEqual(graph);
  for (const node of graph.nodes) {
    expect(node.radius).toBeGreaterThanOrEqual(16);
    expect(node.radius).toBeLessThanOrEqual(34);
    expect(node.x - node.radius).toBeGreaterThan(0);
    expect(node.x + node.radius).toBeLessThan(graph.width);
    expect(node.y - node.radius).toBeGreaterThan(0);
    expect(node.y + node.radius + 28).toBeLessThan(graph.height);
    for (const other of graph.nodes) if (other.id !== node.id) {
      expect(Math.hypot(node.x - other.x, node.y - other.y)).toBeGreaterThan(node.radius + other.radius + 12);
      const first = resultNodeBounds(node), second = resultNodeBounds(other);
      expect(first.right <= second.left || first.left >= second.right || first.bottom <= second.top || first.top >= second.bottom).toBe(true);
    }
  }
});

test("future funding dates cannot create investor evidence", () => {
  const graph = buildResultGraph(result([card("fund", [funding("future", ["lead", "fund"], { event_date: "2027-01-01" })])]));
  expect(graph.eventCount).toBe(0);
  expect(graph.investorCount).toBe(0);
});

test("a candidate with sourced staff affiliations remains visible without manufacturing investment evidence", () => {
  const candidate = card("role-only-fund", []);
  candidate.profile.people = [role("partner", "role-only-fund")];
  const graph = buildResultGraph(result([candidate]));
  expect(graph.investorCount).toBe(1);
  expect(graph.nodes.find(node => node.calaId === "role-only-fund")?.card?.investor.id).toBe("role-only-fund");
  expect(graph.edges.some(edge => edge.kind === "role" && edge.source === "entity:partner" && edge.target === "entity:role-only-fund")).toBe(true);
  expect(graph.edges.filter(edge => edge.kind === "investment")).toEqual([]);
});

test("conflicting current-role records retain both evidence states without increasing degree", () => {
  const base = result([], { people: [role("partner", "lead"), role("partner", "lead", "works_at", { current: true })] });
  const graph = buildResultGraph(base);
  const affiliations = graph.edges.filter(edge => edge.kind === "role");
  expect(affiliations).toHaveLength(2);
  expect(new Set(affiliations.map(edge => edge.role?.current))).toEqual(new Set([false, true]));
  expect(graph.nodes.find(node => node.calaId === "partner")?.degree).toBe(1);
});

test("explicit organizational entity types stay organizational despite an inconsistent angel category", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  candidate.investor = { ...candidate.investor, entity_type: "Organization", kind: "angel" };
  const graph = buildResultGraph(result([candidate]));
  expect(graph.nodes.find(node => node.calaId === "fund")?.kind).toBe("investor");
});

test("relative degree sizing still distinguishes hubs above sixteen neighbors", () => {
  const candidates = [card("a", []), card("b", [])];
  candidates[0].profile.people = [role("x", "a"), role("y", "a")];
  candidates[1].profile.people = [role("x", "b")];
  const base = result(candidates, { people: [role("x", "startup", "founded"), role("y", "startup", "founded"),
    ...Array.from({ length: 17 }, (_, index) => [role("x", `shared-org-${index}`), role("y", `shared-org-${index}`)]).flat()] });
  base.profile.startup.cala_entity_id = "startup";
  const graph = buildResultGraph(base);
  const x = graph.nodes.find(node => node.calaId === "x")!, y = graph.nodes.find(node => node.calaId === "y")!;
  expect(x.degree).toBe(20);
  expect(y.degree).toBe(19);
  expect(x.radius).toBeGreaterThan(y.radius);
  expect(x.radius).toBe(34);
});

test("conflicting scalar facts on duplicate event references stay unknown after a third repetition", () => {
  const first = funding("shared", ["lead", "fund"], { stage: "seed", event_date_precision: "day", total_amount_eur: "1000000" });
  const conflicting = { ...first, stage: "series_a", event_date_precision: "month", total_amount_eur: "2000000" };
  const graph = buildResultGraph(result([card("fund", [first, conflicting, first])]));
  expect(graph.eventCount).toBe(1);
  for (const edge of graph.edges.filter(edge => edge.kind === "investment")) {
    expect(edge.funding?.stage).toBeNull();
    expect(edge.funding?.event_date_precision).toBeNull();
    expect(edge.funding?.total_amount_eur).toBeNull();
  }
});

test("a lead-loop traversal from a fund to its partner keeps the fund circular and affiliation directed person to organization", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  candidate.loops = [{ id: "lead-loop", kind: "lead_loop", contact: { ...entity("lead-partner"), entity_type: "Person" },
    strength: .5, confirmed: false, label: "Public professional evidence", hops: [
      { from_id: "lead", from_name: "Lead", to_id: "lead-partner", to_name: "Lead partner", edge_type: "person_at_fund",
        org: null, since: null, until: null, strength: .9, sources: [source()] },
      { from_id: "lead-partner", from_name: "Lead partner", to_id: "fund-partner", to_name: "Fund partner", edge_type: "worked_together",
        org: null, since: null, until: null, strength: .5, sources: [source()] },
      { from_id: "fund-partner", from_name: "Fund partner", to_id: "fund", to_name: "Fund", edge_type: "person_at_fund",
        org: null, since: null, until: null, strength: .9, sources: [source()] },
    ] }];
  const graph = buildResultGraph(result([candidate]));
  expect(graph.nodes.find(node => node.calaId === "lead")?.kind).toBe("lead");
  expect(graph.nodes.find(node => node.calaId === "lead-partner")?.kind).toBe("person");
  expect(graph.nodes.find(node => node.calaId === "fund-partner")?.kind).toBe("person");
  const edge = graph.edges.find(edge => edge.hop?.edge_type === "person_at_fund" && edge.target === "entity:lead")!;
  expect(edge.source).toBe("entity:lead-partner");
  expect(edge.hop?.from_id).toBe("lead-partner");
  expect(graph.nodes.filter(node => node.calaId === "lead")).toHaveLength(1);
});

test("schools and employers enter the initial view only when they bridge selected people", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  candidate.profile.people = [role("one", "fund"), role("two", "fund"), role("one", "tangential-school", "studied_at"),
    role("one", "shared-school", "studied_at"), role("two", "shared-school", "studied_at")];
  const graph = buildResultGraph(result([candidate]));
  expect(graph.nodes.some(node => node.calaId === "tangential-school")).toBe(false);
  expect(graph.nodes.some(node => node.calaId === "shared-school")).toBe(true);
  expect(graph.nodes.filter(node => node.kind === "person")).toHaveLength(2);
  expect(graph.nodes.filter(node => node.degree === 0 && !node.card && !["founder", "lead"].includes(node.kind))).toEqual([]);
});

test("expanding a fund reveals a bounded staff batch while preserving its overview", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  candidate.profile.people = Array.from({ length: 12 }, (_, index) => role(`staff-${String(index).padStart(2, "0")}`, "fund"));
  const base = result([candidate]);
  const before = JSON.stringify(base), overview = buildResultGraph(base);
  const expanded = buildResultGraph(base, { expandedNodeIds: ["fund", "entity:fund"] });
  expect(expanded.expandedNodeIds).toEqual(["entity:fund"]);
  expect(overview.nodes.find(node => node.calaId === "fund")?.availableNeighborCount).toBe(10);
  const oldIds = new Set(overview.nodes.map(node => node.id));
  expect(expanded.nodes.filter(node => !oldIds.has(node.id))).toHaveLength(RESULT_GRAPH_LIMITS.neighborsPerHub);
  expect(overview.nodes.every(node => expanded.nodes.some(other => other.id === node.id))).toBe(true);
  expect(expanded.nodes.find(node => node.calaId === "fund")?.availableNeighborCount).toBe(2);
  expect(expanded.nodes.find(node => node.calaId === "fund")?.totalNeighborCount).toBe(13);
  expect(expanded.nodes.find(node => node.calaId === "staff-00")?.hubId).toBe("entity:fund");
  expect(expanded.nodes.find(node => node.calaId === "fund")?.hubId).toBeUndefined();
  expect(expanded.edges.filter(edge => edge.kind === "role").every(edge => edge.sources.length > 0)).toBe(true);
  expect(buildResultGraph(base, { expandedNodeIds: ["fund"] })).toEqual(expanded);
  expect(JSON.stringify(base)).toBe(before);
});

test("a revealed organization becomes another hub and survives collapse of its parent", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  candidate.profile.people = [role("staff", "fund"), role("staff", "former-company"), role("colleague", "former-company")];
  const base = result([candidate]), overview = buildResultGraph(base);
  expect(overview.nodes.some(node => node.calaId === "former-company")).toBe(false);
  const first = buildResultGraph(base, { expandedNodeIds: ["entity:staff"] });
  expect(first.nodes.find(node => node.calaId === "former-company")?.hubId).toBe("entity:staff");
  expect(first.nodes.some(node => node.calaId === "colleague")).toBe(false);
  const chained = buildResultGraph(base, { expandedNodeIds: ["staff", "former-company"] });
  expect(chained.nodes.find(node => node.calaId === "colleague")?.hubId).toBe("entity:former-company");
  expect(chained.nodes.find(node => node.calaId === "former-company")?.kind).toBe("company");
  expect(chained.nodes.find(node => node.calaId === "former-company")?.hubId).toBeUndefined();
  const parentCollapsed = buildResultGraph(base, { expandedNodeIds: ["former-company"] });
  expect(parentCollapsed.nodes.some(node => node.calaId === "colleague")).toBe(true);
  const childCollapsed = buildResultGraph(base, { expandedNodeIds: ["staff"] });
  expect(childCollapsed.nodes.some(node => node.calaId === "colleague")).toBe(false);
  expect(buildResultGraph(base, { expandedNodeIds: [] })).toEqual(overview);
});

test("shared neighbors survive another hub collapsing and latest hubs own shared children without cycles", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  candidate.profile.people = [role("staff-a", "fund"), role("staff-b", "fund"),
    role("staff-a", "old-a"), role("staff-b", "old-b"), role("shared-person", "old-a"), role("shared-person", "old-b")];
  const base = result([candidate]);
  const expanded = buildResultGraph(base, { expandedNodeIds: ["staff-a", "old-a", "staff-b", "old-b"] });
  expect(expanded.nodes.filter(node => node.calaId === "shared-person")).toHaveLength(1);
  expect(expanded.nodes.find(node => node.calaId === "shared-person")?.hubId).toBe("entity:old-b");
  expect(expanded.nodes.filter(node => expanded.expandedNodeIds.includes(node.id)).every(node => !node.hubId)).toBe(true);
  const collapsed = buildResultGraph(base, { expandedNodeIds: ["staff-b", "old-b"] });
  expect(collapsed.nodes.some(node => node.calaId === "old-a")).toBe(false);
  expect(collapsed.nodes.find(node => node.calaId === "shared-person")?.hubId).toBe("entity:old-b");
  expect(collapsed.edges.some(edge => edge.role?.person.id === "shared-person" && edge.role.org.id === "old-b")).toBe(true);
});

test("expanded funding comes only from identified sourced participant records and actual recipients", () => {
  const candidate = card("fund", [funding("shared", ["lead", "fund"])]);
  const extra = funding("profile-round", ["fund"], { company: entity("actual-recipient"), event_date: null });
  candidate.profile.rounds = [extra,
    funding("not-participant", ["someone-else"], { company: entity("wrong-recipient") }),
    funding("no-source", ["fund"], { company: entity("unsourced-recipient"), sources: [] }),
    funding("future-round", ["fund"], { company: entity("future-recipient"), event_date: "2027-01-01" }),
    funding("conflict", ["fund"], { company: entity("conflict-a") }),
    funding("conflict", ["fund"], { company: entity("conflict-b") })];
  const base = result([candidate]);
  expect(buildResultGraph(base).nodes.some(node => node.calaId === "actual-recipient")).toBe(false);
  const expanded = buildResultGraph(base, { expandedNodeIds: ["fund", "entity:wrong-recipient", "missing"] });
  expect(expanded.expandedNodeIds).toEqual(["entity:fund"]);
  expect(expanded.eventCount).toBe(2);
  const edge = expanded.edges.find(edge => edge.eventId === "profile-round")!;
  expect(edge.source).toBe("entity:fund");
  expect(edge.target).toBe("entity:actual-recipient");
  expect(edge.funding?.event_date).toBeNull();
  expect(expanded.edges.some(edge => edge.source === "entity:lead" && edge.target === "entity:actual-recipient")).toBe(false);
  expect(expanded.nodes.some(node => ["wrong-recipient", "unsourced-recipient", "future-recipient", "conflict-a", "conflict-b"].includes(node.calaId || ""))).toBe(false);
});

test("a fully visible neighborhood still becomes a hub and can reveal further saved rounds without new entities", () => {
  const candidate = card("fund", Array.from({ length: 4 }, (_, index) => funding(`round-${index}`, ["lead", "fund"])));
  const base = result([candidate]), overview = buildResultGraph(base);
  expect(overview.eventCount).toBe(3);
  expect(overview.nodes.find(node => node.calaId === "fund")?.availableNeighborCount).toBe(0);
  const expanded = buildResultGraph(base, { expandedNodeIds: ["fund"] });
  expect(expanded.nodes.map(node => node.id)).toEqual(overview.nodes.map(node => node.id));
  expect(expanded.eventCount).toBe(4);
  expect(expanded.nodes.find(node => node.calaId === "portfolio-company")?.hubId).toBe("entity:fund");
  expect(expanded.expandedNodeIds).toEqual(["entity:fund"]);
});

test("expanded graphs preserve their default core under global node and people limits", () => {
  const candidates = Array.from({ length: 5 }, (_, index) => card(`fund-${index}`,
    [funding(`round-${index}`, ["lead", `fund-${index}`], { company: entity(`company-${index}`) })]));
  candidates.forEach(candidate => { candidate.profile.people = Array.from({ length: 30 }, (_, index) =>
    role(`${candidate.investor.id}-staff-${String(index).padStart(2, "0")}`, candidate.investor.id)); });
  const base = result(candidates, { people: [role("founder-person", "startup", "founded"),
    ...Array.from({ length: 30 }, (_, index) => role(`lead-staff-${String(index).padStart(2, "0")}`, "lead"))] });
  base.profile.startup.cala_entity_id = "startup";
  const overview = buildResultGraph(base), ids = ["lead", ...candidates.map(candidate => candidate.investor.id)];
  const expanded = buildResultGraph(base, { expandedNodeIds: ids });
  expect(overview.nodes.every(node => expanded.nodes.some(other => other.id === node.id))).toBe(true);
  expect(expanded.nodes.length).toBeLessThanOrEqual(RESULT_GRAPH_LIMITS.expandedNodes);
  expect(expanded.peopleCount).toBeLessThanOrEqual(RESULT_GRAPH_LIMITS.expandedPeople);
  expect(expanded.nodes.length).toBeGreaterThan(overview.nodes.length);
  expect(new Set(expanded.nodes.map(node => node.id)).size).toBe(expanded.nodes.length);
  expect(new Set(expanded.edges.map(edge => edge.id)).size).toBe(expanded.edges.length);
  const old = new Set(overview.nodes.map(node => node.id));
  for (const hub of expanded.expandedNodeIds) expect(expanded.nodes.filter(node => !old.has(node.id) && node.hubId === hub).length)
    .toBeLessThanOrEqual(RESULT_GRAPH_LIMITS.neighborsPerHub);
  expect(buildResultGraph(base, { expandedNodeIds: ids })).toEqual(expanded);
  expect(expanded.truncated).toBe(true);
});
