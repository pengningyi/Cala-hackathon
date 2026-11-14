"""Synthetic transcripts exercise the observed native Cala schema without keys."""
import asyncio
from datetime import datetime, timedelta, timezone

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
