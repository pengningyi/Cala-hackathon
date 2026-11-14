import type { CandidateCard, Entity, FundingRound, Role, RunResult, Source, TrustHop, TrustLoop } from "./types";

export type ResultNodeKind = "founder" | "lead" | "investor" | "company" | "person";
export interface ResultGraphNode {
  id: string; kind: ResultNodeKind; label: string; x: number; y: number;
  calaId: string | null; sources: Source[]; entity?: Entity; card?: CandidateCard; loops?: TrustLoop[];
  roles: string[];
  /** Unique neighboring entities in the rendered, bounded graph. */
  degree: number; radius: number;
  /** Direct retrieved neighbors still hidden, and the full available neighborhood. */
  availableNeighborCount: number; totalNeighborCount: number;
  /** Latest active expansion hub owning this non-hub node. */
  hubId?: string;
}
export interface ResultGraphEdge {
  id: string; kind: "founder_route" | "investment" | "role" | "trust";
  source: string; target: string; label: string; eventId: string | null; sources: Source[];
  funding?: FundingRound; role?: Role; hop?: TrustHop;
  investorId?: string; investorIds?: string[]; confirmed?: boolean;
}
export interface ResultGraph {
  width: number; height: number; nodes: ResultGraphNode[]; edges: ResultGraphEdge[];
  eventCount: number; investorCount: number; peopleCount: number;
  omittedNodeCount: number; omittedEventCount: number; truncated: boolean;
  expandedNodeIds: string[];
}
export interface ResultGraphOptions { expandedNodeIds?: readonly string[] }
export const RESULT_GRAPH_LIMITS = { investors: 5, events: 15, dealsPerOrigin: 3, nodes: 28, people: 24,
  expandedNodes: 72, expandedPeople: 60, neighborsPerHub: 8, expandedEvents: 45 } as const;
export function graphSources(sources: Source[]): Source[] {
  const unique = new Map<string, Source>();
  for (const source of sources) unique.set(`${source.url}|${source.published_at || ""}|${source.retrieved_at}`, source);
  return [...unique.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, source]) => source);
}
const key = (id: string) => `entity:${id}`;
const roleLabels: Record<Role["kind"], string> = { founded: "Founder", works_at: "Employment affiliation",
  member_of: "Organization member", board: "Board affiliation", studied_at: "Education affiliation" };
const validDate = (value: string | null | undefined): value is string => !!value && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const personEntity = (entity: Entity) => {
  const type = entity.entity_type?.toLowerCase();
  if (type === "company" || type === "organization") return false;
  return type === "person" || entity.kind === "angel";
};
const stub = (id: string, name: string): Entity => ({ id, name, kind: "unknown", country: null, website: null,
  description: null, aliases: [], related_entity_ids: [], sources: [] });

/** The renderer uses two 22-character label lines below each shape. */
export function resultNodeBounds(node: ResultGraphNode) {
  return { left: node.x - 110, right: node.x + 110, top: node.y - node.radius - 8, bottom: node.y + node.radius + 52 };
}

/** A deterministic, label-aware network layout with no semantic columns. */
function layout(nodes: ResultGraphNode[], edges: ResultGraphEdge[], width: number, height: number) {
  const ordered = [...nodes].sort((a, b) => a.id.localeCompare(b.id)), byId = new Map(ordered.map(node => [node.id, node]));
  const clamp = (node: ResultGraphNode) => {
    node.x = Math.max(120, Math.min(width - 120, node.x));
    node.y = Math.max(76, Math.min(height - 100, node.y));
  };
  // Several event or role records between the same entities must not multiply attraction.
  const pairEdges = [...new Map(edges.map(edge => [[edge.source, edge.target].sort().join("|"), edge])).values()];
  ordered.forEach((node, index) => {
    const angle = index * Math.PI * (3 - Math.sqrt(5)), distance = Math.sqrt((index + .5) / Math.max(1, ordered.length));
    node.x = width / 2 + Math.cos(angle) * distance * (width / 2 - 130);
    node.y = height / 2 + Math.sin(angle) * distance * (height / 2 - 120);
  });
  for (let iteration = 0; iteration < 200; iteration++) {
    const forces = new Map(ordered.map(node => [node.id, { x: 0, y: 0 }]));
    for (let i = 0; i < ordered.length; i++) for (let j = i + 1; j < ordered.length; j++) {
      const a = ordered[i], b = ordered[j], dx = b.x - a.x || .01, dy = b.y - a.y || .01;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const repulsion = 60000 / distance ** 2 + Math.max(0, 230 - distance) * .08;
      const fx = dx / distance * repulsion, fy = dy / distance * repulsion;
      forces.get(a.id)!.x -= fx; forces.get(a.id)!.y -= fy;
      forces.get(b.id)!.x += fx; forces.get(b.id)!.y += fy;
    }
    for (const edge of pairEdges) {
      const a = byId.get(edge.source)!, b = byId.get(edge.target)!;
      const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(1, Math.hypot(dx, dy));
      const pull = (distance - 260) * .004, fx = dx / distance * pull, fy = dy / distance * pull;
      forces.get(a.id)!.x += fx; forces.get(a.id)!.y += fy;
      forces.get(b.id)!.x -= fx; forces.get(b.id)!.y -= fy;
    }
    const cooling = 1 - iteration / 260;
    for (const node of ordered) {
      const force = forces.get(node.id)!;
      node.x += Math.max(-12, Math.min(12, force.x)) * cooling;
      node.y += Math.max(-12, Math.min(12, force.y)) * cooling;
      clamp(node);
    }
  }
  // Resolve the complete shape + label rectangles, not just circular shape collisions.
  for (let pass = 0; pass < 250; pass++) {
    let changed = false;
    for (let i = 0; i < ordered.length; i++) for (let j = i + 1; j < ordered.length; j++) {
      const a = ordered[i], b = ordered[j], dx = b.x - a.x || .01, dy = b.y - a.y || .01;
      const overlapX = 232 - Math.abs(dx), overlapY = a.radius + b.radius + 72 - Math.abs(dy);
      if (overlapX > 0 && overlapY > 0) {
        if (overlapX < overlapY) {
          const push = Math.sign(dx) * (overlapX + .2) / 2;
          a.x -= push; b.x += push;
        } else {
          const push = Math.sign(dy) * (overlapY + .2) / 2;
          a.y -= push; b.y += push;
        }
        clamp(a); clamp(b); changed = true;
      }
    }
    if (!changed) break;
  }
  const collision = ordered.some((a, i) => ordered.slice(i + 1).some(b => {
    const first = resultNodeBounds(a), second = resultNodeBounds(b);
    return first.left < second.right && first.right > second.left && first.top < second.bottom && first.bottom > second.top;
  }));
  if (collision) {
    // Dense components get separated slots across the full canvas. Slots are allocated
    // by their network positions, not by node category, so there are no investor/person columns.
    const columns = Math.min(5, Math.max(1, Math.ceil(Math.sqrt(ordered.length * width / height))));
    const rows = Math.ceil(ordered.length / columns);
    const slots = Array.from({ length: rows * columns }, (_, index) => ({
      x: columns === 1 ? width / 2 : 120 + index % columns * (width - 240) / (columns - 1),
      y: rows === 1 ? height / 2 : 76 + Math.floor(index / columns) * (height - 176) / (rows - 1),
    }));
    for (const node of [...ordered].sort((a, b) => b.degree - a.degree || a.id.localeCompare(b.id))) {
      slots.sort((a, b) => Math.hypot(node.x - a.x, node.y - a.y) - Math.hypot(node.x - b.x, node.y - b.y) || a.y - b.y || a.x - b.x);
      Object.assign(node, slots.shift()!);
    }
  }
  for (const node of ordered) { node.x = Math.round(node.x * 10) / 10; node.y = Math.round(node.y * 10) / 10; }
}

/** Funding is evidence on investor -> actual recipient edges, never an intermediate entity. */
export function buildResultGraph(result: RunResult, options: ResultGraphOptions = {}): ResultGraph {
  const width = 1180;
  const empty: ResultGraph = { width, height: 720, nodes: [], edges: [], eventCount: 0, investorCount: 0,
    peopleCount: 0, omittedNodeCount: 0, omittedEventCount: 0, truncated: false, expandedNodeIds: [] };
  const lead = result.lead;
  if (!lead || result.state === "no_open_allocation") return empty;
  const cards = result.candidates.slice(0, RESULT_GRAPH_LIMITS.investors).filter(card => card.investor.id !== lead.id);
  const startupId = result.profile.startup.cala_entity_id || null;
  const startupKey = startupId ? key(startupId) : `founder:${result.run_id}`, asOf = result.confirmed_at.slice(0, 10);
  const usableRole = (role: Role) => role.sources.length > 0 && !!role.person.id && !!role.org.id
    && role.person.id !== role.org.id && (!role.valid_since || validDate(role.valid_since) && role.valid_since <= asOf)
    && (!role.valid_until || validDate(role.valid_until))
    && !(role.valid_since && role.valid_until && role.valid_until < role.valid_since);
  const usableHop = (hop: TrustHop) => hop.sources.length > 0 && !!hop.from_id && !!hop.to_id && hop.from_id !== hop.to_id
    && (!hop.since || validDate(hop.since) && hop.since <= asOf) && (!hop.until || validDate(hop.until))
    && !(hop.since && hop.until && hop.until < hop.since);
  const events = new Map<string, FundingRound>(), eventOwners = new Map<string, Set<string>>();
  const overviewEventIds = new Set<string>();
  const eventPriority = new Map<string, number>(), conflicts = new Set<string>();
  const scalarEvidence = new Map<string, { stages: Set<string>; precisions: Set<string>; totals: Set<string> }>();
  const references = cards.flatMap(card => [
    ...(card.origin_rounds || []).map((funding, index) => ({ card, funding, shared: false, priority: 0,
      overview: index < RESULT_GRAPH_LIMITS.dealsPerOrigin })),
    ...card.shared_deals.map((funding, index) => ({ card, funding, shared: true, priority: 1,
      overview: index < RESULT_GRAPH_LIMITS.dealsPerOrigin })),
    ...card.profile.rounds.map(funding => ({ card, funding, shared: false, priority: 2, overview: false })),
  ]);
  for (const { card, funding, shared, priority, overview } of references) {
    const ids = new Set(funding.participants.map(participant => participant.investor.id));
    if (!funding.id?.trim() || !funding.company.id?.trim() || !funding.sources.length || !ids.has(card.investor.id)
        || shared && !ids.has(lead.id)) continue;
    const previous = events.get(funding.id);
    if (previous && (previous.company.id !== funding.company.id
        || previous.event_date && funding.event_date && previous.event_date !== funding.event_date)) conflicts.add(funding.id);
    if (funding.event_date && (!validDate(funding.event_date) || funding.event_date > asOf)) conflicts.add(funding.id);
    if (shared && !validDate(funding.event_date)) continue;
    if (overview) overviewEventIds.add(funding.id);
    const facts = scalarEvidence.get(funding.id) || { stages: new Set<string>(), precisions: new Set<string>(), totals: new Set<string>() };
    if (funding.stage) facts.stages.add(funding.stage);
    if (funding.event_date_precision) facts.precisions.add(funding.event_date_precision);
    if (funding.total_amount_eur !== null) facts.totals.add(funding.total_amount_eur);
    scalarEvidence.set(funding.id, facts);
    if (!previous) events.set(funding.id, funding);
    else {
      const participants = new Map(previous.participants.map(participant => [participant.investor.id, { ...participant }]));
      for (const participant of funding.participants) {
        const prior = participants.get(participant.investor.id);
        participants.set(participant.investor.id, prior ? { ...prior,
          is_lead: prior.is_lead === participant.is_lead ? prior.is_lead : null } : participant);
      }
      events.set(funding.id, { ...previous, event_date: previous.event_date || funding.event_date,
        stage: facts.stages.size === 1 ? [...facts.stages][0] : null,
        event_date_precision: facts.precisions.size === 1 ? [...facts.precisions][0] : null,
        total_amount_eur: facts.totals.size === 1 ? [...facts.totals][0] : null,
        participants: [...participants.values()], sources: graphSources([...previous.sources, ...funding.sources]) });
    }
    eventOwners.set(funding.id, new Set([...(eventOwners.get(funding.id) || []), card.investor.id]));
    eventPriority.set(funding.id, Math.min(eventPriority.get(funding.id) ?? 2, priority));
  }
  const availableEvents = [...events.values()].filter(event => !conflicts.has(event.id)).sort((a, b) =>
    (eventPriority.get(a.id)! - eventPriority.get(b.id)!) || (b.event_date || "").localeCompare(a.event_date || "") || a.id.localeCompare(b.id));
  const overviewEvents = availableEvents.filter(event => overviewEventIds.has(event.id));
  // Reserve evidence for each candidate before filling the global event budget.
  const selectedIds = new Set<string>();
  for (const card of cards) {
    const first = overviewEvents.find(event => eventOwners.get(event.id)?.has(card.investor.id));
    if (first) selectedIds.add(first.id);
  }
  for (const funding of overviewEvents) if (selectedIds.size < RESULT_GRAPH_LIMITS.events) selectedIds.add(funding.id);
  const fundingEvents = overviewEvents.filter(event => selectedIds.has(event.id));
  const hasLoop = (card: CandidateCard) => (card.loops || []).some(loop => loop.hops.some(usableHop));
  const hasPeople = (card: CandidateCard) => [...(result.people || []), ...(card.profile.people || [])]
    .some(role => usableRole(role) && role.org.id === card.investor.id);
  const shownCards = cards.filter(card => hasLoop(card) || hasPeople(card)
    || fundingEvents.some(event => eventOwners.get(event.id)?.has(card.investor.id)));
  const shownIds = new Set(shownCards.map(card => card.investor.id));
  const roles = [...(result.people || []), ...shownCards.flatMap(card => card.profile.people || [])]
    .filter(usableRole);
  const registry = new Map<string, Entity>(), personIds = new Set<string>(), nodeRoles = new Map<string, Set<string>>();
  const organizationIds = new Set([
    ...(!personEntity(lead) ? [lead.id] : []), ...shownCards.filter(card => !personEntity(card.investor)).map(card => card.investor.id),
    ...availableEvents.map(funding => funding.company.id), ...roles.map(role => role.org.id), ...(startupId ? [startupId] : []),
  ]);
  const remember = (entity: Entity, label?: string, person = false) => {
    if (!entity.id) return;
    const old = registry.get(entity.id);
    registry.set(entity.id, old ? { ...((entity.metadata?.length || 0) > (old.metadata?.length || 0) ? entity : old),
      sources: graphSources([...old.sources, ...entity.sources]) } : entity);
    if (["company", "organization"].includes(entity.entity_type?.toLowerCase() || "") || entity.kind === "company") organizationIds.add(entity.id);
    if ((person || personEntity(entity)) && !organizationIds.has(entity.id)) personIds.add(entity.id);
    if (organizationIds.has(entity.id)) personIds.delete(entity.id);
    if (label) nodeRoles.set(entity.id, new Set([...(nodeRoles.get(entity.id) || []), label]));
  };
  remember(lead, "Lead investor");
  for (const card of shownCards) remember(card.investor, "Shortlisted investor");
  for (const funding of availableEvents) remember(funding.company, "Funded company");
  for (const role of roles) { remember(role.person, roleLabels[role.kind], true); remember(role.org); }
  const loopEntries = shownCards.flatMap(card => (card.loops || []).slice(0, 3).map(loop => ({ card, loop })));
  for (const { loop } of loopEntries) {
    if (loop.contact) remember(loop.contact);
    for (const hop of loop.hops) {
      if (!usableHop(hop)) continue;
      const personal = ["worked_together", "board", "same_school"].includes(hop.edge_type);
      remember(stub(hop.from_id, hop.from_name), undefined, personal);
      remember(stub(hop.to_id, hop.to_name), undefined, personal);
      if (hop.org) remember(hop.org);
      if (["founded", "person_at_fund"].includes(hop.edge_type)) {
        // Trust paths are undirected traversals: fund -> partner is still the
        // partner's affiliation with the fund, never a fund becoming a person.
        const personId = organizationIds.has(hop.from_id) ? hop.to_id : hop.from_id;
        if (!organizationIds.has(personId)) personIds.add(personId);
      }
    }
  }
  const nodes = new Map<string, ResultGraphNode>(), edges = new Map<string, ResultGraphEdge>(), omitted = new Set<string>();
  let peopleCount = 0;
  let expanding = false;
  const addNode = (id: string, forcedKind?: ResultNodeKind) => {
    if (startupId && id === startupId && nodes.has(startupKey)) return nodes.get(startupKey);
    const nodeId = key(id);
    if (nodes.has(nodeId)) return nodes.get(nodeId);
    const entity = registry.get(id);
    if (!entity) return;
    const card = shownCards.find(candidate => candidate.investor.id === id);
    const kind = personIds.has(id) ? "person" : forcedKind || (id === lead.id ? "lead" : card ? "investor" : "company");
    if (nodes.size >= (expanding ? RESULT_GRAPH_LIMITS.expandedNodes : RESULT_GRAPH_LIMITS.nodes)
        || kind === "person" && peopleCount >= (expanding ? RESULT_GRAPH_LIMITS.expandedPeople : RESULT_GRAPH_LIMITS.people)) {
      omitted.add(id); return;
    }
    const node: ResultGraphNode = { id: nodeId, kind, label: entity.name, x: 0, y: 0, calaId: id,
      sources: entity.sources, entity, card, roles: [...(nodeRoles.get(id) || [])].sort(), degree: 0, radius: 16,
      availableNeighborCount: 0, totalNeighborCount: 0 };
    if (kind === "person") peopleCount++;
    nodes.set(nodeId, node); return node;
  };
  nodes.set(startupKey, { id: startupKey, kind: "founder", label: result.profile.startup.company_name,
    x: 0, y: 0, calaId: startupId, sources: [], roles: ["Your company"], degree: 0, radius: 16,
    availableNeighborCount: 0, totalNeighborCount: 0,
    entity: startupId ? registry.get(startupId) : undefined });
  addNode(lead.id, "lead");
  for (const card of shownCards) addNode(card.investor.id, "investor");
  for (const funding of fundingEvents) {
    const company = addNode(funding.company.id, "company");
    if (company) {
      company.entity = registry.get(funding.company.id);
      company.sources = graphSources([...company.sources, ...funding.sources, ...funding.company.sources]);
    }
  }
  const orgs = new Map<string, Set<string>>();
  for (const role of roles) orgs.set(role.person.id, new Set([...(orgs.get(role.person.id) || []), role.org.id]));
  const founderIds = new Set([...(result.profile.founder_person_ids || []), ...roles
    .filter(role => role.kind === "founded" && role.org.id === startupId).map(role => role.person.id)]);
  for (const id of [...founderIds].sort()) addNode(id);
  for (const { loop } of loopEntries) for (const hop of loop.hops) if (usableHop(hop)) { addNode(hop.from_id); addNode(hop.to_id); }
  const coreOrganizations = new Set([...nodes.values()].filter(node => node.kind !== "person" && node.calaId).map(node => node.calaId!));
  for (const id of [...orgs.keys()].filter(id => [...orgs.get(id)!].filter(org => coreOrganizations.has(org)).length > 1).sort()) addNode(id);
  const staffByOrg = new Map<string, string[]>();
  for (const role of roles) {
    const people = staffByOrg.get(role.org.id) || [];
    if (!people.includes(role.person.id)) people.push(role.person.id);
    staffByOrg.set(role.org.id, people);
  }
  const groups = [...staffByOrg.entries()].filter(([orgId]) => orgId === lead.id || shownIds.has(orgId))
    .sort(([a], [b]) => a.localeCompare(b));
  // A balanced initial view: several relevant fund people, not everyone's entire employment history.
  for (let index = 0; index < 2; index++) for (const [, people] of groups) {
    if (people[index]) addNode(people[index]);
  }
  const selectedPeople = new Set([...nodes.values()].filter(node => node.kind === "person").map(node => node.calaId));
  const bridgingOrganizations = [...staffByOrg.entries()].filter(([orgId, people]) => !coreOrganizations.has(orgId)
    && people.filter(id => selectedPeople.has(id)).length >= 2).sort(([a], [b]) => a.localeCompare(b));
  for (const [orgId] of bridgingOrganizations) addNode(orgId);
  // Expose intentional relevance omissions as well as hard budget omissions.
  for (const id of registry.keys()) if (!nodes.has(key(id))) {
    if (id !== startupId) omitted.add(id);
  }
  const addEdge = (edge: ResultGraphEdge) => {
    if (!nodes.has(edge.source) || !nodes.has(edge.target) || edge.source === edge.target) return;
    const old = edges.get(edge.id);
    edges.set(edge.id, old ? { ...old, sources: graphSources([...old.sources, ...edge.sources]),
      investorIds: [...new Set([...(old.investorIds || []), ...(edge.investorIds || [])])].sort() } : edge);
  };
  addEdge({ id: "founder-route", kind: "founder_route", source: startupKey, target: key(lead.id),
    label: "Founder-supplied introduction route", eventId: null, sources: [] });
  for (const funding of fundingEvents) for (const participant of funding.participants) {
    if (participant.investor.id !== lead.id && !shownIds.has(participant.investor.id)) continue;
    const investorId = participant.investor.id;
    addEdge({ id: `investment:${funding.id}:${investorId}:${funding.company.id}`, kind: "investment",
      source: key(investorId), target: key(funding.company.id), eventId: funding.id, funding,
      label: funding.stage ? `${funding.stage.replaceAll("_", " ")} investment` : "Identified investment",
      sources: graphSources([...funding.sources, ...participant.investor.sources]), investorId, investorIds: [investorId] });
  }
  for (const role of roles) addEdge({
    id: `role:${role.kind}:${role.person.id}:${role.org.id}:${role.valid_since || ""}:${role.valid_until || ""}:${role.current ? "current" : "unconfirmed"}`,
    kind: "role", source: key(role.person.id), target: key(role.org.id), label: roleLabels[role.kind],
    eventId: null, role, sources: role.sources,
  });
  for (const { card, loop } of loopEntries) for (const hop of loop.hops) {
    if (!usableHop(hop) || hop.edge_type === "led") continue;
    let from = nodes.get(key(hop.from_id)), to = nodes.get(key(hop.to_id));
    if (!from || !to) continue;
    if (["founded", "person_at_fund"].includes(hop.edge_type) && from.kind !== "person" && to.kind === "person") [from, to] = [to, from];
    for (const node of [from, to]) {
      node.sources = graphSources([...node.sources, ...hop.sources]);
      node.loops = [...(node.loops || []).filter(item => item.id !== loop.id), loop];
    }
    if (hop.edge_type === "invested_in" || hop.edge_type === "co_invested") {
      // Investment hops need an identified event; never invent personal investment edges.
      for (const edge of edges.values()) if (edge.kind === "investment" && edge.funding) {
        const ids = new Set(edge.funding.participants.map(participant => participant.investor.id));
        const matches = hop.edge_type === "co_invested" ? ids.has(hop.from_id) && ids.has(hop.to_id)
          : edge.funding.company.id === hop.from_id && edge.source === key(hop.to_id)
            || edge.funding.company.id === hop.to_id && edge.source === key(hop.from_id);
        if (matches) edge.investorIds = [...new Set([...(edge.investorIds || []), card.investor.id])].sort();
      }
      continue;
    }
    const correspondingRole = [...edges.values()].find(edge => edge.kind === "role" && edge.source === from.id && edge.target === to.id
      && (hop.edge_type === "founded" && edge.role?.kind === "founded"
        || hop.edge_type === "person_at_fund" && ["works_at", "member_of"].includes(edge.role?.kind || "")));
    if (correspondingRole) {
      correspondingRole.investorIds = [...new Set([...(correspondingRole.investorIds || []), card.investor.id])].sort();
      continue;
    }
    addEdge({ id: `trust:${hop.edge_type}:${from.calaId}:${to.calaId}`, kind: "trust", source: from.id, target: to.id,
      label: ({ worked_together: "Professional role overlap", board: "Board-related overlap", same_school: "Shared education",
        founded: "Founded", person_at_fund: "Fund affiliation" } as Partial<Record<TrustHop["edge_type"], string>>)[hop.edge_type] || "Public professional relationship",
      eventId: null, hop: { ...hop, from_id: from.calaId!, from_name: from.label, to_id: to.calaId!, to_name: to.label },
      sources: hop.sources, investorId: card.investor.id, investorIds: [card.investor.id], confirmed: loop.confirmed });
  }
  const connected = new Set([...edges.values()].flatMap(edge => [edge.source, edge.target]));
  for (const node of nodes.values()) if (!connected.has(node.id) && node.kind !== "founder" && node.kind !== "lead" && !node.card) {
    nodes.delete(node.id);
    if (node.calaId) omitted.add(node.calaId);
    if (node.kind === "person") peopleCount--;
  }
  const overviewNodeIds = new Set(nodes.keys());
  const overviewFundingIds = new Set([...edges.values()].filter(edge => edge.kind === "investment").map(edge => edge.eventId!));
  // Build the local evidence universe without adding hidden records to the overview.
  // No neighbor is invented from a name, a score or an investment hop alone.
  const availableEdges = new Map(edges);
  const addAvailableEdge = (edge: ResultGraphEdge) => {
    if (edge.source === edge.target) return;
    const old = availableEdges.get(edge.id);
    availableEdges.set(edge.id, old ? { ...old, sources: graphSources([...old.sources, ...edge.sources]),
      investorIds: [...new Set([...(old.investorIds || []), ...(edge.investorIds || [])])].sort() } : edge);
  };
  for (const funding of availableEvents) for (const participant of funding.participants) {
    const investorId = participant.investor.id;
    if (investorId !== lead.id && !shownIds.has(investorId)) continue;
    addAvailableEdge({ id: `investment:${funding.id}:${investorId}:${funding.company.id}`, kind: "investment",
      source: key(investorId), target: key(funding.company.id), eventId: funding.id, funding,
      label: funding.stage ? `${funding.stage.replaceAll("_", " ")} investment` : "Identified investment",
      sources: graphSources([...funding.sources, ...participant.investor.sources]), investorId, investorIds: [investorId] });
  }
  for (const role of roles) addAvailableEdge({
    id: `role:${role.kind}:${role.person.id}:${role.org.id}:${role.valid_since || ""}:${role.valid_until || ""}:${role.current ? "current" : "unconfirmed"}`,
    kind: "role", source: key(role.person.id), target: key(role.org.id), label: roleLabels[role.kind],
    eventId: null, role, sources: role.sources,
  });
  for (const { card, loop } of loopEntries) for (const hop of loop.hops) {
    if (!usableHop(hop) || hop.edge_type === "led") continue;
    if (hop.edge_type === "invested_in" || hop.edge_type === "co_invested") {
      for (const edge of availableEdges.values()) if (edge.kind === "investment" && edge.funding) {
        const participants = new Set(edge.funding.participants.map(item => item.investor.id));
        const matches = hop.edge_type === "co_invested" ? participants.has(hop.from_id) && participants.has(hop.to_id)
          : edge.funding.company.id === hop.from_id && edge.source === key(hop.to_id)
            || edge.funding.company.id === hop.to_id && edge.source === key(hop.from_id);
        if (matches) edge.investorIds = [...new Set([...(edge.investorIds || []), card.investor.id])].sort();
      }
      continue;
    }
    let fromId = hop.from_id, toId = hop.to_id;
    if (["founded", "person_at_fund"].includes(hop.edge_type) && !personIds.has(fromId) && personIds.has(toId)) [fromId, toId] = [toId, fromId];
    const from = key(fromId), to = key(toId);
    const correspondingRole = [...availableEdges.values()].find(edge => edge.kind === "role" && edge.source === from && edge.target === to
      && (hop.edge_type === "founded" && edge.role?.kind === "founded"
        || hop.edge_type === "person_at_fund" && ["works_at", "member_of"].includes(edge.role?.kind || "")));
    if (correspondingRole) {
      correspondingRole.investorIds = [...new Set([...(correspondingRole.investorIds || []), card.investor.id])].sort();
      continue;
    }
    addAvailableEdge({ id: `trust:${hop.edge_type}:${fromId}:${toId}`, kind: "trust", source: from, target: to,
      label: ({ worked_together: "Professional role overlap", board: "Board-related overlap", same_school: "Shared education",
        founded: "Founded", person_at_fund: "Fund affiliation" } as Partial<Record<TrustHop["edge_type"], string>>)[hop.edge_type] || "Public professional relationship",
      eventId: null, hop: { ...hop, from_id: fromId, from_name: registry.get(fromId)?.name || hop.from_name,
        to_id: toId, to_name: registry.get(toId)?.name || hop.to_name }, sources: hop.sources,
      investorId: card.investor.id, investorIds: [card.investor.id], confirmed: loop.confirmed });
  }
  const allNeighbors = new Map<string, Set<string>>();
  for (const edge of availableEdges.values()) {
    allNeighbors.set(edge.source, new Set([...(allNeighbors.get(edge.source) || []), edge.target]));
    allNeighbors.set(edge.target, new Set([...(allNeighbors.get(edge.target) || []), edge.source]));
  }
  const expandedNodeIds: string[] = [];
  expanding = true;
  for (const requested of options.expandedNodeIds || []) {
    const id = requested === startupKey ? startupKey : requested.startsWith("entity:") ? requested : key(requested);
    if (expandedNodeIds.includes(id) || !allNeighbors.has(id) && !nodes.has(id)) continue;
    if (!nodes.has(id) && id.startsWith("entity:")) addNode(id.slice(7));
    if (nodes.has(id)) expandedNodeIds.push(id);
  }
  const priority = (id: string) => {
    const raw = id.startsWith("entity:") ? id.slice(7) : id;
    if (founderIds.has(raw)) return 0;
    if (personIds.has(raw) && (orgs.get(raw)?.size || 0) > 1) return 1;
    if (id === key(lead.id) || shownIds.has(raw)) return 1;
    if (personIds.has(raw)) return 2;
    if ((staffByOrg.get(raw)?.length || 0) > 1) return 3;
    return 4;
  };
  // Each hub's first batch is chosen against the unchanged overview, so shared
  // children survive another hub collapsing instead of shifting between batches.
  for (const hub of expandedNodeIds) {
    const hidden = [...(allNeighbors.get(hub) || [])].filter(id => !overviewNodeIds.has(id))
      .sort((a, b) => priority(a) - priority(b) || a.localeCompare(b)).slice(0, RESULT_GRAPH_LIMITS.neighborsPerHub);
    for (const id of hidden) if (id.startsWith("entity:")) addNode(id.slice(7));
  }
  const visibleFundingIds = new Set(overviewFundingIds);
  if (expandedNodeIds.length) for (const funding of availableEvents) {
    if (visibleFundingIds.size >= RESULT_GRAPH_LIMITS.expandedEvents) break;
    const relevant = [...availableEdges.values()].some(edge => edge.eventId === funding.id && nodes.has(edge.source) && nodes.has(edge.target)
      && (expandedNodeIds.includes(edge.source) || expandedNodeIds.includes(edge.target)
        || !overviewNodeIds.has(edge.source) || !overviewNodeIds.has(edge.target)));
    if (relevant) visibleFundingIds.add(funding.id);
  }
  const renderedEdges = [...availableEdges.values()].filter(edge => nodes.has(edge.source) && nodes.has(edge.target)
    && (edge.kind !== "investment" || visibleFundingIds.has(edge.eventId!)));
  const renderedConnected = new Set(renderedEdges.flatMap(edge => [edge.source, edge.target]));
  for (const node of nodes.values()) if (!renderedConnected.has(node.id) && !overviewNodeIds.has(node.id) && !expandedNodeIds.includes(node.id)) {
    nodes.delete(node.id);
    if (node.kind === "person") peopleCount--;
  }
  const neighbors = new Map([...nodes.keys()].map(id => [id, new Set<string>()]));
  for (const edge of renderedEdges) { neighbors.get(edge.source)!.add(edge.target); neighbors.get(edge.target)!.add(edge.source); }
  const maxDegree = Math.max(1, ...[...neighbors.values()].map(related => related.size));
  for (const node of nodes.values()) {
    node.degree = neighbors.get(node.id)!.size;
    node.radius = 16 + 18 * Math.sqrt(node.degree / maxDegree);
    node.totalNeighborCount = allNeighbors.get(node.id)?.size || 0;
    node.availableNeighborCount = [...(allNeighbors.get(node.id) || [])].filter(id => !nodes.has(id)).length;
  }
  // Any node can become a hub, even when its entire neighborhood was visible.
  // Active hubs retain their own centers; the latest hub owns shared neighbors.
  for (const hub of expandedNodeIds) for (const neighbor of neighbors.get(hub) || []) {
    if (!expandedNodeIds.includes(neighbor)) nodes.get(neighbor)!.hubId = hub;
  }
  const height = Math.max(720, Math.min(1080, 600 + Math.ceil(nodes.size / 5) * 65));
  layout([...nodes.values()], renderedEdges, width, height);
  const eventCount = new Set(renderedEdges.filter(edge => edge.kind === "investment").map(edge => edge.eventId)).size;
  const omittedEventCount = Math.max(0, availableEvents.length - eventCount);
  const omittedNodeCount = [...allNeighbors.keys()].filter(id => !nodes.has(id)).length;
  return { width, height, nodes: [...nodes.values()], edges: renderedEdges,
    eventCount, investorCount: shownCards.length, peopleCount, omittedNodeCount, omittedEventCount, expandedNodeIds,
    truncated: omittedNodeCount > 0 || omittedEventCount > 0 || result.candidates.length > cards.length };
}

/** Every entity or evidence edge can be focused; callers can clear selection on a second click. */
export function graphFocus(graph: ResultGraph, selectedId: string | null): { nodes: Set<string>; edges: Set<string> } | null {
  if (!selectedId) return null;
  const selectedEdge = graph.edges.find(edge => edge.id === selectedId);
  if (selectedEdge) return { nodes: new Set([selectedEdge.source, selectedEdge.target]), edges: new Set([selectedEdge.id]) };
  const node = graph.nodes.find(item => item.id === selectedId || item.calaId === selectedId);
  if (!node) return null;
  const edges = graph.edges.filter(edge => edge.source === node.id || edge.target === node.id);
  return { nodes: new Set([node.id, ...edges.flatMap(edge => [edge.source, edge.target])]), edges: new Set(edges.map(edge => edge.id)) };
}
