"""Synthetic transcripts exercise the observed native Cala schema without keys."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.cala import CalaError
from app.cala_native import CalaNativeProvider, ToolPayload, _round
from app.runtime import BudgetExceeded


def source():
    return {"name": "Synthetic test evidence", "document": "https://synthetic.example.test/event", "date": "2026-01-01"}


def prop(value):
    return {"value": value, "sources": [source()]}


def participant(identifier, *, role="INVESTOR", is_lead=None):
    properties = {"role": role, "sources": [source()]}
    if is_lead is not None:
        properties["is_lead"] = is_lead
    return {"id": identifier, "name": identifier, "entity_type": "Company", "properties": properties}


def event(*, lead="lead", currency="EUR", precision=None):
    properties = {"id": prop("event"), "event_date": prop(datetime.now(timezone.utc).date().isoformat()),
                  "round_label": prop("SEED"), "transaction_value": prop(5_000_000),
                  "transaction_value_units": prop(currency)}
    if precision:
        properties["event_date__precision"] = prop(precision)
    return {"id": "event", "name": "Synthetic round", "properties": properties,
            "relationships": {"incoming": {"PARTICIPATES_IN_FUNDING_ROUND": [
                participant(lead, is_lead=True), participant("candidate"), participant("company", role="ISSUER"),
            ]}}}


def test_native_round_preserves_identity_roles_sources_and_total_only():
    funding = _round(event(), datetime.now(timezone.utc))
    assert funding.id == "event"
    assert funding.company.id == "company"
    assert funding.total_amount_eur == 5_000_000
    assert {item.investor.id: item.is_lead for item in funding.participants} == {"lead": True, "candidate": None}
    assert funding.sources[0].published_at.isoformat() == "2026-01-01"
    assert _round(event(currency="USD"), datetime.now(timezone.utc)).total_amount_eur is None
    assert _round(event(precision="YEAR"), datetime.now(timezone.utc)).event_date_precision == "year"


def test_native_round_without_identified_issuer_is_not_guessed():
    data = event()
    data["relationships"]["incoming"]["PARTICIPATES_IN_FUNDING_ROUND"] = [participant("lead"), participant("candidate")]
    assert _round(data, datetime.now(timezone.utc)) is None


def test_native_parser_preserves_conflicting_participant_role_observations():
    data = event()
    data["relationships"]["incoming"]["PARTICIPATES_IN_FUNDING_ROUND"].extend([
        participant("candidate", is_lead=True), participant("candidate", is_lead=False),
    ])
    funding = _round(data, datetime.now(timezone.utc))
    assert [item.is_lead for item in funding.participants if item.investor.id == "candidate"] == [None, True, False]


def transcript(*, actual_lead="lead", optional_company_failure=False):
    calls = []
    today = datetime.now(timezone.utc).date()

    async def call(tool, args):
        calls.append((tool, args))
        identifier = args.get("entity_id")
        if tool == "entity_search":
            data = {"entities": [{"id": "lead", "name": "Lead", "entity_type": "Organization"}]}
        elif tool == "entity_introspection":
            data = {"properties": ["id", "name", "description", "event_date", "round_label"],
                    "relationships": {"outgoing": ["PARTICIPATES_IN_FUNDING_ROUND"],
                                      "incoming": ["PARTICIPATES_IN_FUNDING_ROUND"]}}
        elif identifier == "event":
            data = event(lead=actual_lead)
        elif identifier == "company":
            if optional_company_failure:
                raise BudgetExceeded()
            data = {"id": "company", "name": "Company", "description": "Synthetic product description",
                    "properties": {"description": prop("Synthetic product description")}}
        else:
            data = {"id": identifier, "name": identifier, "properties": {"name": prop(identifier)},
                    "relationships": {"outgoing": {"PARTICIPATES_IN_FUNDING_ROUND": [{
                        "id": "event", "name": "Round", "entity_type": "FundingRound",
                        "properties": {"event_date": today.isoformat(), "role": "INVESTOR", "sources": [source()]},
                    }]}}}
        return ToolPayload(data=data, retrieved_at=datetime.now(timezone.utc))

    return call, calls


def test_native_calls_are_bound_to_actual_tool_budget_and_profiles_reuse_graph():
    call, calls = transcript()
    native = CalaNativeProvider().with_tool_caller(call)

    async def run():
        matches = await native.search_entities("Lead", "investor")
        graph = await native.lead_graph(matches[0].id, 60)
        profile = await native.investor_profile("candidate", 24)
        return graph, profile

    graph, profile = asyncio.run(run())
    assert len(graph.rounds) == 1
    assert graph.complete is False
    assert profile.investor.id == "candidate"
    assert profile.rounds[0].id == graph.rounds[0].id
    assert profile.rounds[0].company.description == "Synthetic product description"
    assert profile.activity_window_complete is False
    assert profile.observations[0].id == "event"
    assert profile.observations[0].is_lead is None
    assert len(calls) == 8  # Search1, lead2, event2, company1, profile2.
    assert {tool for tool, _ in calls} == {"entity_search", "entity_introspection", "entity_retrieval"}


def test_native_lead_must_be_in_exact_event_not_inferred_from_company():
    call, _ = transcript(actual_lead="different-lead")
    graph = asyncio.run(CalaNativeProvider().with_tool_caller(call).lead_graph("lead", 60))
    assert graph.rounds == []


def test_optional_company_enrichment_failure_preserves_verified_event():
    call, _ = transcript(optional_company_failure=True)
    graph = asyncio.run(CalaNativeProvider().with_tool_caller(call).lead_graph("lead", 60))
    assert len(graph.rounds) == 1
    assert graph.rounds[0].company.description is None


def test_native_profiler_preserves_conflicting_observations_for_snapshot_merge():
    base_call, _ = transcript()

    async def call(tool, args):
        response = await base_call(tool, args)
        if tool == "entity_retrieval" and args.get("entity_id") == "candidate":
            edges = response.data["relationships"]["outgoing"]["PARTICIPATES_IN_FUNDING_ROUND"]
            for role in (True, False):
                edges.append({**edges[0], "properties": {**edges[0]["properties"], "is_lead": role}})
        return response

    profile = asyncio.run(CalaNativeProvider().with_tool_caller(call).investor_profile("candidate", 24))
    assert [item.is_lead for item in profile.observations] == [None, True, False]


def test_native_metadata_projects_all_advertised_fields_and_only_available_metadata_edges():
    calls = []
    fields = ["id", "name", "headquarters_address", "registered_address", "legal_name", "lei", "website"]

    async def call(tool, args):
        calls.append((tool, args))
        if tool == "entity_introspection":
            data = {"properties": fields, "relationships": {"outgoing": [
                "HAS_HEADQUARTERS_IN", "PARTICIPATES_IN_FUNDING_ROUND", "OPERATES_IN_INDUSTRY"]}}
        else:
            data = {"id": "lead", "name": "Lead", "entity_type": "Company", "properties": {
                "headquarters_address": prop("1 Road, Menlo Park, United States"),
                "legal_name": prop("Synthetic Lead LP"), "lei": prop("Synthetic LEI"),
                "website": prop("lead.example.test")}}
        return ToolPayload(data=data, retrieved_at=datetime.now(timezone.utc))

    entity = asyncio.run(CalaNativeProvider().with_tool_caller(call).entity_metadata("lead"))
    assert len(calls) == 2
    projection = calls[1][1]
    assert projection["properties"] == fields
    assert set(projection["relationships"]["outgoing"]) == {"HAS_HEADQUARTERS_IN", "OPERATES_IN_INDUSTRY"}
    assert entity.country == "United States" and entity.metadata_status == "retrieved"
    assert entity.entity_type == "Company" and entity.kind == "unknown"
    assert str(entity.website) == "https://lead.example.test/"
    assert next(item for item in entity.metadata if item.name == "lei").value == "Synthetic LEI"


def test_native_investor_profile_projects_full_metadata_without_extra_calls():
    calls = []

    async def call(tool, args):
        calls.append((tool, args))
        if tool == "entity_introspection":
            data = {"properties": ["name", "legal_name", "headquarters_address", "investor_type"],
                    "relationships": {"outgoing": ["PARTICIPATES_IN_FUNDING_ROUND", "HAS_HEADQUARTERS_IN"]}}
        else:
            data = {"id": "candidate", "name": "Candidate", "entity_type": "Organization", "properties": {
                "legal_name": prop("Synthetic Candidate"), "investor_type": prop("VC"),
                "headquarters_address": prop("1 Road, Helsinki, Finland")}}
        return ToolPayload(data=data, retrieved_at=datetime.now(timezone.utc))

    profile = asyncio.run(CalaNativeProvider().with_tool_caller(call).investor_profile("candidate", 24))
    assert len(calls) == 2
    assert calls[1][1]["properties"] == ["name", "legal_name", "headquarters_address", "investor_type"]
    assert "PARTICIPATES_IN_FUNDING_ROUND" in calls[1][1]["relationships"]["outgoing"]
    assert profile.investor.country == "Finland" and profile.investor.kind == "vc"
    assert profile.investor.metadata_status == "retrieved"


def test_native_metadata_rejects_identity_mismatch():
    async def call(tool, args):
        data = {"properties": ["name"]} if tool == "entity_introspection" else {"id": "other", "name": "Other"}
        return ToolPayload(data=data, retrieved_at=datetime.now(timezone.utc))

    with pytest.raises(CalaError, match="different entity"):
        asyncio.run(CalaNativeProvider().with_tool_caller(call).entity_metadata("lead"))


def test_roles_parse_people_edges_with_dates_and_skip_unparseable_or_unsourced():
    from datetime import datetime, timezone
    from app.cala_native import _fund_events, _roles
    from app.models import Entity

    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    src = [{"document": "https://synthetic.example.test/role"}]
    data = {"relationships": {"incoming": {
        "WORKS_AT": [
            {"id": "p1", "name": "Partner One", "entity_type": "Person", "sources": src,
             "properties": {"valid_since": "2019-03-01", "is_current": True}},
            {"id": "p2", "name": "Bad Date", "entity_type": "Person", "sources": src,
             "properties": {"valid_since": "sometime"}},
            {"id": "p3", "name": "No Source", "entity_type": "Person", "properties": {"valid_since": "2019-01-01"}},
            {"id": "c1", "name": "A Company", "entity_type": "Company", "sources": src},
        ],
        "IS_BOARD_MEMBER_OF": [{"id": "p4", "name": "Board Four", "entity_type": "Person", "sources": src,
                                "properties": {"valid_since": "2014-01-01", "valid_until": "2017-01-01"}}],
    }}, "relationships_out": {}}
    fund = Entity(id="fund", name="Fund")
    roles = _roles(data, now, "incoming", fund)
    assert [(r.person.id, r.kind, r.current) for r in roles] == [("p1", "works_at", True), ("p4", "board", False)]
    assert roles[1].valid_until.isoformat() == "2017-01-01" and roles[0].org.id == "fund"
    events = _fund_events({"relationships": {"outgoing": {"MANAGES_FUND": [
        {"id": "f3", "name": "Fund III", "sources": src, "properties": {"vintage_year": 2026}},
        {"id": "f2", "name": "Fund II", "properties": {"vintage_year": 2022}}]}}}, now)
    assert [(e.name, e.event_date.year) for e in events] == [("Fund III", 2026)]
