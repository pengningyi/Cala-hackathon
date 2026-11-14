"""Resolver metadata joins preserve identities and the existing raw-call limits."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from datetime import timedelta

from app.cala import CalaError
from app.cala_native import ToolPayload
from app.config import Settings
from app.models import Entity, FounderInput, ResolveRequest
from app.pipeline import Workflow
from app.runtime import utcnow


def profile(*, allocated=False):
    return FounderInput(
        startup={"company_name": "Synthetic startup", "product_description": "Test product",
                 "sector": "Test sector", "target_customer": "Test buyer",
                 "problem_solved": "Test problem", "country": "Finland"},
        round={"stage": "seed", "target_eur": "1000000",
               "firm_commitments_eur": "1000000" if allocated else "500000",
               "lead_status": "firm_verbal"},
        lead_name="Synthetic lead",
    )


def settings(tmp_path, **overrides):
    return Settings(_env_file=None, model_provider="disabled", cache_dir=tmp_path / "cache",
                    cache_ttl_seconds=overrides.pop("cache_ttl_seconds", 0), **overrides)


@dataclass
class MetadataState:
    calls: list = field(default_factory=list)
    active: int = 0
    max_active: int = 0
    retrieved_at: object = field(default_factory=lambda: utcnow() - timedelta(minutes=2))


class SyntheticNativeMetadata:
    """Each full profile deliberately costs schema + property retrieval."""

    def __init__(self, *, lead_ids=("lead", "vehicle"), startup_ids=("startup",),
                 failing_ids=(), wrong_ids=(), slow_ids=(), delay=0,
                 startup_failure=False):
        self.state = MetadataState()
        self.lead_ids, self.startup_ids = lead_ids, startup_ids
        self.failing_ids, self.wrong_ids, self.slow_ids = failing_ids, wrong_ids, slow_ids
        self.delay, self.startup_failure = delay, startup_failure
        self._caller = None

    def with_tool_caller(self, caller):
        bound = copy.copy(self)
        bound._caller = caller
        return bound

    async def _call(self, tool, args):
        return await (self._caller or self.call_data_tool)(tool, args)

    async def search_entities(self, query, kind):
        result = await self._call("entity_search", {"query": query, "kind": kind})
        return [Entity.model_validate(item) for item in result.data["entities"]]

    async def entity_metadata(self, entity_id, company=False):
        await self._call("entity_introspection", {"entity_id": entity_id})
        result = await self._call("entity_retrieval", {"entity_id": entity_id, "company": company})
        return Entity.model_validate(result.data)

    async def call_data_tool(self, tool, arguments):
        self.state.calls.append((tool, dict(arguments)))
        self.state.active += 1
        self.state.max_active = max(self.state.max_active, self.state.active)
        try:
            await asyncio.sleep(self.delay)
            identifier = arguments.get("entity_id")
            if tool == "entity_search":
                if arguments["kind"] == "company" and self.startup_failure:
                    raise CalaError("synthetic_startup_failure", "Synthetic optional lookup failure")
                identifiers = self.startup_ids if arguments["kind"] == "company" else self.lead_ids
                data = {"entities": [{"id": item, "name": f"Basic {item}",
                                      "description": "Basic description"} for item in identifiers]}
            elif tool == "entity_introspection":
                data = {"properties": ["id", "name", "country"]}
            else:
                if identifier in self.slow_ids:
                    await asyncio.sleep(1)
                if identifier in self.failing_ids:
                    raise CalaError("synthetic_metadata_failure", "Synthetic metadata failure")
                data = {"id": "different-identity" if identifier in self.wrong_ids else identifier,
                        "name": f"Full {identifier}", "entity_type": "Company",
                        "country": "United States", "website": "https://synthetic.example.test/",
                        "metadata_status": "retrieved",
                        "metadata_retrieved_at": self.state.retrieved_at.isoformat(),
                        "metadata": [{"name": "country", "value": "United States", "sources": []}]}
            return ToolPayload(data=data, retrieved_at=self.state.retrieved_at)
        finally:
            self.state.active -= 1


def test_resolver_enriches_all_offered_lead_and_startup_matches(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata(delay=0.002)
        workflow = Workflow(settings(tmp_path, cala_concurrency=2), provider)
        resolved = await workflow.resolve(ResolveRequest(profile=profile(), lookup_startup=True))
        assert [item.id for item in resolved.lead_matches] == ["lead", "vehicle"]
        assert [item.id for item in resolved.startup_matches] == ["startup"]
        assert all(item.country == "United States" and item.entity_type == "Company"
                   and item.metadata_status == "retrieved"
                   for item in [*resolved.lead_matches, *resolved.startup_matches])
        assert resolved.lead_matches[0].description == "Basic description"
        assert len(provider.state.calls) == 8  # Search2 + three metadata joins of two calls.
        assert provider.state.max_active == 2 and provider.state.active == 0
        assert resolved.notices == []
    asyncio.run(scenario())


def test_metadata_timeout_keeps_basic_matches_and_completed_full_metadata(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata(lead_ids=("fast", "slow"), slow_ids=("slow",))
        workflow = Workflow(settings(tmp_path, cala_request_timeout_seconds=0.2), provider)
        resolved = await workflow.resolve(ResolveRequest(profile=profile()))
        assert [item.id for item in resolved.lead_matches] == ["fast", "slow"]
        assert resolved.lead_matches[0].metadata_status == "retrieved"
        assert resolved.lead_matches[0].country == "United States"
        assert resolved.lead_matches[1].name == "Basic slow"
        assert resolved.lead_matches[1].metadata_status == "unavailable"
        assert resolved.lead_matches[1].country is None
        assert any("lookup limits" in item for item in resolved.notices)
        assert resolved.resolver_ms < 400 and provider.state.active == 0
        # Confirming the original identity remains possible after partial enrichment.
        assert {item.id for item in workflow.resolutions[resolved.id].lead_matches} == {"fast", "slow"}
    asyncio.run(scenario())


def test_metadata_failure_or_mismatched_identity_never_replaces_basic_match(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata(lead_ids=("failed", "wrong"),
                                           failing_ids=("failed",), wrong_ids=("wrong",))
        resolved = await Workflow(settings(tmp_path), provider).resolve(ResolveRequest(profile=profile()))
        assert [(item.id, item.name) for item in resolved.lead_matches] == [
            ("failed", "Basic failed"), ("wrong", "Basic wrong")]
        assert all(item.metadata_status == "unavailable" and item.country is None
                   for item in resolved.lead_matches)
        assert len(resolved.notices) == 1
    asyncio.run(scenario())


def test_metadata_raw_calls_stop_at_existing_resolution_budget(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata(lead_ids=("one", "two", "three"), delay=0.002)
        workflow = Workflow(settings(tmp_path, cala_call_limit=5, cala_concurrency=2), provider)
        resolved = await workflow.resolve(ResolveRequest(profile=profile()))
        assert len(provider.state.calls) == 5
        assert [item.id for item in resolved.lead_matches] == ["one", "two", "three"]
        assert any(item.metadata_status == "unavailable" for item in resolved.lead_matches)
        assert provider.state.max_active <= 2 and provider.state.active == 0
        assert any("lookup limits" in item for item in resolved.notices)
    asyncio.run(scenario())


def test_cached_full_metadata_preserves_properties_and_retrieval_dates(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata(lead_ids=("lead",))
        workflow = Workflow(settings(tmp_path, cache_ttl_seconds=3600), provider)
        cold = await workflow.resolve(ResolveRequest(profile=profile()))
        calls = len(provider.state.calls)
        warm = await workflow.resolve(ResolveRequest(profile=profile()))
        assert calls == 3 and len(provider.state.calls) == calls
        assert warm.lead_matches == cold.lead_matches
        assert warm.lead_matches[0].metadata_retrieved_at == provider.state.retrieved_at
        assert warm.lead_matches[0].metadata[0].value == "United States"
    asyncio.run(scenario())


def test_concurrent_resolutions_share_global_request_semaphore(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata(lead_ids=("one", "two", "three"), delay=0.005)
        workflow = Workflow(settings(tmp_path, cala_concurrency=2), provider)
        results = await asyncio.gather(*[
            workflow.resolve(ResolveRequest(profile=profile())) for _ in range(2)])
        assert all(all(item.metadata_status == "retrieved" for item in result.lead_matches)
                   for result in results)
        assert provider.state.max_active == 2 and provider.state.active == 0
    asyncio.run(scenario())


def test_optional_startup_search_failure_still_enriches_found_lead(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata(lead_ids=("lead",), startup_failure=True)
        resolved = await Workflow(settings(tmp_path), provider).resolve(
            ResolveRequest(profile=profile(), lookup_startup=True))
        assert resolved.startup_matches == []
        assert resolved.lead_matches[0].metadata_status == "retrieved"
        assert any("Optional startup lookup" in item for item in resolved.notices)
    asyncio.run(scenario())


def test_provider_without_metadata_operation_keeps_original_search_flow(tmp_path):
    class BasicProvider:
        async def search_entities(self, query, kind):
            return [Entity(id="lead", name="Basic lead")]

    async def scenario():
        resolved = await Workflow(settings(tmp_path), BasicProvider()).resolve(ResolveRequest(profile=profile()))
        assert resolved.lead_matches[0].name == "Basic lead"
        assert resolved.lead_matches[0].metadata_status == "not_requested"
        assert resolved.notices == []
    asyncio.run(scenario())


def test_no_allocation_skips_search_and_full_metadata_calls(tmp_path):
    async def scenario():
        provider = SyntheticNativeMetadata()
        resolved = await Workflow(settings(tmp_path), provider).resolve(
            ResolveRequest(profile=profile(allocated=True), lookup_startup=True))
        assert resolved.no_open_allocation and resolved.lead_matches == []
        assert resolved.startup_matches == [] and provider.state.calls == []
    asyncio.run(scenario())
