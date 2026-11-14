"""Strategy B trust loops and H partner departure, on synthetic roles only."""
from datetime import date, datetime, timezone

from app import trust
from app.models import Entity, FundingRound, Participation, Role, Source

AS_OF = date(2026, 11, 14)
SRC = Source(url="https://synthetic.example.test/role", retrieved_at=datetime(2026, 11, 14, tzinfo=timezone.utc))


def person(pid):
    return Entity(id=pid, name=pid.title(), entity_type="Person", sources=[SRC])


def org(oid, entity_type="Company"):
    return Entity(id=oid, name=oid.title(), entity_type=entity_type, sources=[SRC])


def role(pid, oid, since, until=None, kind="works_at", current=False, entity_type="Company"):
    return Role(person=person(pid), org=org(oid, entity_type), kind=kind, valid_since=since,
                valid_until=until, current=current, sources=[SRC])


LEAD = org("lead", "Organization")
FUND = org("fund", "Organization")
STARTUP = org("startup")


def shared(when=date(2025, 6, 1)):
    return [FundingRound(id="r1", company=org("portfolio"), event_date=when, sources=[SRC],
                         participants=[Participation(investor=LEAD, is_lead=True), Participation(investor=FUND, is_lead=False)])]


def run(roles, founders=("founder",), headcounts=None, lead_partner=None, confirmed=()):
    founder_entities = [person(f) for f in founders]
    graph, departures = trust.build_graph(STARTUP, founder_entities, LEAD, lead_partner,
                                          [trust.Candidate(FUND, shared())], roles, headcounts or {}, AS_OF)
    return trust.measure(graph, founder_entities, LEAD, lead_partner, FUND, set(confirmed)), graph, departures


def test_non_overlapping_dates_create_no_worked_together_edge():
    a = role("founder", "acme", date(2010, 1, 1), date(2012, 1, 1))
    b = role("partner", "acme", date(2013, 1, 1), date(2015, 1, 1))
    assert trust.overlap_edge(a, b, AS_OF, 30) is None
    undated = role("partner", "acme", None, date(2015, 1, 1))
    assert trust.overlap_edge(a, undated, AS_OF, 30) is None


def test_direct_overlap_is_a_known_loop_with_sources_and_dates():
    roles = [role("founder", "acme", date(2015, 1, 1), date(2019, 1, 1)),
             role("partner", "acme", date(2016, 1, 1), date(2020, 1, 1)),
             role("partner", "fund", date(2020, 2, 1), current=True, entity_type="Organization")]
    (value, loops), _, _ = run(roles, headcounts={"acme": 25})
    assert 0 < value < 1
    assert loops[0].kind == "known" and loops[0].contact.id == "partner"
    hop = next(h for h in loops[0].hops if h.edge_type == "worked_together")
    assert hop.org.id == "acme" and hop.since == date(2016, 1, 1) and hop.until == date(2019, 1, 1)
    assert hop.sources and not loops[0].confirmed


def test_hub_discount_and_size_factor_lower_mega_employer_contacts():
    def common(headcount, extra_neighbours):
        roles = [role("founder", "co", date(2015, 1, 1), date(2019, 1, 1)),
                 role("contact", "co", date(2015, 1, 1), date(2019, 1, 1)),
                 role("contact", "co2", date(2018, 1, 1), date(2021, 1, 1)),
                 role("partner", "co2", date(2018, 1, 1), date(2021, 1, 1)),
                 role("partner", "fund", date(2021, 2, 1), current=True, entity_type="Organization")]
        roles += [role(f"n{i}", "co2", date(2018, 1, 1), date(2021, 1, 1)) for i in range(extra_neighbours)]
        (value, loops), _, _ = run(roles, headcounts={"co": headcount, "co2": headcount})
        assert loops and loops[0].kind == "one_contact" and loops[0].contact.id == "contact"
        return value
    assert common(20, 0) > common(100_000, 0)
    assert common(20, 0) > common(20, 30)


def test_weakest_link_and_saturation():
    strong = role("contact", "co2", date(2014, 1, 1), date(2024, 1, 1))
    roles = [role("founder", "co", date(2014, 1, 1), date(2024, 1, 1)),
             role("contact", "co", date(2014, 1, 1), date(2024, 1, 1)),
             strong, role("partner", "co2", date(2023, 6, 1), date(2023, 7, 1)),
             role("partner", "fund", date(2024, 2, 1), current=True, entity_type="Organization")]
    (value, loops), graph, _ = run(roles, headcounts={"co": 5, "co2": 5})
    edges = [h.strength for h in loops[0].hops]
    assert loops[0].strength == min(edges)
    assert loops[0].strength < graph.adj["founder"]["contact"].strength
    # Many independent contacts saturate below 1 and never exceed it.
    many = list(roles)
    for i in range(12):
        many += [role(f"c{i}", "co", date(2014, 1, 1), date(2024, 1, 1)),
                 role(f"c{i}", "co2", date(2014, 1, 1), date(2024, 1, 1)),
                 role("partner", "co2", date(2014, 1, 1), date(2024, 1, 1))]
    (more, loops_more), _, _ = run(many, headcounts={"co": 5, "co2": 5})
    assert value < more < 1
    assert len(loops_more) == trust.MAX_LOOPS and {l.kind for l in loops_more} <= {"multi_contact", "known"}


def test_lead_partner_loop_gets_bonus_and_confirmed_loops_rank_higher():
    partner_lead = person("lp")
    roles = [role("lp", "co", date(2015, 1, 1), date(2019, 1, 1)),
             role("partner", "co", date(2015, 1, 1), date(2019, 1, 1)),
             role("partner", "fund", date(2020, 1, 1), current=True, entity_type="Organization")]
    (via_lead, loops), _, _ = run(roles, founders=(), lead_partner=partner_lead, headcounts={"co": 10})
    assert loops[0].kind == "lead_loop"
    assert loops[0].hops[0].edge_type == "led"
    # Same overlap but from the founder: no lead bonus.
    founder_roles = [r.model_copy(update={"person": person("founder")}) if r.person.id == "lp" else r for r in roles]
    (direct, _), _, _ = run(founder_roles, headcounts={"co": 10})
    assert via_lead > direct
    (confirmed, confirmed_loops), _, _ = run(founder_roles, headcounts={"co": 10},
                                             confirmed=[l.id for l in run(founder_roles, headcounts={"co": 10})[0][1]])
    assert confirmed > direct and confirmed_loops[0].confirmed


def test_no_people_data_leaves_trust_unknown():
    (value, loops), _, _ = run([])
    assert value is None and loops == []


def invested(fund, company, when=date(2024, 5, 1), rid=None):
    return FundingRound(id=rid or f"{fund.id}-{company.id}", company=company, event_date=when, sources=[SRC],
                        participants=[Participation(investor=fund, is_lead=False)])


def test_existing_investor_triangle_closes_through_the_lead():
    founders = [person("founder")]
    graph, _ = trust.build_graph(STARTUP, founders, LEAD, None,
                                 [trust.Candidate(FUND, shared(), invested=[invested(FUND, STARTUP)])], [], {}, AS_OF)
    value, loops = trust.measure(graph, founders, LEAD, None, FUND)
    loop = loops[0]
    assert loop.kind == "existing_investor" and loop.contact is None and loop.closes_through_lead
    # Triangle: startup <- invested - fund - co_invested - lead - led - startup.
    assert [h.edge_type for h in loop.hops] == ["invested_in", "co_invested", "led"]
    assert value > 0.5


def test_previous_company_four_edge_loop_and_lead_closure_bonus():
    founders = [person("founder")]
    previous = org("vilynx")
    roles = [role("founder", "vilynx", date(2012, 1, 1), date(2018, 1, 1), kind="founded")]

    def run_with(shared_rounds):
        graph, _ = trust.build_graph(STARTUP, founders, LEAD, None,
                                     [trust.Candidate(FUND, shared_rounds, invested=[invested(FUND, previous, date(2015, 1, 1))])],
                                     roles, {}, AS_OF)
        return trust.measure(graph, founders, LEAD, None, FUND)

    closed_value, closed = run_with(shared())
    loop = closed[0]
    assert loop.kind == "previous_company" and loop.contact.id == "vilynx"
    # founder - founded - Vilynx - invested_in - fund - co_invested - lead - led - home: four edges with home collapsed.
    assert [h.edge_type for h in loop.hops] == ["founded", "invested_in", "co_invested", "led"]
    open_value, open_loops = run_with([])
    assert not open_loops[0].closes_through_lead and closed_value > open_value
