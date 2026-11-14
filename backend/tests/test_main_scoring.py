"""Golden parity with main 1fed306, plus typed evidence and allocation integration."""
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from app import main_scoring
from app.models import Entity, FounderInput, FundingRound, InvestorProfile, Mandate, Participation, Source, StatusEntry
from app.scoring import build_allocation, combined_draft, exclusion_for, normalize_stage, score_candidate


GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "main-scoring-1fed306.json").read_text(encoding="utf-8"))
AS_OF = date.fromisoformat(GOLDEN["as_of"])
SOURCE = Source(url="https://synthetic.example.test/funding", retrieved_at=datetime(2026, 11, 14, tzinfo=timezone.utc))
LEAD = Entity(id="lead", name="Lead", kind="vc", sources=[SOURCE])
INVESTOR = Entity(id="candidate", name="Candidate", kind="vc", sources=[SOURCE])
SIGNALS = {"co_investment": "coinv", "recent_activity": "activity", "stage_fit": "stage",
           "sector_fit": "sector", "non_lead_participation": "follow", "geography_fit": "geo"}


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda case: case["name"])
def test_ported_engine_matches_unmodified_main_golden_results(case):
    # These expected values were produced once by the original main engine,
    # not recomputed by the implementation under test.
    result = main_scoring.score_candidate(deepcopy(case["candidate"]), deepcopy(case["ctx"]), as_of=AS_OF)
    actual = {key: result[key] for key in case["expected"]}
    assert actual == case["expected"]


def typed_case(case):
    context = case["ctx"]
    profile_input = FounderInput.model_validate({
        "startup": {"company_name": "Founder company", "sector": ", ".join(context["sector_terms"]),
                    "country": context["country"], "known_competitors": context["competitors"],
                    "product_description": "", "target_customer": "", "problem_solved": ""},
        "round": {"stage": "seed", "target_eur": str(context["open_eur"]), "firm_commitments_eur": "0",
                  "lead_status": "unconfirmed"}, "lead_name": LEAD.name,
    })
    events = []
    for index, row in enumerate(case["candidate"]["rounds"]):
        events.append(FundingRound(
            id=f"event-{index}", company=Entity(id=f"company-{index}", name=row["company"], kind="company"),
            event_date=date.fromisoformat(row["date"]), stage=row.get("stage"), sector=row.get("sector"),
            company_country=row.get("country"), total_amount_eur=row.get("amount"),
            participants=[Participation(investor=LEAD, is_lead=True),
                          Participation(investor=INVESTOR, is_lead=row.get("lead")),
                          Participation(investor=Entity(id="other", name="Other", kind="vc"))],
            sources=[SOURCE],
        ))
    shared = [event for event in events if any(event.company.name == item["company"]
              and event.event_date.isoformat() == item["date"] for item in case["candidate"]["shared"])]
    return profile_input, InvestorProfile(investor=INVESTOR, rounds=events), shared


COMPATIBLE = [case for case in GOLDEN["cases"] if case["name"] not in (
    "three_shared_followons_cap_connection", "no_profile_history_unknowns_zero")]


@pytest.mark.parametrize("case", COMPATIBLE, ids=lambda case: case["name"])
def test_typed_adapter_preserves_main_scores_ratios_flags_and_ticket_estimate(case):
    founder, profile, shared = typed_case(case)
    card = score_candidate(profile, shared, founder, LEAD, AS_OF)
    expected = case["expected"]
    assert card.fit_score == expected["score"]
    assert card.review_status == expected["status"]
    assert card.recent_rounds_12m == expected["rounds_12m"]
    assert {(flag.kind, flag.severity) for flag in card.flags} == {
        (flag["kind"], flag["severity"]) for flag in expected["flags"]}
    for component in card.components:
        value = expected["signals"].get(SIGNALS[component.name], {}).get("v")
        assert component.value == pytest.approx(value if value is not None else 0)
    assert card.estimated_ticket_eur == Decimal(str(expected["ticket"]))
    assert "Equal-share heuristic" in card.ticket_estimate_basis
    assert "actual investor checks are unknown" in card.ticket_estimate_basis
    assert profile.ticket_criteria == []


def base_card():
    founder, profile, shared = typed_case(GOLDEN["cases"][0])
    return founder, score_candidate(profile, shared, founder, LEAD, AS_OF)


def test_main_allocation_is_greedy_by_rank_with_exact_eur_residual_and_percentages():
    _, first = base_card()
    first = first.model_copy(update={"estimated_ticket_eur": Decimal("1.00"), "fit_score": 90})
    second = first.model_copy(update={"investor": INVESTOR.model_copy(update={"id": "second", "name": "Second"}), "fit_score": 80})
    result = build_allocation([second, first], Decimal("3.01"))
    assert [row.investor_id for row in result] == ["candidate", "second", None]
    assert [row.amount_eur for row in result] == [Decimal("1.00"), Decimal("1.00"), Decimal("1.01")]
    assert sum(row.amount_eur for row in result) == Decimal("3.01")
    assert sum(Decimal(str(row.percentage)) for row in result) == Decimal("100")
    assert result[-1].name == "Still open"


def test_allocation_caps_ticket_to_gap_and_excludes_flagged_or_unknown_tickets():
    _, card = base_card()
    capped = build_allocation([card], Decimal("500000"))
    assert len(capped) == 1 and capped[0].amount_eur == Decimal("500000")
    assert capped[0].percentage == 100
    for unusable in (card.model_copy(update={"review_status": "flagged"}),
                     card.model_copy(update={"estimated_ticket_eur": None})):
        result = build_allocation([unusable], Decimal("500000"))
        assert len(result) == 1 and result[0].investor_id is None
        assert result[0].amount_eur == Decimal("500000") and result[0].percentage == 100
    assert build_allocation([card], Decimal("0")) == []


def test_allocation_percentage_rounding_cannot_make_a_tiny_remainder_negative():
    _, base = base_card()
    cards = [base.model_copy(update={"investor": INVESTOR.model_copy(update={"id": str(index), "name": f"Investor {index}"}),
                                    "estimated_ticket_eur": amount, "fit_score": 90 - index})
             for index, amount in enumerate([Decimal("3333335000"), Decimal("3333335000"), Decimal("3333329999.99")])]
    rows = build_allocation(cards, Decimal("10000000000"))
    assert rows[-1].amount_eur == Decimal("0.01")
    assert all(0 <= row.percentage <= 100 for row in rows)
    assert sum(Decimal(str(row.percentage)) for row in rows) == 100
    assert sum(row.amount_eur for row in rows) == Decimal("10000000000")


def test_unknown_currency_or_missing_round_amount_does_not_create_a_ticket():
    founder, profile, shared = typed_case(GOLDEN["cases"][0])
    # The native adapter only fills total_amount_eur for actual EUR amounts.
    profile.rounds[0].total_amount_eur = None
    card = score_candidate(profile, shared, founder, LEAD, AS_OF)
    assert card.estimated_ticket_eur is None and card.ticket_estimate_basis is None
    assert build_allocation([card], founder.round.remaining_eur)[0].investor_id is None


@pytest.mark.parametrize("stage,expected", [("Pre-seed", "pre_seed"), ("pre_seed", "pre_seed"),
                                          ("Seed", "seed"), ("series_a", "series_a"), ("Series B", "series_b")])
def test_main_stage_normalization_accepts_canonical_api_values(stage, expected):
    assert normalize_stage(stage) == expected


def test_unconfirmed_lead_drafts_and_main_explicit_exclusions_are_preserved():
    founder, card = base_card()
    assert "Before a lead commitment is confirmed" in card.draft
    assert "Our product:" not in card.draft
    assert "Our sector:" in card.draft
    assert "has not been confirmed" in combined_draft([card], founder, LEAD)
    assert combined_draft([card.model_copy(update={"review_status": "flagged"})], founder, LEAD) == ""
    founder.excluded_investors = [card.investor.name]
    assert exclusion_for(card.investor, founder, LEAD).code == "already_in_excluded"
    founder.investor_statuses = [StatusEntry(investor_id=card.investor.id, statuses=["do_not_contact"])]
    assert exclusion_for(card.investor, founder, LEAD).code == "do_not_contact"


def test_main_optional_founder_country_cannot_establish_a_mandate_mismatch():
    founder, profile, _ = typed_case(GOLDEN["cases"][0])
    founder.startup.country = ""
    profile.mandates = [Mandate(allowed_countries=["Finland"], currently_applicable=True, sources=[SOURCE])]
    assert exclusion_for(profile.investor, founder, LEAD, profile) is None
