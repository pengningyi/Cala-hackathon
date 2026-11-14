"""Backend acceptance cases using synthetic evidence, never live Cala data.

These tests establish workflow behavior. They do not establish Cala integration
readiness or the PRD's live-demo timing gate.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.cala import CalaError
from app.config import Settings
from app.models import (
    Entity, FounderInput, FundingRound, GraphSnapshot, InvestorProfile,
    Participation, ResolveRequest, Source, StartRunRequest,
)
from app.pipeline import Workflow, WorkflowError
from app.runtime import BudgetExceeded, ResponseCache, Retrieval, Trace, utcnow


def evidence():
    return Source(url="https://synthetic.example.test/funding-event",
                  label="Synthetic test fixture, not live Cala",
                  retrieved_at=utcnow() - timedelta(hours=1),
                  published_at=(utcnow() - timedelta(days=40)).date())


def entity(identifier, *, kind="vc", related=()):
    return Entity(id=identifier, name=f"Synthetic {identifier}", kind=kind,
                  country="Finland", related_entity_ids=list(related), sources=[evidence()])


def founder(**overrides):
    data = {
        "founder_name": "Test Founder",
        "startup": {
            "company_name": "Unlisted Test Startup",
            "product_description": "Synthetic invoicing software",
            "sector": "Financial software",
            "target_customer": "Small businesses",
            "problem_solved": "Manual invoice reconciliation",
            "country": "Finland",
        },
        "round": {
            "stage": "seed", "target_eur": "2000000.00",
            "firm_commitments_eur": "1400000.00",
            "conditional_interest_eur": "300000.00",
            "lead_status": "firm_verbal", "lead_ticket_eur": "1000000.00",
        },
        "lead_name": "Synthetic lead",
    }
    data.update(overrides)
    return FounderInput.model_validate(data)


def funding(identifier, participants, *, company="company-a", days=30,
            sourced=True, dated=True):
    return FundingRound(
        id=identifier, company=entity(company, kind="company"),
        event_date=(utcnow() - timedelta(days=days)).date() if dated else None,
        stage="seed", company_country="Finland", total_amount_eur="9000000",
        participants=[Participation(investor=p) for p in participants],
        sources=[evidence()] if sourced else [],
    )


class SyntheticProvider:
    """Deterministic injected provider; the application never selects this itself."""

    def __init__(self, *, graph=None, profiles=None, profile_delay=0):
        self.lead = entity("lead")
        self.graph = graph or GraphSnapshot(rounds=[], complete=True)
        self.profiles = profiles or {}
        self.profile_delay = profile_delay
        self.calls = []
        self.active_profiles = self.max_active_profiles = 0

    async def search_entities(self, query, kind):
        self.calls.append(("search_entities", query, kind))
        return [] if kind == "company" else [self.lead.model_copy(deep=True)]

    async def lead_graph(self, lead_id, lookback_months):
        self.calls.append(("lead_graph", lead_id, lookback_months))
        return self.graph.model_copy(deep=True)

    async def investor_profile(self, investor_id, lookback_months):
        self.calls.append(("investor_profile", investor_id, lookback_months))
        self.active_profiles += 1
        self.max_active_profiles = max(self.max_active_profiles, self.active_profiles)
        try:
            await asyncio.sleep(self.profile_delay)
            profile = self.profiles.get(investor_id, InvestorProfile(investor=entity(investor_id)))
            return profile.model_copy(deep=True)
        finally:
            self.active_profiles -= 1


def settings(tmp_path, **overrides):
    return Settings(_env_file=None, model_provider="disabled", cache_dir=tmp_path / "cache",
                    cache_ttl_seconds=0, **overrides)


async def complete_run(workflow, profile=None, *, lookup_startup=False):
    profile = profile or founder()
    resolved = await workflow.resolve(ResolveRequest(profile=profile, lookup_startup=lookup_startup))
    status = workflow.start(StartRunRequest(
        resolution_id=resolved.id, lead_id=resolved.lead_matches[0].id if resolved.lead_matches else None,
        profile_confirmed=True, profile=profile,
    ))
    record = workflow.get(status.id)
    await record.task
    assert record.result is not None
    return resolved, record


def test_manual_profile_resolve_confirm_and_copyable_result(tmp_path):
    async def scenario():
        lead, candidate = entity("lead"), entity("candidate")
        provider = SyntheticProvider(graph=GraphSnapshot(
            rounds=[funding("shared-event", [lead, candidate])], complete=True))
        workflow = Workflow(settings(tmp_path), provider)
        resolved, record = await complete_run(workflow, lookup_startup=True)
        assert resolved.startup_matches == []
        assert resolved.profile.startup.company_name == "Unlisted Test Startup"
        assert resolved.resolver_ms >= 0
        assert record.confirmed_at >= resolved.created_at
        result = record.result
        assert result.state == "completed"
        assert result.remaining_allocation_eur == 600000
        assert result.profile.round.conditional_interest_eur == 300000
        assert len(result.candidates) == 1
        card = result.candidates[0]
        assert card.investor.id == "candidate"
        assert "Synthetic candidate" in card.draft and "Unlisted Test Startup" in card.draft
        assert "600,000" in card.draft and "Synthetic company-a" in card.draft
        assert card.sources and card.shared_deals[0].id == "shared-event"
        assert card.profile.ticket_criteria == []  # Event total is not an investor check.
        assert result.cala_calls == 2  # Resolver calls are measured separately.
        assert [event.id for event in record.trace.events] == list(range(1, len(record.trace.events) + 1))
        assert record.trace.events[-1].type == "complete"
        assert any(event.tool == "lead_graph" and event.query["lead_id"] == "lead"
                   for event in record.trace.events)
    asyncio.run(scenario())


def test_confirmation_rejects_unoffered_id_changed_lead_and_missing_confirmation(tmp_path):
    async def scenario():
        provider = SyntheticProvider()
        workflow = Workflow(settings(tmp_path), provider)
        profile = founder()
        resolved = await workflow.resolve(ResolveRequest(profile=profile))
        request = dict(resolution_id=resolved.id, lead_id="unoffered-lead", profile_confirmed=True,
                       profile=profile)
        with pytest.raises(WorkflowError, match="Select a lead ID"):
            workflow.start(StartRunRequest(**request))
        changed = profile.model_copy(update={"lead_name": "A different lead"}, deep=True)
        request.update(lead_id="lead", profile=changed)
        with pytest.raises(WorkflowError, match="updated lead name"):
            workflow.start(StartRunRequest(**request))
        request.update(profile=profile, profile_confirmed=False)
        with pytest.raises(ValidationError):
            StartRunRequest(**request)
        assert len(provider.calls) == 1 and workflow.runs == {}
    asyncio.run(scenario())


def test_confirmation_freezes_founder_input(tmp_path):
    async def scenario():
        workflow = Workflow(settings(tmp_path), SyntheticProvider())
        profile = founder()
        resolved = await workflow.resolve(ResolveRequest(profile=profile))
        status = workflow.start(StartRunRequest(resolution_id=resolved.id, lead_id="lead",
                                                profile_confirmed=True, profile=profile))
        profile.startup.company_name = "Changed after confirmation"
        record = workflow.get(status.id)
        await record.task
        assert record.profile.startup.company_name == "Unlisted Test Startup"
        assert resolved.profile.startup.company_name == "Unlisted Test Startup"
    asyncio.run(scenario())


@pytest.mark.parametrize("committed", ["2000000", "2300000"])
def test_no_open_allocation_skips_every_cala_call(tmp_path, committed):
    async def scenario():
        profile = founder()
        profile.round.firm_commitments_eur = Decimal(committed)
        profile = FounderInput.model_validate(profile.model_dump())
        provider = SyntheticProvider()
        resolved, record = await complete_run(Workflow(settings(tmp_path), provider), profile)
        assert resolved.no_open_allocation is True
        assert record.result.state == "no_open_allocation"
        assert record.result.remaining_allocation_eur == 0
        assert record.result.cala_calls == 0 and provider.calls == []
        assert record.result.candidates == []
    asyncio.run(scenario())


def test_only_same_dated_sourced_event_qualifies_and_duplicates_do_not_inflate_counts(tmp_path):
    async def scenario():
        lead, connected = entity("lead"), entity("connected")
        other = entity("same-company-different-round")
        first = funding("event-a", [lead, connected])
        graph = GraphSnapshot(rounds=[
            first, first.model_copy(deep=True),
            funding("event-b", [lead, connected], days=60),
            funding("other-round", [other]),
            funding("undated", [lead, entity("undated")], dated=False),
            funding("unsourced", [lead, entity("unsourced")], sourced=False),
            funding("outside-lookback", [lead, entity("too-old")], days=1900),
            funding("future-event", [lead, entity("future")], days=-10),
        ], complete=True)
        provider = SyntheticProvider(graph=graph)
        _, record = await complete_run(Workflow(settings(tmp_path), provider))
        result = record.result
        assert [c.investor.id for c in result.candidates] == ["connected"]
        assert result.candidates[0].shared_round_count == 2
        assert result.candidates[0].shared_company_count == 1
        assert result.counts["discovered"] == 1
        assert [c[1] for c in provider.calls if c[0] == "investor_profile"] == ["connected"]
    asyncio.run(scenario())


def test_no_match_never_pads_generic_candidates(tmp_path):
    async def scenario():
        _, record = await complete_run(Workflow(settings(tmp_path), SyntheticProvider()))
        assert record.result.state == "no_match"
        assert record.result.candidates == [] and record.result.combined_draft == ""
        assert record.result.lookback_months == 60
        assert any("60-month" in text for text in record.result.notices)
    asyncio.run(scenario())


def test_sparse_profile_keeps_unknowns_and_neutral_score_components(tmp_path):
    async def scenario():
        # Connection inside 60 months; no observations inside the 24-month profile.
        graph = GraphSnapshot(rounds=[funding("shared", [entity("lead"), entity("sparse")], days=1000)], complete=True)
        _, record = await complete_run(Workflow(settings(tmp_path), SyntheticProvider(graph=graph)))
        card = record.result.candidates[0]
        assert card.profile_completeness == 0.35
        assert card.recent_rounds_12m is None
        assert card.profile.assessments == []
        assert all(not component.known and component.value == 0.5
                   for component in card.components if component.name != "co_investment")
        assert card.shared_round_count == 1 and len(record.result.candidates) == 1
    asyncio.run(scenario())


def test_statuses_exclude_before_profiling_and_adapt_existing_conversation(tmp_path):
    async def scenario():
        names = ["shareholder", "approached", "conversation", "conditional", "committed", "declined", "blocked", "lead-vehicle"]
        lead = entity("lead", related=["lead-vehicle"])
        provider = SyntheticProvider(graph=GraphSnapshot(
            rounds=[funding("shared", [lead, *(entity(name) for name in names)])], complete=True))
        provider.lead = lead
        profile = founder(investor_statuses=[
            {"investor_id": "shareholder", "statuses": ["existing_shareholder"]},
            {"investor_id": "approached", "statuses": ["already_approached"]},
            {"investor_id": "conversation", "statuses": ["in_conversation"]},
            {"investor_id": "conditional", "statuses": ["conditional_interest"]},
            {"investor_id": "committed", "statuses": ["firm_committed"]},
            {"investor_id": "declined", "statuses": ["declined"]},
            {"investor_id": "blocked", "statuses": ["existing_shareholder", "firm_committed", "do_not_contact"]},
        ])
        profile.round.lead_status = "conditional"
        _, record = await complete_run(Workflow(settings(tmp_path), provider), profile)
        cards = {c.investor.id: c for c in record.result.candidates}
        assert set(cards) == {"shareholder", "approached", "conversation", "conditional"}
        assert cards["approached"].action == cards["conversation"].action == "follow_up"
        assert "existing conversation" in cards["approached"].draft
        assert cards["shareholder"].draft  # Existing cap-table membership stays eligible for follow-on.
        assert all("provisional" in c.draft for c in cards.values())
        excluded = {e.investor.id: e.code for e in record.result.exclusions}
        assert excluded == {"committed": "firm_committed", "declined": "declined",
                            "blocked": "do_not_contact", "lead-vehicle": "lead_alias"}
        profiled = {c[1] for c in provider.calls if c[0] == "investor_profile"}
        assert profiled == set(cards)
    asyncio.run(scenario())


def test_concurrency_is_bounded_to_four_and_profiles_to_five(tmp_path):
    async def scenario():
        candidates = [entity(f"candidate-{i}") for i in range(12)]
        provider = SyntheticProvider(graph=GraphSnapshot(
            rounds=[funding("shared", [entity("lead"), *candidates])], complete=True), profile_delay=0.01)
        _, record = await complete_run(Workflow(settings(tmp_path), provider))
        assert provider.max_active_profiles == 4
        assert len([c for c in provider.calls if c[0] == "investor_profile"]) == 5
        assert len(record.result.candidates) == 5
        assert record.result.counts["discovered"] == 12
        assert any(e.step == "lead_graph" and e.count == 10 for e in record.trace.events)
    asyncio.run(scenario())


def test_deadline_returns_partial_and_unfinished_profile_is_not_excluded(tmp_path):
    async def scenario():
        provider = SyntheticProvider(graph=GraphSnapshot(
            rounds=[funding("shared", [entity("lead"), entity("slow")])], complete=True), profile_delay=0.2)
        config = settings(tmp_path, run_deadline_seconds=0.06, assembly_reserve_seconds=0.01,
                          cala_request_timeout_seconds=0.03)
        _, record = await complete_run(Workflow(config, provider))
        result = record.result
        assert result.state == "partial"
        assert [e.id for e in result.incomplete] == ["slow"]
        assert result.exclusions == [] and result.candidates == []
        assert result.processing_ms < 500
        assert result.cala_calls <= 3  # Graph plus at most two profile attempts.
    asyncio.run(scenario())


def test_partial_deadline_preserves_available_card_and_its_draft(tmp_path):
    class MixedSpeedProvider(SyntheticProvider):
        async def investor_profile(self, investor_id, lookback_months):
            if investor_id == "slow":
                await asyncio.sleep(0.2)
            return await super().investor_profile(investor_id, lookback_months)

    async def scenario():
        provider = MixedSpeedProvider(graph=GraphSnapshot(rounds=[
            funding("shared", [entity("lead"), entity("ready"), entity("slow")])], complete=True))
        config = settings(tmp_path, run_deadline_seconds=0.07, assembly_reserve_seconds=0.01,
                          cala_request_timeout_seconds=0.035)
        _, record = await complete_run(Workflow(config, provider))
        result = record.result
        assert result.state == "partial"
        assert [card.investor.id for card in result.candidates] == ["ready"]
        assert result.candidates[0].draft and result.combined_draft
        assert [investor.id for investor in result.incomplete] == ["slow"]
        assert result.exclusions == []
    asyncio.run(scenario())


def test_twenty_attempt_budget_includes_transient_retries(tmp_path):
    class RetryProvider:
        def __init__(self):
            self.attempts = Counter()

        async def search_entities(self, query, kind):
            self.attempts[query] += 1
            if self.attempts[query] == 1:
                raise CalaError("rate_limited", "Synthetic transient response", transient=True)
            return [entity(query)]

    async def scenario():
        provider = RetryProvider()
        config = settings(tmp_path)
        retrieval = Retrieval(provider, ResponseCache(config.cache_dir, 0, "synthetic"), config, Trace())
        for index in range(10):
            await retrieval.call("search_entities", {"query": f"investor-{index}", "kind": "investor"},
                                 list[Entity], "test")
        assert retrieval.calls == sum(provider.attempts.values()) == 20
        assert all(count == 2 for count in provider.attempts.values())
        with pytest.raises(BudgetExceeded):
            await retrieval.call("search_entities", {"query": "over-budget", "kind": "investor"},
                                 list[Entity], "test")
        assert "over-budget" not in provider.attempts
    asyncio.run(scenario())


def test_transient_failure_retries_once_and_respects_retry_after(tmp_path, monkeypatch):
    delays = []
    original_sleep = asyncio.sleep

    async def record_delay(seconds):
        delays.append(seconds)
        await original_sleep(0)

    monkeypatch.setattr("app.runtime.asyncio.sleep", record_delay)

    class UnavailableProvider:
        attempts = 0

        async def search_entities(self, query, kind):
            self.attempts += 1
            raise CalaError("unavailable", "Synthetic outage", transient=True, retry_after=0.005)

    async def scenario():
        provider = UnavailableProvider()
        config = settings(tmp_path)
        retrieval = Retrieval(provider, ResponseCache(config.cache_dir, 0, "synthetic"), config, Trace())
        with pytest.raises(CalaError):
            await retrieval.call("search_entities", {"query": "lead", "kind": "investor"}, list[Entity], "test")
        assert provider.attempts == retrieval.calls == 2
        assert delays == [0.005]
    asyncio.run(scenario())


def test_cache_avoids_calls_and_preserves_retrieval_and_publication_dates(tmp_path):
    async def scenario():
        provider = SyntheticProvider()
        config = settings(tmp_path)
        cache = ResponseCache(config.cache_dir, 3600, "synthetic")
        first = Retrieval(provider, cache, config, Trace())
        args = {"query": "lead", "kind": "investor"}
        original = await first.call("search_entities", args, list[Entity], "resolver")
        second = Retrieval(provider, cache, config, Trace())
        cached = await second.call("search_entities", args, list[Entity], "resolver")
        assert len(provider.calls) == first.calls == 1
        assert second.calls == 0 and second.cache_hits == 1
        assert second.timestamps == first.timestamps
        assert cached == original
        assert cached[0].sources[0].retrieved_at == original[0].sources[0].retrieved_at
        assert cached[0].sources[0].published_at == original[0].sources[0].published_at
        assert second.trace.events[-1].cached is True
    asyncio.run(scenario())


def test_http_manual_confirmation_result_and_replayable_sse(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from app.main import create_app

    provider = SyntheticProvider(graph=GraphSnapshot(
        rounds=[funding("shared", [entity("lead"), entity("candidate")])], complete=True))
    app = create_app(settings(tmp_path), provider=provider)
    profile = founder().model_dump(mode="json")

    async def wait_for_run(run_id):
        await app.state.workflow.get(run_id).task

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json()["model_provider"] == "disabled"
        resolved = client.post("/api/resolve", json={"profile": profile, "lookup_startup": True})
        assert resolved.status_code == 200
        resolution = resolved.json()
        assert resolution["startup_matches"] == []
        request = {"resolution_id": resolution["id"], "lead_id": "candidate",
                   "profile_confirmed": True, "profile": profile}
        invalid = client.post("/api/runs", json=request)
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "invalid_lead_selection"
        request["lead_id"] = "lead"
        started = client.post("/api/runs", json=request)
        assert started.status_code == 202
        status = started.json()
        client.portal.call(wait_for_run, status["id"])
        result = client.get(status["result_url"])
        assert result.status_code == 200 and result.json()["state"] == "completed"
        assert result.json()["candidates"][0]["draft"]
        events = client.get(status["events_url"])
        assert events.status_code == 200
        assert events.headers["content-type"].startswith("text/event-stream")
        data = [json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")]
        assert data[0]["step"] == "confirmation" and data[-1]["type"] == "complete"
        assert [event["id"] for event in data] == list(range(1, len(data) + 1))
        replay = client.get(status["events_url"], headers={"Last-Event-ID": "1"})
        assert "id: 1\n" not in replay.text
        assert f"id: {data[-1]['id']}\n" in replay.text
        finished = client.get(status["events_url"], headers={"Last-Event-ID": str(data[-1]["id"])})
        assert finished.status_code == 200 and finished.text == ""
        invalid_cursor = client.get(status["events_url"], headers={"Last-Event-ID": "not-a-number"})
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["error"]["code"] == "invalid_event_cursor"


def test_http_pending_result_validation_and_missing_run_are_structured(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    class PausedProvider(SyntheticProvider):
        def __init__(self):
            super().__init__(graph=GraphSnapshot(
                rounds=[funding("shared", [entity("lead"), entity("paused")])], complete=True))
            self.release = asyncio.Event()

        async def investor_profile(self, investor_id, lookback_months):
            await self.release.wait()
            return await super().investor_profile(investor_id, lookback_months)

    provider = PausedProvider()
    app = create_app(settings(tmp_path), provider=provider)
    profile = founder().model_dump(mode="json")

    async def release_and_wait(run_id):
        provider.release.set()
        await app.state.workflow.get(run_id).task

    with TestClient(app) as client:
        missing = client.get("/api/runs/missing/result")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "run_not_found"
        invalid_profile = founder().model_dump(mode="json")
        invalid_profile["round"]["target_eur"] = "-1"
        invalid = client.post("/api/resolve", json={"profile": invalid_profile})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"
        assert invalid.json()["error"]["details"]
        resolution = client.post("/api/resolve", json={"profile": profile}).json()
        start = client.post("/api/runs", json={"resolution_id": resolution["id"],
                            "lead_id": "lead", "profile_confirmed": True, "profile": profile})
        assert start.status_code == 202
        status = start.json()
        pending = client.get(status["result_url"])
        assert pending.status_code == 202 and pending.headers["retry-after"] == "1"
        assert pending.json()["state"] == "running"
        client.portal.call(release_and_wait, status["id"])
        assert client.get(status["result_url"]).json()["state"] == "completed"


def test_lead_aliases_learned_from_graph_are_excluded_before_profiles(tmp_path):
    async def run():
        lead = entity("lead").model_copy(update={"aliases": ["Synthetic vehicle"]})
        provider = SyntheticProvider(graph=GraphSnapshot(
            rounds=[funding("shared", [lead, entity("vehicle")])], complete=True, lead=lead))
        workflow = Workflow(settings(tmp_path), provider)
        _, record = await complete_run(workflow)
        assert record.result.candidates == []
        assert record.result.exclusions[0].code == "lead_alias"
        assert record.result.lead.aliases == ["Synthetic vehicle"]
        assert not any(call[0] == "investor_profile" for call in provider.calls)

    asyncio.run(run())
