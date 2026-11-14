from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models import (Assessment, Entity, FounderInput, FundingRound, InvestmentObservation, InvestorProfile,
                        InvestorStatus, Mandate, Participation, Source, StatusEntry, TicketCriterion)
from app.scoring import (combined_draft, deduplicate_rounds, exclusion_for, months_before, rank_candidates,
                         score_candidate, statuses_for)


TODAY = date(2026, 11, 14)
SOURCE = Source(url="https://cala.example/round/1", retrieved_at=datetime(2026, 11, 14, tzinfo=timezone.utc))
LEAD = Entity(id="lead", name="Lead Capital", kind="vc", sources=[SOURCE])
INVESTOR = Entity(id="candidate", name="Candidate Capital", kind="vc", country="Finland", sources=[SOURCE])


def founder(**updates):
    data = dict(startup=dict(company_name="Manual Startup", product_description="Business payments",
                            sector="Fintech", target_customer="SMEs", problem_solved="Slow payments", country="Finland"),
                round=dict(stage="seed", target_eur="2000000", firm_commitments_eur="1500000", lead_status="signed"),
                lead_name="Lead Capital")
    data.update(updates)
    return FounderInput.model_validate(data)


def funding(id="round1", company="company1", when=TODAY, stage="seed", role=False, country="Finland", sources=None):
    return FundingRound(id=id, company=Entity(id=company, name=company, kind="company"),
                        event_date=when, stage=stage, company_country=country,
                        participants=[Participation(investor=LEAD, is_lead=True),
                                      Participation(investor=INVESTOR, is_lead=role)],
                        sources=[SOURCE] if sources is None else sources)


def component(card, name):
    return next(item for item in card.components if item.name == name)


def test_duplicate_ids_deduplicate_but_main_scoring_counts_distinct_followons():
    first = funding()
    followon = funding(id="round2", when=date(2026, 10, 14))
    card = score_candidate(InvestorProfile(investor=INVESTOR, rounds=[first, first, followon]),
                           [first, first, followon], founder(), LEAD, TODAY)
    assert card.shared_round_count == 2
    assert card.shared_company_count == 1
    assert component(card, "co_investment").value == 1
    assert component(card, "recent_activity").value == .5


def test_shared_company_different_round_is_not_a_connection():
    lead_round = funding()
    lead_round.participants = [Participation(investor=LEAD)]
    investor_round = funding(id="other")
    investor_round.participants = [Participation(investor=INVESTOR)]
    with pytest.raises(ValueError, match="shared with the confirmed lead"):
        score_candidate(InvestorProfile(investor=INVESTOR, rounds=[investor_round]),
                        [lead_round, investor_round], founder(), LEAD, TODAY)


def test_unsourced_or_future_shared_events_cannot_support_a_card():
    for event in [funding(sources=[]), funding(when=date(2027, 1, 1))]:
        with pytest.raises(ValueError):
            score_candidate(InvestorProfile(investor=INVESTOR), [event], founder(), LEAD, TODAY)


def test_unknown_fields_contribute_zero_under_main_scoring_with_separate_completeness():
    event = funding(stage=None, role=None, country=None)
    card = score_candidate(InvestorProfile(investor=INVESTOR, rounds=[event]), [event], founder(), LEAD, TODAY)
    for name in ["stage_fit", "sector_fit", "non_lead_participation", "geography_fit"]:
        value = component(card, name)
        assert not value.known
        assert value.value == 0
        assert value.denominator is None
    assert card.profile_completeness == .55
    assert card.fit_score == 22  # main:35*.5 +20*.25, Python round(22.5).


def test_main_sector_keywords_ignore_llm_numeric_assessments_and_keep_explicit_roles():
    event = funding()
    profile = InvestorProfile(investor=INVESTOR, rounds=[event, funding(id="followon")],
        assessments=[Assessment(company_id="company1", sector_fit="adjacent", rationale="Adjacent buyer", sources=[SOURCE])])
    card = score_candidate(profile, [event], founder(), LEAD, TODAY)
    assert component(card, "sector_fit").value == 0
    assert not component(card, "sector_fit").known
    assert component(card, "non_lead_participation").value == 1
    assert card.profile_completeness == .85
    event.sector = "Fintech business payments"
    profile.rounds[1].sector = event.sector
    sourced_card = score_candidate(profile, [event], founder(), LEAD, TODAY)
    assert component(sourced_card, "sector_fit").value == 1
    assert component(sourced_card, "sector_fit").denominator == 2
    assert sourced_card.profile_completeness == 1


def test_status_precedence_and_identity_disambiguation():
    context = founder(investor_statuses=[dict(investor_name="candidate capital", statuses=["existing_shareholder", "declined"]),
                                         dict(investor_id="candidate", statuses=["firm_committed", "do_not_contact"]),
                                         dict(investor_id="different", investor_name="Candidate Capital", statuses=["in_conversation"])])
    assert InvestorStatus.IN_CONVERSATION not in statuses_for(INVESTOR, context)
    assert exclusion_for(INVESTOR, context, LEAD).code == "do_not_contact"
    context.investor_statuses = [StatusEntry(investor_id="candidate", statuses=[InvestorStatus.EXISTING_SHAREHOLDER])]
    assert exclusion_for(INVESTOR, context, LEAD) is None


def test_current_published_minimum_excludes_but_historical_typical_and_unclear_mandate_do_not():
    profile = InvestorProfile(investor=INVESTOR,
        ticket_criteria=[TicketCriterion(kind="historical_typical", minimum_eur=Decimal("1000000"), sources=[SOURCE])],
        mandates=[Mandate(allowed_countries=["Germany"], currently_applicable=False, sources=[SOURCE])])
    assert exclusion_for(INVESTOR, founder(), LEAD, profile) is None
    event = funding()
    card = score_candidate(profile, [event], founder(), LEAD, TODAY)
    assert {item.code for item in card.considerations} >= {"participation_size", "historical_mandate"}
    profile.ticket_criteria = [TicketCriterion(kind="published_minimum_initial", minimum_eur=Decimal("1000000"),
                                             currently_applicable=True, sources=[SOURCE])]
    assert exclusion_for(INVESTOR, founder(), LEAD, profile).code == "minimum_initial_ticket"
    profile.ticket_criteria[0].sources = []
    assert exclusion_for(INVESTOR, founder(), LEAD, profile) is None


def test_main_inactivity_flags_and_ratio_minimums_preserve_separate_overlap_context():
    event = funding(when=date(2025, 1, 1), stage="Series A", role=True)
    profile = InvestorProfile(investor=INVESTOR, rounds=[event], activity_window_complete=True, sources=[SOURCE],
        assessments=[Assessment(company_id="company1", sector_fit="same", overlap="direct_substitute",
                                rationale="Same product, buyer and problem from retrieved descriptions.", sources=[SOURCE])])
    card = score_candidate(profile, [event], founder(), LEAD, TODAY)
    assert exclusion_for(INVESTOR, founder(), LEAD, profile) is None
    assert {item.code for item in card.considerations} >= {"low_recent_activity", "portfolio_overlap"}
    assert not {"limited_stage_history", "usually_leads"} & {item.code for item in card.considerations}
    assert card.review_status == "flagged"
    assert {flag.kind for flag in card.flags} == {"inactive"}
    assert all(item.sources for item in card.considerations if item.basis != "Founder supplied")


def test_conditional_lead_followup_wording_and_combined_limit():
    context = founder(round=dict(stage="seed", target_eur="2000000", firm_commitments_eur="1000000",
                                conditional_interest_eur="500000", lead_ticket_eur="500000", lead_status="conditional"),
                      investor_statuses=[dict(investor_id="candidate", statuses=["already_approached"])])
    event = funding()
    card = score_candidate(InvestorProfile(investor=INVESTOR, rounds=[event]), [event], context, LEAD, TODAY)
    assert card.action == "follow_up"
    assert "provisional" in card.draft
    assert "existing conversation" in card.draft
    assert "EUR 1,000,000 remaining" in card.draft
    assert "could you introduce us" not in card.draft
    many = [card.model_copy(update={"investor": INVESTOR.model_copy(update={"id": str(i), "name": f"Candidate {i}"})}) for i in range(5)]
    draft = combined_draft(many, context, LEAD)
    assert draft.count("- Candidate") == 3


def test_stable_tiebreaks_country_mapping_and_calendar_boundaries():
    assert months_before(date(2024, 3, 31), 1) == date(2024, 2, 29)
    event = funding(country="SE")
    card = score_candidate(InvestorProfile(investor=INVESTOR, rounds=[event]), [event], founder(), LEAD, TODAY)
    assert component(card, "geography_fit").value == 0
    a = card.model_copy(update={"investor": INVESTOR.model_copy(update={"id": "a", "name": "Alpha"})})
    b = card.model_copy(update={"investor": INVESTOR.model_copy(update={"id": "b", "name": "Beta"})})
    assert [c.investor.id for c in rank_candidates([b, a])] == ["a", "b"]
    snapshot = InvestorProfile(investor=INVESTOR, rounds=[event])
    assert score_candidate(snapshot, [event], founder(), LEAD, TODAY).model_dump() == score_candidate(snapshot, [event], founder(), LEAD, TODAY).model_dump()


def test_round_total_never_becomes_investor_check():
    event = funding()
    event.total_amount_eur = Decimal("100000000")
    profile = InvestorProfile(investor=INVESTOR, rounds=[event])
    card = score_candidate(profile, [event], founder(), LEAD, TODAY)
    assert not card.profile.ticket_criteria
    assert "participation_size" not in {c.code for c in card.considerations}


def test_contradictory_duplicate_roles_are_unknown_and_do_not_depend_on_order():
    non_lead = funding()
    lead = funding(role=True)
    for records in [[non_lead, lead], [lead, non_lead]]:
        profile = InvestorProfile(investor=INVESTOR, rounds=records)
        card = score_candidate(profile, records, founder(), LEAD, TODAY)
        assert card.shared_round_count == 1
        assert not component(card, "non_lead_participation").known
        assert "usually_leads" not in {item.code for item in card.considerations}
    repeated_participant = funding()
    repeated_participant.participants.append(Participation(investor=INVESTOR, is_lead=True))
    card = score_candidate(InvestorProfile(investor=INVESTOR, rounds=[repeated_participant]),
                           [repeated_participant], founder(), LEAD, TODAY)
    assert not component(card, "non_lead_participation").known


def test_conflicting_event_identity_cannot_support_a_card():
    for conflicting in [funding(company="other_company"), funding(when=date(2025, 1, 1))]:
        with pytest.raises(ValueError):
            score_candidate(InvestorProfile(investor=INVESTOR), [funding(), conflicting], founder(), LEAD, TODAY)


def test_existing_shareholder_receives_a_follow_on_request():
    context = founder(investor_statuses=[dict(investor_id="candidate", statuses=["existing_shareholder"])])
    event = funding()
    card = score_candidate(InvestorProfile(investor=INVESTOR, rounds=[event]), [event], context, LEAD, TODAY)
    assert card.action == "follow_up"
    assert "follow-on request to our existing shareholder" in card.draft
    assert "follow-on request to our existing shareholder" in combined_draft([card], context, LEAD)
    assert exclusion_for(INVESTOR, context, LEAD) is None


def test_unsourced_public_filter_facts_do_not_exclude_and_are_unverified():
    unknown = INVESTOR.model_copy(update={"kind": "angel", "country": "Germany", "sources": []})
    context = founder(preferences=dict(include_angels=False, investor_countries=["Finland"]))
    assert exclusion_for(unknown, context, LEAD) is None
    event = funding()
    card = score_candidate(InvestorProfile(investor=unknown, rounds=[event]), [event], context, LEAD, TODAY)
    assert {item.code for item in card.considerations} >= {"country_unknown", "investor_type_unknown"}
    assert exclusion_for(unknown.model_copy(update={"sources": [SOURCE]}), context, LEAD).code == "investor_type_filter"
    plain_lead = LEAD.model_copy(update={"sources": [], "related_entity_ids": [unknown.id]})
    assert exclusion_for(unknown, founder(), plain_lead) is None
    assert exclusion_for(unknown, founder(), plain_lead.model_copy(update={"sources": [SOURCE]})).code == "lead_alias"


def test_canonical_dedup_unions_evidence_and_conflicting_roles():
    first, second = funding(), funding(role=True)
    second.sources = [Source(url="https://cala.example/second-evidence", retrieved_at=SOURCE.retrieved_at)]
    merged = deduplicate_rounds([first, second])
    assert len(merged) == 1
    assert len(merged[0].sources) == 2
    assert next(p for p in merged[0].participants if p.investor.id == INVESTOR.id).is_lead is None


def test_enriched_country_and_category_filters_require_field_specific_sources():
    unrelated = Source(url="https://cala.example/investor/website", retrieved_at=SOURCE.retrieved_at)
    investor = INVESTOR.model_copy(update={"country": "Germany", "kind": "angel",
                                          "entity_type": "Person", "metadata_status": "retrieved",
                                          "sources": [unrelated]})
    context = founder(preferences=dict(include_angels=False, investor_countries=["Finland"]))
    assert exclusion_for(investor, context, LEAD) is None
    card = score_candidate(InvestorProfile(investor=investor), [funding()], context, LEAD, TODAY)
    assert {item.code for item in card.considerations} >= {"country_unknown", "investor_type_unknown"}

    country_known = investor.model_copy(update={"country_sources": [SOURCE], "country_basis": "headquarters_address"})
    excluded = exclusion_for(country_known, context, LEAD)
    assert excluded.code == "investor_country_filter"
    assert excluded.sources == [SOURCE]

    category_known = investor.model_copy(update={"kind_sources": [SOURCE], "kind_basis": "explicit_category"})
    excluded = exclusion_for(category_known, context, LEAD)
    assert excluded.code == "investor_type_filter"
    assert excluded.sources == [SOURCE]


def test_legal_company_type_does_not_confirm_an_investor_category():
    investor = INVESTOR.model_copy(update={"kind": "company", "entity_type": "Company"})
    context = founder(preferences=dict(include_corporate_vcs=False))
    card = score_candidate(InvestorProfile(investor=investor), [funding()], context, LEAD, TODAY)
    assert "investor_type_unknown" in {item.code for item in card.considerations}


@pytest.mark.parametrize("canonical,preference", [
    ("Korea, Republic of", "South Korea"), ("Viet Nam", "Vietnam"),
    ("Taiwan, Province of China", "TW"), ("Moldova, Republic of", "Moldova"),
])
def test_iso_metadata_country_names_match_filters_with_main_exact_country_geography(canonical, preference):
    investor = INVESTOR.model_copy(update={"country": canonical, "country_sources": [SOURCE],
                                          "metadata_status": "retrieved"})
    context = founder(preferences=dict(investor_countries=[preference]))
    assert exclusion_for(investor, context, LEAD) is None
    # ISO aliases align the field and filter, while main gives no regional bonus.
    context = founder(startup={**context.startup.model_dump(), "country": preference})
    card = score_candidate(InvestorProfile(investor=investor), [funding(country=canonical)], context, LEAD, TODAY)
    assert component(card, "geography_fit").value == 1
    regional_peer = "France" if preference == "Moldova" else "Japan"
    context = founder(startup={**context.startup.model_dump(), "country": regional_peer})
    card = score_candidate(InvestorProfile(investor=investor), [funding(country=canonical)], context, LEAD, TODAY)
    assert component(card, "geography_fit").value == 0


def test_enriched_portfolio_geography_uses_the_headquarters_fields_own_evidence():
    headquarters_source = Source(url="https://cala.example/company/headquarters", retrieved_at=SOURCE.retrieved_at)
    event = funding(country=None)
    event.company = event.company.model_copy(update={"country": "Finland", "metadata_status": "retrieved",
                                                    "country_basis": "headquarters_address",
                                                    "country_sources": [headquarters_source]})
    card = score_candidate(InvestorProfile(investor=INVESTOR), [event], founder(), LEAD, TODAY)
    assert component(card, "geography_fit").value == 1
    assert component(card, "geography_fit").sources == [headquarters_source]
    event.company = event.company.model_copy(update={"country_sources": [], "sources": [SOURCE]})
    card = score_candidate(InvestorProfile(investor=INVESTOR), [event], founder(), LEAD, TODAY)
    assert not component(card, "geography_fit").known


def test_independent_observations_supply_activity_stage_and_role_without_fake_companies():
    shared = funding(when=date(2023, 1, 1))
    independent_source = Source(url="https://cala.example/investor/candidate/activity", retrieved_at=SOURCE.retrieved_at)
    observations = [
        InvestmentObservation(id="independent1", event_date=TODAY, stage="seed", is_lead=False, sources=[independent_source]),
        InvestmentObservation(id="independent2", event_date=date(2026, 1, 1), stage="Series A", is_lead=True, sources=[independent_source]),
        InvestmentObservation(id="independent3", event_date=date(2025, 2, 1), stage="seed", is_lead=None, sources=[independent_source]),
    ]
    profile = InvestorProfile(investor=INVESTOR, observations=observations)
    card = score_candidate(profile, [shared], founder(), LEAD, TODAY)
    assert card.recent_rounds_12m == 2
    assert component(card, "recent_activity").value == .5
    assert component(card, "stage_fit").numerator == 2
    assert component(card, "stage_fit").denominator == 3
    assert component(card, "non_lead_participation").numerator == 1
    assert component(card, "non_lead_participation").denominator == 2
    assert component(card, "non_lead_participation").value == .5
    assert not component(card, "sector_fit").known
    assert not component(card, "geography_fit").known
    assert card.most_recent_investment == TODAY
    assert independent_source in card.sources
    assert component(card, "recent_activity").sources == [independent_source]
    assert card.shared_round_count == 1


def test_observation_duplicate_full_round_counts_once_and_preserves_role_conflict():
    shared = funding()
    observations = [InvestmentObservation(id=shared.id, event_date=TODAY, stage="seed", is_lead=True, sources=[SOURCE]),
                    InvestmentObservation(id="second", event_date=TODAY, stage="seed", is_lead=False, sources=[SOURCE])]
    card = score_candidate(InvestorProfile(investor=INVESTOR, rounds=[shared], observations=observations),
                           [shared], founder(), LEAD, TODAY)
    assert card.recent_rounds_12m == 2
    assert component(card, "stage_fit").denominator == 2
    # Shared full round says non-lead, observation says lead: that event's role is unknown.
    assert component(card, "non_lead_participation").numerator == 1
    assert component(card, "non_lead_participation").denominator == 1


def test_observation_windows_missing_fields_conflicts_and_sources_are_conservative():
    shared = funding(when=date(2023, 1, 1))
    items = [
        InvestmentObservation(id="conflict-date", event_date=TODAY, stage="seed", is_lead=False, sources=[SOURCE]),
        InvestmentObservation(id="conflict-date", event_date=date(2026, 1, 1), stage="seed", is_lead=False, sources=[SOURCE]),
        InvestmentObservation(id="conflict-stage", event_date=TODAY, stage="seed", is_lead=True, sources=[SOURCE]),
        InvestmentObservation(id="conflict-stage", event_date=TODAY, stage="Series A", is_lead=False, sources=[SOURCE]),
        InvestmentObservation(id="future", event_date=date(2027, 1, 1), stage="seed", is_lead=False, sources=[SOURCE]),
        InvestmentObservation(id="undated", sources=[SOURCE]),
        InvestmentObservation(id="unsourced", event_date=TODAY, stage="seed", is_lead=False, sources=[]),
        InvestmentObservation(id="too-old", event_date=date(2024, 1, 1), stage="seed", is_lead=False, sources=[SOURCE]),
    ]
    cards = [score_candidate(InvestorProfile(investor=INVESTOR, observations=records),
                             [shared], founder(), LEAD, TODAY) for records in [items, list(reversed(items))]]
    assert cards[0].components == cards[1].components
    assert cards[0].recent_rounds_12m == 1
    assert not component(cards[0], "stage_fit").known
    assert not component(cards[0], "non_lead_participation").known
    assert cards[0].most_recent_investment == TODAY
