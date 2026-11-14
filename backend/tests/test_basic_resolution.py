"""Main's optional-lead flow uses exact, sourced funding-event identities."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.cala import CalaError
from app.cala_native import CalaNativeProvider, ToolPayload
from app.config import Settings
from app.models import DetectedLead, Entity, FounderInput, GraphSnapshot, ResolveRequest, Source, StartRunRequest
from app.pipeline import Workflow, WorkflowError
from app.runtime import utcnow


def evidence():
    return Source(url="https://synthetic.example.test/company/funding", retrieved_at=utcnow(),
                  published_at=date(2026, 1, 1), label="Synthetic company announcement")


def profile(*, company="Synthetic Company", lead="", allocated=False):
    return FounderInput(startup={"company_name": company, "sector": "AI infrastructure"},
                        round={"stage": "pre_seed", "target_eur": "4000000",
                               "firm_commitments_eur": "4000000" if allocated else "2000000"},
                        lead_name=lead)


class AutoLeadProvider:
    def __init__(self, *, matches=None, delay=0, fail=False, wrong_company=False):
        self.company = Entity(id="startup", name="Synthetic Company", kind="company",
                              website="https://synthetic.example.test/")
        self.lead = Entity(id="lead", name="Synthetic Lead", sources=[evidence()])
        self.matches = [self.company] if matches is None else matches
        self.delay, self.fail, self.wrong_company = delay, fail, wrong_company
        self.calls = []

    async def search_entities(self, query, kind):
        self.calls.append(("search", query, kind))
        return [self.lead] if kind == "investor" else self.matches

    async def detect_lead(self, company_id):
        self.calls.append(("detect", company_id))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise CalaError("synthetic_failure", "Synthetic failure")
        return DetectedLead(lead=self.lead, company_id="wrong" if self.wrong_company else company_id,
                            event_id="identified-event", stage="PRE_SEED", event_date=date(2025, 1, 1),
                            event_date_precision="year", sources=[evidence()])

    async def lead_graph(self, lead_id, lookback_months):
        return GraphSnapshot(rounds=[], complete=True)


def config(tmp_path, **overrides):
    return Settings(_env_file=None, model_provider="disabled", cache_dir=tmp_path / "cache",
                    cache_ttl_seconds=0, **overrides)


def test_blank_lead_is_detected_from_company_and_requires_the_confirmed_profile(tmp_path):
    async def scenario():
        provider = AutoLeadProvider()
        workflow = Workflow(config(tmp_path), provider)
        original = profile()
        resolved = await workflow.resolve(ResolveRequest(profile=original))
        assert resolved.profile.lead_name == "Synthetic Lead"
        assert resolved.profile.round.lead_status == "unconfirmed"
        assert original.lead_name == ""  # Resolution does not mutate the submitted form.
        assert resolved.detected_lead.event_id == "identified-event"
        assert resolved.detected_lead.event_date_precision == "year"
        assert [item.id for item in resolved.lead_matches] == ["lead"]
        assert [item.id for item in resolved.startup_matches] == ["startup"]
        with pytest.raises(WorkflowError, match="Resolve the updated lead name"):
            workflow.start(StartRunRequest(resolution_id=resolved.id, lead_id="lead",
                                           profile_confirmed=True, profile=original))
        confirmed = resolved.profile.model_copy(deep=True)
        confirmed.startup.cala_entity_id = "startup"
        status = workflow.start(StartRunRequest(resolution_id=resolved.id, lead_id="lead",
                                                profile_confirmed=True, profile=confirmed))
        await workflow.get(status.id).task
        assert workflow.get(status.id).result.error is None
    asyncio.run(scenario())


def test_explicit_lead_does_not_trigger_company_lead_detection(tmp_path):
    async def scenario():
        provider = AutoLeadProvider()
        resolved = await Workflow(config(tmp_path), provider).resolve(
            ResolveRequest(profile=profile(lead="Synthetic Lead"), lookup_startup=True))
        assert resolved.detected_lead is None
        assert not any(call[0] == "detect" for call in provider.calls)
        assert resolved.profile.lead_name == "Synthetic Lead"
    asyncio.run(scenario())


def test_ambiguous_or_fuzzy_company_matches_are_not_silently_chosen(tmp_path):
    async def scenario():
        for matches in (
            [Entity(id="one", name="Synthetic Company"), Entity(id="two", name="Synthetic Company")],
            [Entity(id="fuzzy", name="Synthetic Company Similar Name")],
        ):
            provider = AutoLeadProvider(matches=matches)
            resolved = await Workflow(config(tmp_path), provider).resolve(ResolveRequest(profile=profile()))
            assert resolved.detected_lead is None and resolved.lead_matches == []
            assert len(resolved.startup_matches) == len(matches)
            assert not any(call[0] == "detect" for call in provider.calls)
            assert any("unique company match" in notice for notice in resolved.notices)
    asyncio.run(scenario())


def test_website_can_disambiguate_exact_company_matches(tmp_path):
    async def scenario():
        correct = Entity(id="correct", name="Synthetic Company", website="https://synthetic.example.test/")
        other = Entity(id="other", name="Synthetic Company", website="https://other.example.test/")
        provider = AutoLeadProvider(matches=[other, correct])
        founder = profile()
        founder.startup.website = correct.website
        resolved = await Workflow(config(tmp_path), provider).resolve(ResolveRequest(profile=founder))
        assert resolved.detected_lead.company_id == "correct"
    asyncio.run(scenario())


@pytest.mark.parametrize("fail,wrong_company", [(True, False), (False, True)])
def test_detection_failure_or_mismatched_company_preserves_search_matches(tmp_path, fail, wrong_company):
    async def scenario():
        provider = AutoLeadProvider(fail=fail, wrong_company=wrong_company)
        resolved = await Workflow(config(tmp_path), provider).resolve(ResolveRequest(profile=profile()))
        assert resolved.detected_lead is None and resolved.lead_matches == []
        assert resolved.startup_matches[0].id == "startup"
    asyncio.run(scenario())


def test_detection_timeout_preserves_company_matches_and_resolver_deadline(tmp_path):
    async def scenario():
        provider = AutoLeadProvider(delay=1)
        resolved = await Workflow(config(tmp_path, cala_request_timeout_seconds=.15), provider).resolve(
            ResolveRequest(profile=profile()))
        assert resolved.detected_lead is None and resolved.startup_matches[0].id == "startup"
        assert resolved.resolver_ms < 400
        assert any("lookup limits" in notice for notice in resolved.notices)
    asyncio.run(scenario())


def test_auto_detected_lead_cannot_be_reused_with_a_different_company_match(tmp_path):
    async def scenario():
        provider = AutoLeadProvider(matches=[Entity(id="startup", name="Synthetic Company"),
                                              Entity(id="other", name="Other Company")])
        workflow = Workflow(config(tmp_path), provider)
        resolved = await workflow.resolve(ResolveRequest(profile=profile()))
        confirmed = resolved.profile.model_copy(deep=True)
        confirmed.startup.cala_entity_id = "other"
        with pytest.raises(WorkflowError) as caught:
            workflow.start(StartRunRequest(resolution_id=resolved.id, lead_id="lead",
                                           profile_confirmed=True, profile=confirmed))
        assert caught.value.code == "detected_lead_company_changed"
    asyncio.run(scenario())


def test_blank_lead_with_filled_allocation_makes_zero_cala_calls(tmp_path):
    async def scenario():
        provider = AutoLeadProvider()
        resolved = await Workflow(config(tmp_path), provider).resolve(ResolveRequest(profile=profile(allocated=True)))
        assert resolved.no_open_allocation and provider.calls == []
    asyncio.run(scenario())


def native_transcript(*, is_lead=True, issuer="startup", second_lead=False, dated=True, issuer_sourced=True):
    calls = []
    source = {"document": "https://synthetic.example.test/funding", "date": "2026-01-01"}

    def prop(value):
        return {"value": value, "sources": [source]}

    def participation(identifier, role="INVESTOR", lead=None):
        data = {"id": identifier, "name": identifier, "entity_type": "Company",
                "properties": {"role": role, "sources": [source]}}
        if lead is not None:
            data["properties"]["is_lead"] = lead
        return data

    async def call(tool, args):
        calls.append((tool, args))
        identifier = args.get("entity_id")
        if tool == "entity_introspection":
            data = {"properties": ["id", "name", "event_date", "event_date__precision", "round_label"],
                    "relationships": {"outgoing": ["PARTICIPATES_IN_FUNDING_ROUND"],
                                      "incoming": ["PARTICIPATES_IN_FUNDING_ROUND"]}}
        elif identifier == "startup":
            data = {"id": "startup", "name": "Startup", "properties": {},
                    "relationships": {"outgoing": {"PARTICIPATES_IN_FUNDING_ROUND": [
                        {"id": "event", "entity_type": "FundingRound", "properties": {"role": "ISSUER"}}]}}}
        else:
            people = [participation(issuer, "ISSUER"), participation("lead", lead=is_lead)]
            if not issuer_sourced:
                people[0]["properties"]["sources"] = []
            if second_lead:
                people.append(participation("co-lead", lead=True))
            data = {"id": "event", "name": "Seed", "properties": {"round_label": prop("SEED")},
                    "relationships": {"incoming": {"PARTICIPATES_IN_FUNDING_ROUND": people}}}
            if dated:
                data["properties"].update(event_date=prop((utcnow() - timedelta(days=30)).date().isoformat()),
                                          event_date__precision=prop("day"))
        return ToolPayload(data=data, retrieved_at=utcnow())
    return call, calls


def test_native_detection_retains_identified_event_roles_sources_and_raw_call_count():
    call, calls = native_transcript()
    detected = asyncio.run(CalaNativeProvider().with_tool_caller(call).detect_lead("startup"))
    assert detected.company_id == "startup" and detected.event_id == "event"
    assert detected.lead.id == "lead" and detected.sources
    assert detected.event_date_precision == "day"
    assert len(calls) == 4


@pytest.mark.parametrize("options", [{"is_lead": None}, {"is_lead": False},
                                      {"issuer": "different-startup"}, {"second_lead": True},
                                      {"issuer_sourced": False}])
def test_native_detection_does_not_guess_unknown_leads_or_merge_wrong_issuers(options):
    call, _ = native_transcript(**options)
    assert asyncio.run(CalaNativeProvider().with_tool_caller(call).detect_lead("startup")) is None


def test_identified_undated_event_can_suggest_lead_without_inventing_a_date():
    call, _ = native_transcript(dated=False)
    detected = asyncio.run(CalaNativeProvider().with_tool_caller(call).detect_lead("startup"))
    assert detected.lead.id == "lead" and detected.event_date is None
