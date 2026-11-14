"""REST identity matching and complete MCP facts retain the owner's rule meaning."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.cala import CalaError
from app.cala_native import CalaNativeProvider, ToolPayload
from app.config import Settings
from app.models import (
    CompanyMatchRequest, ConfirmCompanyMatches, Entity, EntityMetadataProperty,
    FounderInput, FundingRound, GraphSnapshot, InvestorProfile, Participation,
    ResolveRequest, Source, StartRunRequest,
)
from app.pipeline import Workflow, _portfolio_headquarters_companies
from app.runtime import Retrieval, utcnow


AT = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def source(path="fact"):
    return Source(url=f"https://synthetic.example.test/{path}", retrieved_at=AT,
                  published_at=AT.date() - timedelta(days=10))


def prop(value, path):
    return {"value": value, "sources": [{"document": f"https://synthetic.example.test/{path}",
                                          "date": "2026-11-04", "name": "Synthetic own-field fact"}]}


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, model_provider="disabled", cache_dir=tmp_path / "cache",
                    cache_ttl_seconds=kwargs.pop("cache_ttl_seconds", 0), **kwargs)


class RestIdentities:
    def __init__(self, count=1):
        self.calls = []
        self.count = count

    async def knowledge_query_entities(self, query, kind):
        self.calls.append((query, kind))
        prefix = "startup" if kind == "company" else "lead"
        return [Entity(id=f"{prefix}-{index}", name=f"REST {prefix} {index}",
                       kind="company" if kind == "company" else "unknown",
                       description="Basic lookup description", website="https://rest.example.test/",
                       metadata_status="retrieved", sources=[source("rest-query")])
                for index in range(self.count)]


class FullNativeMetadata(CalaNativeProvider):
    fields = ["id", "name", "office_address", "registered_address", "investor_type", "lei", "custom_property"]

    def __init__(self, *, failing=(), wrong=(), slow=()):
        super().__init__(endpoint=None)
        self.state = {"calls": [], "active": 0, "peak": 0}
        self.failing, self.wrong, self.slow = failing, wrong, slow

    async def search_entities(self, **kwargs):
        raise AssertionError("REST remains the identity matching source")

    async def call_data_tool(self, tool, arguments):
        self.state["calls"].append((tool, arguments))
        self.state["active"] += 1
        self.state["peak"] = max(self.state["peak"], self.state["active"])
        try:
            identifier = arguments["entity_id"]
            if tool == "entity_introspection":
                data = {"properties": self.fields, "relationships": {"outgoing": []}}
            else:
                if identifier in self.failing:
                    raise CalaError("synthetic_metadata_unavailable", "Synthetic metadata unavailable")
                if identifier in self.slow:
                    await asyncio.sleep(1)
                data = {"id": "wrong-id" if identifier in self.wrong else identifier,
                        "name": f"Canonical {identifier}", "entity_type": "Company", "properties": {
                            "office_address": prop("1 Test Road, Barcelona, Spain", "office"),
                            "registered_address": prop("2 Test Road, London, United Kingdom", "registered"),
                            "investor_type": prop("Venture capital", "category"),
                            "lei": prop("SYNTHETIC-LEI", "lei"),
                            "custom_property": prop({"enabled": True, "labels": ["first", "second"]}, "custom"),
                        }}
            return ToolPayload(data=data, retrieved_at=AT)
        finally:
            self.state["active"] -= 1


def test_rest_matching_is_followed_by_full_schema_driven_mcp_metadata(tmp_path):
    async def scenario():
        identity, provider = RestIdentities(), FullNativeMetadata()
        workflow = Workflow(settings(tmp_path, cala_concurrency=2), provider, identity=identity)
        matches = await workflow.match_companies(CompanyMatchRequest(company_name="Founder", lead_name="Lead"))
        assert identity.calls == [("Founder", "company"), ("Lead", "investor")]
        assert [item.id for item in matches.startup_matches] == ["startup-0"]
        assert [item.id for item in matches.lead_matches] == ["lead-0"]
        assert len(provider.state["calls"]) == 4 and provider.state["active"] == 0
        projections = [args for tool, args in provider.state["calls"] if tool == "entity_retrieval"]
        assert all(args["properties"] == provider.fields for args in projections)
        startup, lead = matches.startup_matches[0], matches.lead_matches[0]
        assert startup.office_address.endswith("Spain") and startup.country_basis == "office_address"
        assert startup.country == "Spain" and startup.metadata_retrieved_at == AT
        assert startup.description == "Basic lookup description" and str(startup.website) == "https://rest.example.test/"
        assert [str(item.url) for item in startup.country_sources] == ["https://synthetic.example.test/office"]
        assert startup.country_sources[0].published_at.isoformat() == "2026-11-04"
        assert lead.kind == "vc" and [str(item.url) for item in lead.kind_sources] == ["https://synthetic.example.test/category"]
        custom = next(item for item in startup.metadata if item.name == "custom_property")
        assert custom.value == {"enabled": True, "labels": ["first", "second"]}
        assert [str(item.url) for item in custom.sources] == ["https://synthetic.example.test/custom"]
        before = len(provider.state["calls"])
        confirmation = workflow.confirm_companies(matches.id, ConfirmCompanyMatches(startup_id="startup-0", lead_id="lead-0"))
        assert confirmation.prefill.country == "" and confirmation.prefill_sources["country"] == []
        assert confirmation.startup.country == "Spain" and confirmation.startup.country_sources == startup.country_sources
        assert len(provider.state["calls"]) == before
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["failing", "wrong", "slow"])
def test_rest_basic_matches_and_successful_metadata_survive_optional_failure(tmp_path, failure):
    async def scenario():
        provider = FullNativeMetadata(**{failure: ("startup-0",)})
        workflow = Workflow(settings(tmp_path, cala_request_timeout_seconds=.15), provider, identity=RestIdentities())
        matches = await workflow.match_companies(CompanyMatchRequest(company_name="Founder", lead_name="Lead"))
        assert matches.startup_matches[0].id == "startup-0"
        assert matches.startup_matches[0].name == "REST startup 0"
        assert matches.startup_matches[0].metadata_status == "unavailable"
        assert matches.lead_matches[0].metadata_status == "retrieved"
        assert matches.startup_matches[0].country is None
        assert any("metadata was unavailable" in notice for notice in matches.notices)
        assert provider.state["active"] == 0
        confirmed = workflow.confirm_companies(matches.id, ConfirmCompanyMatches(startup_id="startup-0", lead_id="lead-0"))
        assert confirmed.prefill.company_name == "REST startup 0"
    asyncio.run(scenario())


def test_mcp_metadata_uses_its_own_provider_budget_after_rest_matching(tmp_path):
    async def scenario():
        identity, provider = RestIdentities(count=3), FullNativeMetadata()
        workflow = Workflow(settings(tmp_path, cala_call_limit=3, cala_concurrency=1), provider, identity=identity)
        matches = await workflow.match_companies(CompanyMatchRequest(company_name="Founder", lead_name="Lead"))
        assert len(identity.calls) == 2 and len(provider.state["calls"]) == 3
        assert len(matches.startup_matches) == len(matches.lead_matches) == 3
        assert matches.lead_matches[0].metadata_status == "retrieved"
        assert any(item.metadata_status == "unavailable" for item in matches.startup_matches)
        assert any("lookup limits" in notice for notice in matches.notices)
        assert provider.state["peak"] <= 1 and provider.state["active"] == 0
    asyncio.run(scenario())


def test_rest_and_mcp_metadata_have_separate_bounded_deadlines(tmp_path, monkeypatch):
    import app.pipeline as pipeline

    seen = []

    class RecordingRetrieval(Retrieval):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self)

    monkeypatch.setattr(pipeline, "Retrieval", RecordingRetrieval)

    async def scenario():
        identity, provider = RestIdentities(), FullNativeMetadata()
        workflow = Workflow(settings(tmp_path, cala_query_timeout_seconds=.5, cala_request_timeout_seconds=.15),
                            provider, identity=identity)
        matches = await workflow.match_companies(CompanyMatchRequest(company_name="Founder", lead_name="Lead"))
        assert len(seen) == 2 and seen[0].provider is identity and seen[1].provider is provider
        assert seen[0].stop_at - seen[0].started == pytest.approx(.5, abs=.01)
        assert seen[1].stop_at - seen[1].started == pytest.approx(.15, abs=.01)
        assert all(entity.metadata_status == "retrieved" for entity in [*matches.startup_matches, *matches.lead_matches])
    asyncio.run(scenario())


def test_rest_matches_remain_selectable_when_adapter_has_no_full_metadata_operation(tmp_path):
    async def scenario():
        workflow = Workflow(settings(tmp_path), object(), identity=RestIdentities())
        matches = await workflow.match_companies(CompanyMatchRequest(company_name="Founder", lead_name="Lead"))
        assert all(entity.metadata_status == "unavailable" for entity in [*matches.startup_matches, *matches.lead_matches])
        assert any("metadata was unavailable" in notice for notice in matches.notices)
        confirmed = workflow.confirm_companies(matches.id, ConfirmCompanyMatches(startup_id="startup-0", lead_id="lead-0"))
        assert confirmed.prefill.company_name == "REST startup 0"
    asyncio.run(scenario())


def test_cached_native_metadata_retains_its_own_retrieval_time(tmp_path):
    async def scenario():
        identity, provider = RestIdentities(), FullNativeMetadata()
        workflow = Workflow(settings(tmp_path, cache_ttl_seconds=3600), provider, identity=identity)
        query = CompanyMatchRequest(company_name="Founder", lead_name="Lead")
        cold = await workflow.match_companies(query)
        warm = await workflow.match_companies(query)
        assert len(identity.calls) == 2 and len(provider.state["calls"]) == 4
        assert warm.startup_matches == cold.startup_matches
        assert warm.startup_matches[0].metadata_retrieved_at == AT
    asyncio.run(scenario())


@pytest.mark.parametrize("basis", ["office_address", "office_addresses", "registered_address", "registration_address",
                                   "registration_country_property", "registration_relationship"])
def test_rule_company_projection_keeps_fallback_metadata_without_calling_it_headquarters(basis):
    entity = Entity(id="portfolio", name="Portfolio", country="Spain", country_basis=basis,
                    country_sources=[source("address")], office_address="Barcelona, Spain",
                    registered_address="Madrid, Spain", metadata_status="retrieved",
                    metadata=[EntityMetadataProperty(name="office_address", value="Barcelona, Spain", sources=[source("address")])])
    projected = _portfolio_headquarters_companies([entity], [None])[entity.id]
    assert projected.country is None
    assert projected.office_address == entity.office_address and projected.metadata == entity.metadata
    assert projected.country_sources == entity.country_sources and projected.country_basis == basis
    assert entity.country == "Spain"  # Public full metadata is unchanged.


class RuleRunProvider:
    def __init__(self, *, basis, metadata_failure):
        self.lead = Entity(id="lead", name="Lead", kind="vc", sources=[source()])
        self.investor = Entity(id="candidate", name="Candidate", kind="vc", sources=[source()])
        self.company = Entity(id="portfolio", name="Portfolio", kind="company", country="Spain",
                              country_basis=basis, country_sources=[source("country")],
                              office_address="Barcelona, Spain", registered_address="Madrid, Spain",
                              metadata=[EntityMetadataProperty(name="industry", value="Software", sources=[source("industry")])])
        self.funding = FundingRound(id="event", company=self.company, event_date=utcnow().date() - timedelta(days=10),
                                    stage="seed", participants=[Participation(investor=self.lead), Participation(investor=self.investor)],
                                    sources=[source()])
        self.metadata_failure = metadata_failure

    async def search_entities(self, query, kind):
        return [self.lead.model_copy(deep=True)] if kind == "investor" else []

    async def lead_graph(self, lead_id, lookback_months):
        return GraphSnapshot(rounds=[self.funding.model_copy(deep=True)], complete=True)

    async def investor_profile(self, investor_id, lookback_months):
        return InvestorProfile(investor=self.investor.model_copy(deep=True), rounds=[self.funding.model_copy(deep=True)])

    async def entity_metadata(self, entity_id, company=False):
        if entity_id == "lead":
            return self.lead.model_copy(deep=True)
        if self.metadata_failure:
            raise CalaError("synthetic_metadata_unavailable", "Synthetic metadata unavailable")
        return self.company.model_copy(deep=True)


@pytest.mark.parametrize("basis,metadata_failure,context_limit", [("office_address", False, 1), ("registered_address", False, 1),
                                                                 ("office_address", True, 1), ("registration_relationship", True, 1),
                                                                 ("office_address", False, 0), ("headquarters_address", False, 1)])
def test_owner_country_rule_uses_headquarters_while_cards_keep_complete_metadata(tmp_path, basis, metadata_failure, context_limit):
    async def scenario():
        provider = RuleRunProvider(basis=basis, metadata_failure=metadata_failure)
        workflow = Workflow(settings(tmp_path, company_context_limit=context_limit), provider)
        founder = FounderInput(startup={"company_name": "Founder", "sector": "Software", "country": "Spain"},
                               lead_name="Lead", round={"stage": "seed", "target_eur": "1000000", "firm_commitments_eur": "0"})
        resolution = await workflow.resolve(ResolveRequest(profile=founder))
        status = workflow.start(StartRunRequest(resolution_id=resolution.id, lead_id="lead", profile=founder, profile_confirmed=True))
        record = workflow.get(status.id)
        await record.task
        assert record.result.state == "completed" and len(record.result.candidates) == 1
        card = record.result.candidates[0]
        country = next(item for item in card.components if item.name == "country_match")
        if basis == "headquarters_address":
            assert country.known and country.value == 1
        else:
            assert not country.known and "no headquarters" in country.explanation
        assert card.shared_deals[0].company.country == "Spain"
        assert card.shared_deals[0].company.country_basis == basis
        assert card.shared_deals[0].company.office_address == "Barcelona, Spain"
        sector = next(item for item in card.components if item.name == "sector_match")
        assert sector.known and sector.value == 1
        assert provider.company.country == "Spain"
    asyncio.run(scenario())
