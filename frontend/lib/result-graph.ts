import type { CandidateCard, Entity, FundingRound, RunResult, Source, TrustLoop } from "./types";

export type ResultNodeKind = "founder" | "lead" | "investor" | "round" | "company" | "contact";

export interface ResultGraphNode {
  id: string;
  kind: ResultNodeKind;
  label: string;
  x: number;
  y: number;
  calaId: string | null;
  sources: Source[];
  entity?: Entity;
  funding?: FundingRound;
  card?: CandidateCard;
  /** For contact nodes: the trust loops that pass through this person. */
  loops?: TrustLoop[];
}

export interface ResultGraphEdge {
  id: string;
  kind: "founder_route" | "participation" | "issuer" | "trust";
  source: string;
  target: string;
  eventId: string | null;
  sources: Source[];
  /** Trust edges belong to one candidate's loop. */
  investorId?: string;
  confirmed?: boolean;
}

export interface ResultGraph {
  width: number;
  height: number;
  nodes: ResultGraphNode[];
  edges: ResultGraphEdge[];
  eventCount: number;
  investorCount: number;
}

export function graphSources(sources: Source[]): Source[] {
  const unique = new Map<string, Source>();
  for (const source of sources) {
    unique.set(`${source.url}|${source.published_at || ""}|${source.retrieved_at}`, source);
  }
  return [...unique.values()];
}

const spread = (index: number, count: number, height: number) =>
  count <= 1 ? height / 2 : 76 + index * ((height - 180) / (count - 1));

/** Every solid edge is derived from the same identified funding event. */
export function buildResultGraph(result: RunResult): ResultGraph {
  const width = 760;
  const height = 450;
  const lead = result.lead;
  if (!lead || result.state === "no_open_allocation") {
    return { width, height, nodes: [], edges: [], eventCount: 0, investorCount: 0 };
  }
  const cards = result.candidates.slice(0, 5).filter(card => card.investor.id !== lead.id);
  const candidates = new Map(cards.map(card => [card.investor.id, card]));
  const events = new Map<string, FundingRound>();
  const conflicts = new Set<string>();
  for (const card of cards) {
    for (const funding of card.shared_deals.slice(0, 3)) {
      const ids = new Set(funding.participants.map(person => person.investor.id));
      if (!funding.id || !funding.company.id || !funding.sources.length || !ids.has(lead.id) || !ids.has(card.investor.id)) continue;
      const previous = events.get(funding.id);
      if (previous && (previous.company.id !== funding.company.id || previous.event_date !== funding.event_date)) {
        conflicts.add(funding.id);
        continue;
      }
      if (!previous) events.set(funding.id, funding);
      else {
        const participants = new Map(previous.participants.map(person => [person.investor.id, person]));
        for (const person of funding.participants) {
          if (!participants.has(person.investor.id)) participants.set(person.investor.id, person);
        }
        events.set(funding.id, { ...previous, participants: [...participants.values()], sources: graphSources([...previous.sources, ...funding.sources]) });
      }
    }
  }
  const fundingEvents = [...events.values()].filter(event => !conflicts.has(event.id)).slice(0, 15)
    .sort((a, b) => (b.event_date || "").localeCompare(a.event_date || "") || a.id.localeCompare(b.id));
  const nodes = new Map<string, ResultGraphNode>();
  const edges = new Map<string, ResultGraphEdge>();
  const startupId = result.profile.startup.cala_entity_id || null;
  const founderKey = startupId ? `company:${startupId}` : `founder:${result.run_id}`;
  const leadKey = `investor:${lead.id}`;
  nodes.set(founderKey, {
    id: founderKey, kind: "founder", label: result.profile.startup.company_name,
    x: 82, y: 90, calaId: startupId, sources: [],
  });
  nodes.set(leadKey, {
    id: leadKey, kind: "lead", label: lead.name, x: 100, y: height * .56,
    calaId: lead.id, sources: lead.sources, entity: lead,
  });
  edges.set("founder-route", {
    id: "founder-route", kind: "founder_route", source: founderKey, target: leadKey,
    eventId: null, sources: [],
  });
  // Founder-network investors have no shared event with the lead; they are shown through their loops.
  const shownInvestors = cards.filter(card => fundingEvents.some(event => event.participants.some(person => person.investor.id === card.investor.id))
    || (card.origins || []).some(origin => origin !== "lead_network") && (card.loops || []).length > 0);
  shownInvestors.forEach((card, index) => nodes.set(`investor:${card.investor.id}`, {
    id: `investor:${card.investor.id}`, kind: "investor", label: card.investor.name,
    x: 660, y: spread(index, shownInvestors.length, height), calaId: card.investor.id,
    sources: card.sources, entity: card.investor, card,
  }));
  const issuers = new Map<string, Entity>();
  fundingEvents.forEach(event => issuers.set(event.company.id, event.company));
  const distinctIssuers = [...issuers.values()].filter(company => company.id !== startupId);
  distinctIssuers.forEach((company, index) => nodes.set(`company:${company.id}`, {
    id: `company:${company.id}`, kind: "company", label: company.name, x: 466,
    y: spread(index, distinctIssuers.length, height), calaId: company.id,
    sources: company.sources, entity: company,
  }));
  fundingEvents.forEach((funding, index) => {
    const eventKey = `event:${funding.id}`;
    const companyKey = `company:${funding.company.id}`;
    nodes.set(eventKey, {
      id: eventKey, kind: "round", label: funding.stage ? `${funding.stage.replaceAll("_", " ")} round` : "Funding round",
      x: 300, y: spread(index, fundingEvents.length, height), calaId: funding.id,
      sources: funding.sources, funding,
    });
    const companyNode = nodes.get(companyKey);
    if (companyNode) {
      companyNode.sources = graphSources([...companyNode.sources, ...funding.sources]);
      companyNode.entity = funding.company;
    }
    const issuerKey = `${eventKey}:issuer`;
    edges.set(issuerKey, {
      id: issuerKey, kind: "issuer", source: eventKey, target: companyKey,
      eventId: funding.id, sources: funding.sources,
    });
    for (const person of funding.participants) {
      if (person.investor.id !== lead.id && !candidates.has(person.investor.id)) continue;
      const investorKey = `investor:${person.investor.id}`;
      if (!nodes.has(investorKey)) continue;
      const edgeKey = `${investorKey}:${eventKey}`;
      edges.set(edgeKey, {
        id: edgeKey, kind: "participation", source: investorKey, target: eventKey,
        eventId: funding.id, sources: graphSources([...funding.sources, ...person.investor.sources]),
      });
    }
  });
  // Trust loops: founder (or lead, for lead-partner loops) -> common contact -> investor.
  // Contacts sit on the bottom row; edges are dashed because ties are unconfirmed public roles.
  const contactLoops = new Map<string, { entity: Entity; loops: TrustLoop[] }>();
  for (const card of shownInvestors) {
    for (const loop of (card.loops || []).slice(0, 2)) {
      if (!loop.contact) {
        // Existing investor of your company: a direct, sourced edge from your company.
        const direct = `trust:${card.investor.id}:${loop.id}:direct`;
        edges.set(direct, { id: direct, kind: "trust", source: founderKey, target: `investor:${card.investor.id}`, eventId: null,
          sources: graphSources(loop.hops.flatMap(hop => hop.sources)), investorId: card.investor.id, confirmed: true });
        continue;
      }
      const entry = contactLoops.get(loop.contact.id) || { entity: loop.contact, loops: [] };
      entry.loops.push(loop);
      contactLoops.set(loop.contact.id, entry);
      const contactKey = `contact:${loop.contact.id}`;
      const fromKey = loop.kind === "lead_loop" ? leadKey : founderKey;
      const personal = loop.hops.filter(hop => hop.edge_type !== "led" && hop.edge_type !== "person_at_fund");
      const first = `trust:${card.investor.id}:${loop.id}:a`, second = `trust:${card.investor.id}:${loop.id}:b`;
      edges.set(first, { id: first, kind: "trust", source: fromKey, target: contactKey, eventId: null,
        sources: graphSources(personal.flatMap(hop => hop.sources)), investorId: card.investor.id, confirmed: loop.confirmed });
      edges.set(second, { id: second, kind: "trust", source: contactKey, target: `investor:${card.investor.id}`, eventId: null,
        sources: graphSources(loop.hops.flatMap(hop => hop.sources)), investorId: card.investor.id, confirmed: loop.confirmed });
    }
  }
  [...contactLoops.entries()].slice(0, 6).forEach(([id, entry], index, all) => nodes.set(`contact:${id}`, {
    id: `contact:${id}`, kind: "contact", label: entry.entity.name,
    x: all.length <= 1 ? 480 : 240 + index * (400 / (all.length - 1)), y: height - 34, calaId: id,
    sources: graphSources(entry.loops.flatMap(loop => loop.hops.flatMap(hop => hop.sources))), entity: entry.entity, loops: entry.loops,
  }));
  const kept = [...edges.values()].filter(edge => nodes.has(edge.source) && nodes.has(edge.target));
  return { width, height, nodes: [...nodes.values()], edges: kept, eventCount: fundingEvents.length, investorCount: shownInvestors.length };
}

export function graphFocus(graph: ResultGraph, investorId: string | null): { nodes: Set<string>; edges: Set<string> } | null {
  const candidate = graph.nodes.find(node => node.kind === "investor" && node.calaId === investorId);
  if (!candidate) return null;
  const eventIds = new Set(graph.edges.filter(edge => edge.kind === "participation" && edge.source === candidate.id).map(edge => edge.eventId));
  const leadIds = new Set(graph.nodes.filter(node => node.kind === "lead").map(node => node.id));
  const edges = graph.edges.filter(edge => edge.kind === "founder_route" || edge.kind === "trust" && edge.investorId === investorId
    || edge.kind !== "trust" && eventIds.has(edge.eventId)
    && (edge.kind === "issuer" || edge.source === candidate.id || leadIds.has(edge.source)));
  return { nodes: new Set(edges.flatMap(edge => [edge.source, edge.target])), edges: new Set(edges.map(edge => edge.id)) };
}
