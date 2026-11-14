"""Ranking and recommended action from the single rule set."""
from datetime import date, datetime, timezone

from app import ranker, rules
from app.models import CandidateCard, Entity, FundingRound, InvestorProfile, Source, TrustHop, TrustLoop

SRC = Source(url="https://synthetic.example.test/x", retrieved_at=datetime(2026, 11, 14, tzinfo=timezone.utc))


def card(cid, met=(), unchecked=(), latest=date(2026, 1, 1), loop=False, origins=("lead_network",)):
    investor = Entity(id=cid, name=cid.upper(), sources=[SRC])
    deals = [FundingRound(id=f"{cid}-r", company=Entity(id="co", name="co"), event_date=latest, sources=[SRC])]
    loops = [TrustLoop(id=f"l-{cid}", kind="known", contact=Entity(id="p", name="P"), strength=.5,
                       hops=[TrustHop(from_id="f", from_name="F", to_id="p", to_name="P", edge_type="worked_together",
                                      strength=.5, sources=[SRC])])] if loop else []
    c = CandidateCard(investor=investor, fit_score=0, profile_completeness=0, explanation="", shared_round_count=1,
                      shared_company_count=1, shared_deals=deals, recent_rounds_12m=None, most_recent_investment=None,
                      components=[], considerations=[], statuses=[], profile=InvestorProfile(investor=investor),
                      sources=[SRC], action="introduction", draft="", trust=.5 if loop else None, loops=loops,
                      origins=list(origins))
    results = [rules.result(r.id, r.id in met, "x", [SRC]) for r in rules.RULES if r.id not in unchecked]
    results += [rules.result(rid, None, "x") for rid in unchecked]
    return rules.apply(c, results)


def ids(cards):
    return [c.investor.id for c in cards]


def test_score_is_rules_met_and_unchecked_rules_score_nothing():
    c = card("a", met={"lead_coinvestor", "stage_match"}, unchecked={"sector_match"})
    assert c.fit_score == 2
    assert [x.name for x in c.components] == [r.id for r in rules.RULES]


def test_rank_by_rules_met_then_connection_then_recency_then_name():
    more = card("more", met={"lead_coinvestor", "stage_match", "country_match"})
    connected = card("conn", met={"lead_coinvestor", "repeat_partner"})
    fit = card("fit", met={"stage_match", "country_match"})
    older = card("old", met={"lead_coinvestor", "repeat_partner"}, latest=date(2024, 1, 1))
    assert ids(ranker.rank([fit, older, more, connected])) == ["more", "conn", "old", "fit"]


def test_action_ladder():
    assert ranker.recommended_action(card("a", met={"your_investor"}, origins=("existing_investor",)))[0] == "reach_out_directly"
    via = ranker.recommended_action(card("b", met={"warm_path", "lead_coinvestor"}, loop=True))
    assert via[0] == "reach_out_via_contact" and via[1].id == "p"
    assert ranker.recommended_action(card("c", met={"lead_coinvestor"}))[0] == "ask_lead_intro"
    assert ranker.recommended_action(card("d", met={"stage_match"})) == ("skip", None)
