"""Trust loops over one people + institutions graph; feeds the Warm path and Lead-closed path rules.

Intuition: a short closed loop containing the founder, a common contact and the
candidate's partner is the strongest public evidence that the candidate is
reachable. We measure mutual connections from adjacency lists (common
neighbours), never by enumerating cycles, and ignore loops longer than four
edges: a founder-side path of at most two edges plus the same path back.

Nodes are people and institutions in one graph. "Home" is your company plus its
founders; a candidate unit is the fund plus its current people. Paths from home to
the unit have at most two edges (direct, or via one common contact: a person or a
company). When the fund also co-invested with your lead, the path closes back home
through the lead (fund - co_invested - lead - led - home), giving loops of at most
four edges: a triangle for an existing investor of yours who co-invested with your
lead, and a four-edge loop through a founder's previous company or a common contact.

Edges come only from sourced, dated public professional roles (employment,
membership, founding, board seats, schools) and identified funding events.
A shared employer is not proof that two people know each other; every loop is
labelled unconfirmed until the founder ticks "I know this person".
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date

from .models import Entity, FundingRound, Role, Source, TrustHop, TrustLoop

# Type weights in 0-1; order worked_together > board > co_invested > same_school.
EDGE_WEIGHTS = {
    "worked_together": 1.0, "board": 0.8, "person_at_fund": 0.9, "led": 1.0,
    "co_invested": 0.6, "same_school": 0.2,
    # Institutional ties from identified funding events and founder roles.
    "invested_in_startup": 1.0,          # an existing investor of your company
    "invested_in_previous": 0.7,         # backed a founder's previous company
    "founded": 1.0,
}
LEAD_CLOSURE_BONUS = 1.0       # contribution x (1 + bonus x co_invested strength) when the loop closes via your lead
LEAD_LOOP_BONUS = 1.5          # paths that start at the founder's own lead partner
CONFIRMED_LOOP_BONUS = 1.5     # founder ticked "I know this person"
RECENCY_HALF_LIFE_YEARS = 4.0
FULL_OVERLAP_DAYS = 730        # two years of shared time counts in full
SMALL_ORG_HEADCOUNT = 10       # organisations this small get the full size factor
UNKNOWN_SIZE_FACTOR = 0.6
MAX_LOOPS = 3

PERSONAL_TYPES = {"worked_together", "board", "same_school"}
# Edges a home-to-candidate path may use (lead edges only close the loop).
PATH_TYPES = PERSONAL_TYPES | {"invested_in", "founded", "person_at_fund"}
_WORK_KINDS = {"works_at", "member_of", "founded"}


@dataclass
class Edge:
    type: str
    strength: float
    org: Entity | None = None
    since: date | None = None
    until: date | None = None
    sources: list[Source] = field(default_factory=list)


@dataclass
class Graph:
    nodes: dict[str, Entity] = field(default_factory=dict)
    adj: dict[str, dict[str, Edge]] = field(default_factory=dict)
    startup_id: str | None = None

    def add(self, a: Entity, b: Entity, edge: Edge):
        if a.id == b.id or edge.strength <= 0:
            return
        self.nodes.setdefault(a.id, a)
        self.nodes.setdefault(b.id, b)
        for x, y in ((a.id, b.id), (b.id, a.id)):
            current = self.adj.setdefault(x, {}).get(y)
            # Keep the strongest evidence for a pair; ties keep the first (deterministic input order).
            if current is None or edge.strength > current.strength:
                self.adj[x][y] = edge

    def personal(self, node_id: str) -> dict[str, Edge]:
        return {k: e for k, e in self.adj.get(node_id, {}).items() if e.type in PERSONAL_TYPES}

    def degree(self, node_id: str) -> int:
        """Hub size for the Adamic-Adar discount: all path-type neighbours."""
        return len([1 for e in self.adj.get(node_id, {}).values() if e.type in PATH_TYPES])

    def path_neighbours(self, node_id: str) -> dict[str, Edge]:
        return {k: e for k, e in self.adj.get(node_id, {}).items() if e.type in PATH_TYPES}


def _unique(sources: list[Source]) -> list[Source]:
    found = {(str(s.url), str(s.published_at), s.retrieved_at.isoformat()): s for s in sources}
    return [found[key] for key in sorted(found)]


def _end(role: Role, as_of: date) -> date | None:
    if role.valid_until:
        return min(role.valid_until, as_of)
    return as_of if role.current else None


def is_current(role: Role, as_of: date) -> bool:
    if role.valid_since and role.valid_since > as_of:
        return False
    return role.current or (role.valid_until is not None and role.valid_until >= as_of)


def size_factor(headcount: int | None) -> float:
    """Shared time at a 30-person company counts far more than at a 100k-person one."""
    if not headcount:
        return UNKNOWN_SIZE_FACTOR
    return min(1.0, math.log2(2 + SMALL_ORG_HEADCOUNT) / math.log2(2 + headcount))


def overlap_edge(a: Role, b: Role, as_of: date, headcount: int | None) -> Edge | None:
    """Two roles at one organisation; non-overlapping or undated ranges give no edge."""
    if a.person.id == b.person.id or a.org.id != b.org.id or not (a.valid_since and b.valid_since):
        return None
    end_a, end_b = _end(a, as_of), _end(b, as_of)
    if end_a is None or end_b is None:
        return None
    start, end = max(a.valid_since, b.valid_since), min(end_a, end_b)
    if end <= start:
        return None
    if a.kind == "studied_at" and b.kind == "studied_at":
        kind = "same_school"
    elif a.kind == "studied_at" or b.kind == "studied_at":
        return None
    elif "board" in (a.kind, b.kind):
        kind = "board"
    else:
        kind = "worked_together"
    overlap = min(1.0, (end - start).days / FULL_OVERLAP_DAYS)
    recency = 0.5 ** (max(0, (as_of - end).days) / 365.25 / RECENCY_HALF_LIFE_YEARS)
    strength = EDGE_WEIGHTS[kind] * size_factor(headcount) * overlap * recency
    return Edge(kind, round(min(1.0, strength), 4), a.org, start, end, _unique(a.sources + b.sources))


@dataclass
class Candidate:
    fund: Entity
    shared: list[FundingRound]
    # Identified rounds of your company or a founder's previous company that this fund joined.
    invested: list[FundingRound] = field(default_factory=list)


def _recency(when: date | None, as_of: date) -> float:
    if when is None:
        return 0.5
    return 0.5 ** (max(0, (as_of - when).days) / 365.25 / RECENCY_HALF_LIFE_YEARS)


def build_graph(startup: Entity, founders: list[Entity], lead: Entity, lead_partner: Entity | None,
                candidates: list[Candidate], roles: list[Role], org_headcounts: dict[str, int],
                as_of: date) -> tuple[Graph, dict]:
    graph = Graph(startup_id=startup.id)
    graph.nodes[startup.id] = startup
    for person in founders:
        graph.nodes[person.id] = person
    unique_roles = {}
    for role in roles:
        key = (role.person.id, role.org.id, role.kind, role.valid_since, role.valid_until)
        unique_roles.setdefault(key, role)
    roles = [unique_roles[k] for k in sorted(unique_roles, key=lambda k: tuple(str(x) for x in k))]

    # People at the same organisation with overlapping dates.
    by_org: dict[str, list[Role]] = {}
    for role in roles:
        by_org.setdefault(role.org.id, []).append(role)
    for org_id, org_roles in by_org.items():
        headcount = org_headcounts.get(org_id) or len({r.person.id for r in org_roles})
        for i, a in enumerate(org_roles):
            for b in org_roles[i + 1:]:
                edge = overlap_edge(a, b, as_of, headcount)
                if edge:
                    graph.add(a.person, b.person, edge)

    fund_ids = {lead.id, *(c.fund.id for c in candidates)}
    for role in roles:
        if role.org.id in fund_ids and role.kind in ("works_at", "member_of") and is_current(role, as_of):
            graph.add(role.person, role.org, Edge("person_at_fund", EDGE_WEIGHTS["person_at_fund"], role.org,
                                                  role.valid_since, role.valid_until, role.sources))
    if lead_partner:
        graph.add(lead_partner, lead, Edge("person_at_fund", EDGE_WEIGHTS["person_at_fund"], lead))
    graph.add(lead, startup, Edge("led", EDGE_WEIGHTS["led"], startup))

    # Founders -> previous companies they founded (not your company itself).
    founder_ids = {p.id for p in founders}
    for role in roles:
        if role.person.id in founder_ids and role.kind == "founded" and role.org.id != startup.id:
            end = _end(role, as_of) or role.valid_since
            graph.add(role.person, role.org, Edge("founded", round(EDGE_WEIGHTS["founded"] * _recency(end, as_of), 4),
                                                  role.org, role.valid_since, role.valid_until, role.sources))
    # Funds -> companies they backed in identified rounds (your company or a founder's previous one).
    for candidate in candidates:
        by_company: dict[str, list[FundingRound]] = {}
        for funding in candidate.invested:
            if any(p.investor.id == candidate.fund.id for p in funding.participants):
                by_company.setdefault(funding.company.id, []).append(funding)
        for company_id, rounds in sorted(by_company.items()):
            latest = max((r.event_date for r in rounds if r.event_date), default=None)
            weight = EDGE_WEIGHTS["invested_in_startup" if company_id == startup.id else "invested_in_previous"]
            company = startup if company_id == startup.id else rounds[0].company
            graph.add(candidate.fund, company, Edge("invested_in", round(weight * _recency(latest, as_of), 4), company,
                                                    min((r.event_date for r in rounds if r.event_date), default=None),
                                                    latest, _unique([s for r in rounds for s in r.sources])))

    for candidate in candidates:
        n = len(candidate.shared)
        strength = min(1.0, EDGE_WEIGHTS["co_invested"] * min(1.0, n / 3))
        sources = _unique([s for r in candidate.shared for s in r.sources])
        latest = max((r.event_date for r in candidate.shared if r.event_date), default=None)
        graph.add(candidate.fund, lead, Edge("co_invested", round(strength, 4), None, None, latest, sources))
    return graph, {}


def _hop(graph: Graph, a: str, b: str) -> TrustHop:
    edge = graph.adj[a][b]
    return TrustHop(from_id=a, from_name=graph.nodes[a].name, to_id=b, to_name=graph.nodes[b].name,
                    edge_type=edge.type, org=edge.org, since=edge.since, until=edge.until,
                    strength=edge.strength, sources=edge.sources)


def _loop_id(node_ids: list[str]) -> str:
    return hashlib.sha256("|".join(node_ids).encode()).hexdigest()[:16]


def measure(graph: Graph, founders: list[Entity], lead: Entity, lead_partner: Entity | None,
            fund: Entity, confirmed: set[str] = frozenset()) -> tuple[float | None, list[TrustLoop]]:
    """Trust in 0-1 and up to three best loops for one candidate fund.

    Home is your company plus its founders; the lead partner is a separate source
    whose paths are lead loops. Targets are the fund itself and people with a
    current role there. A path has one edge (existing investor, or a direct
    personal tie) or two (via one common contact: a person or a previous company).
    The weakest edge sets a path's strength; a contact is discounted by its hub
    size (Adamic-Adar); contributions saturate. When the fund co-invested with your
    lead, the path closes home through the lead and its contribution is boosted.
    """
    home = [p.id for p in founders if p.id in graph.nodes]
    if graph.startup_id and graph.startup_id in graph.nodes:
        home.append(graph.startup_id)
    sources = [(node, False) for node in home]
    if lead_partner and lead_partner.id in graph.nodes and lead_partner.id not in home:
        sources.append((lead_partner.id, True))
    people = [pid for pid, e in graph.adj.get(fund.id, {}).items() if e.type == "person_at_fund"]
    targets = [fund.id, *people]
    has_path_data = any(graph.path_neighbours(t) for t in targets) and any(graph.path_neighbours(s) for s, _ in sources)
    if not has_path_data:
        return None, []

    closure_edge = graph.adj.get(fund.id, {}).get(lead.id)
    closure = closure_edge.strength if closure_edge and closure_edge.type == "co_invested" else 0.0
    startup_id = graph.startup_id

    def lead_prefix(source_id: str) -> list[TrustHop]:
        hops = [_hop(graph, startup_id, lead.id)] if startup_id in graph.adj.get(lead.id, {}) else []
        return hops + ([_hop(graph, lead.id, source_id)] if source_id in graph.adj.get(lead.id, {}) else [])

    def lead_suffix() -> list[TrustHop]:
        hops = [_hop(graph, fund.id, lead.id)]
        return hops + ([_hop(graph, lead.id, startup_id)] if startup_id in graph.adj.get(lead.id, {}) else [])

    excluded = {fund.id, lead.id, *(s for s, _ in sources), *targets}
    found: list[tuple[float, float, TrustLoop]] = []
    contacts: set[str] = set()
    for source_id, via_lead in sources:
        mine = graph.path_neighbours(source_id)
        for target_id in targets:
            theirs = graph.path_neighbours(target_id)
            to_fund = [] if target_id == fund.id else [_hop(graph, target_id, fund.id)]
            paths = []
            if target_id in mine and mine[target_id].type != "person_at_fund":
                paths.append((None, [source_id, target_id]))
            for contact_id in sorted(set(mine) & set(theirs) - excluded):
                paths.append((contact_id, [source_id, contact_id, target_id]))
            for contact_id, nodes in paths:
                core = [_hop(graph, a, b) for a, b in zip(nodes, nodes[1:])] + to_fund
                strength = min(h.strength for h in core)  # weakest link of the home-to-candidate path
                if strength <= 0:
                    continue
                discount = 1.0 if contact_id is None else math.log(4) / math.log(2 + graph.degree(contact_id))
                closes = closure > 0 and not via_lead
                loop_id = _loop_id(nodes + [fund.id])
                is_confirmed = loop_id in confirmed
                contribution = strength * discount
                contribution *= (LEAD_LOOP_BONUS if via_lead else 1) * (CONFIRMED_LOOP_BONUS if is_confirmed else 1)
                contribution *= (1 + LEAD_CLOSURE_BONUS * closure) if closes else 1
                first = core[0]
                if via_lead:
                    kind = "lead_loop"
                elif first.edge_type == "invested_in" and contact_id is None:
                    kind = "existing_investor"
                elif contact_id is not None and graph.adj[source_id][contact_id].type == "founded":
                    kind = "previous_company"
                elif contact_id is None:
                    kind = "known"
                else:
                    kind = "one_contact"
                    contacts.add(contact_id)
                hops = (lead_prefix(source_id) if via_lead else []) + core + (lead_suffix() if closes else [])
                contact = graph.nodes[contact_id] if contact_id else (None if kind == "existing_investor" else graph.nodes[target_id])
                label = ("Identified funding event; investor relationship confirmed by Cala" if kind == "existing_investor"
                         else "Public professional overlap; personal relationship unconfirmed")
                found.append((contribution, strength, TrustLoop(
                    id=loop_id, kind=kind, hops=hops, strength=round(strength, 4), confirmed=is_confirmed,
                    contact=contact, closes_through_lead=closes or via_lead, label=label,
                )))
    if not found:
        return 0.0, []
    # Sum contributions per contact rather than counting loops, so the number of
    # four-loops (quadratic in common contacts) cannot inflate trust.
    best: dict[str, float] = {}
    for contribution, _, loop in found:
        key = loop.contact.id if loop.contact else loop.id
        best[key] = max(best.get(key, 0.0), contribution)
    value = 1 - math.exp(-sum(best.values()))
    if len(contacts) >= 2:
        for _, _, loop in found:
            if loop.kind == "one_contact":
                loop.kind = "multi_contact"
    ranked = sorted(found, key=lambda item: (-item[0], -item[1], item[2].id))
    loops, seen = [], set()
    for _, _, loop in ranked:
        if loop.id not in seen:
            seen.add(loop.id)
            loops.append(loop)
        if len(loops) == MAX_LOOPS:
            break
    return round(min(1.0, max(0.0, value)), 4), loops
