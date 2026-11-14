"""Identity matching precedes editable founder details and confirmed discovery."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.cala import CalaError
from app.config import Settings
from app.main import create_app
from app.models import (
    CompanyMatchRequest, ConfirmCompanyMatches, Entity, EntityMetadataProperty,
    FounderInput, GraphSnapshot, Source, StartRunRequest,
)
from app.onboarding import startup_prefill
from app.pipeline import Workflow, WorkflowError
from app.runtime import utcnow


def source(name="company"):
    return Source(url=f"https://synthetic.example.test/{name}", retrieved_at=utcnow())


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, model_provider="disabled", cache_dir=tmp_path / "cache",
                    cache_ttl_seconds=0, **kwargs)


class MatchingProvider:
    configured = True

    def __init__(self, *, fail=None, delay=0, metadata_delay=0, wrong_metadata=False):
        self.startup = Entity(id="startup", name="Canonical Startup", kind="company", sources=[source()],
                              website="https://startup.example.test/", country="Finland",
                              country_basis="headquarters_address", country_sources=[source("headquarters")],
                              metadata=[EntityMetadataProperty(name="website", value="https://startup.example.test/", sources=[source("website")]),
                                        EntityMetadataProperty(name="operates_in_industry", value=["SOFTWARE_AND_SERVICES"], sources=[source("industry")])])
        self.lead = Entity(id="lead", name="Canonical Lead", kind="vc", sources=[source("lead")])
        self.calls, self.fail = [], fail
        self.delay, self.metadata_delay, self.wrong_metadata = delay, metadata_delay, wrong_metadata
        self.active, self.peak = 0, 0

    async def search_entities(self, query, kind):
        self.calls.append(("search", query, kind))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            if self.fail == kind:
                raise CalaError("synthetic_unavailable", "Synthetic lookup unavailable")
            entity = self.startup if kind == "company" else self.lead
            return [entity.model_copy(deep=True)]
        finally:
            self.active -= 1

    async def entity_metadata(self, entity_id, company=False):
        self.calls.append(("metadata", entity_id, company))
        await asyncio.sleep(self.metadata_delay)
        entity = self.startup if company else self.lead
        return entity.model_copy(update={"id": "wrong"} if self.wrong_metadata else {}, deep=True)

    async def lead_graph(self, lead_id, lookback_months):
        self.calls.append(("graph", lead_id))
        return GraphSnapshot(rounds=[], complete=True)


def query():
    return CompanyMatchRequest(company_name="Startup Search", lead_name="Lead Search")


def choose():
    return ConfirmCompanyMatches(startup_id="startup", lead_id="lead")


def completed_profile(**changes):
    profile = FounderInput(startup={"company_name": "Canonical Startup", "cala_entity_id": "startup",
                                    "sector": "Founder reviewed sector"}, lead_name="Canonical Lead",
                           round={"stage": "seed", "target_eur": "4000000", "firm_commitments_eur": "0"})
    for field, value in changes.items():
        setattr(profile.startup, field, value)
    return profile


def test_names_search_in_parallel_without_round_or_arbitrary_identity_selection(tmp_path):
    async def scenario():
        provider = MatchingProvider(delay=.02)
        workflow = Workflow(settings(tmp_path), provider)
        matches = await workflow.match_companies(query())
        assert provider.peak == 2
        assert [entity.id for entity in matches.startup_matches] == ["startup"]
        assert [entity.id for entity in matches.lead_matches] == ["lead"]
        assert "profile" not in matches.model_dump()
        assert workflow.onboarding_matches[matches.id].confirmation is None
        assert not workflow.runs and not workflow.onboarding_confirmations
        assert all(call[0] in ("search", "metadata") for call in provider.calls)
    asyncio.run(scenario())


def test_confirmed_fields_have_specific_sources_and_unknown_round_fields_are_absent(tmp_path):
    async def scenario():
        provider = MatchingProvider()
        workflow = Workflow(settings(tmp_path), provider)
        matches = await workflow.match_companies(query())
        before = list(provider.calls)
        confirmed = workflow.confirm_companies(matches.id, choose())
        assert provider.calls == before and not workflow.runs
        assert confirmed.prefill.model_dump() == {
            "company_name": "Canonical Startup", "website": "https://startup.example.test/",
            "sector": "Software and services", "country": "Finland",
        }
        assert str(confirmed.prefill_sources["website"][0].url).endswith("/website")
        assert str(confirmed.prefill_sources["country"][0].url).endswith("/headquarters")
        assert str(confirmed.prefill_sources["sector"][0].url).endswith("/industry")
        assert set(type(confirmed.prefill).model_fields) == {"company_name", "website", "sector", "country"}
        assert workflow.confirm_companies(matches.id, choose()).resolution_id == confirmed.resolution_id
        # Neither returned object can rewrite stored identities.
        matches.startup_matches[0].name = "Mutated response"
        confirmed.startup.name = "Mutated confirmation"
        assert workflow.confirm_companies(matches.id, choose()).startup.name == "Canonical Startup"
    asyncio.run(scenario())


def test_unsourced_or_registration_country_and_description_do_not_fill_unknowns():
    entity = Entity(id="s", name="Known name", description="A Finnish AI startup raising a Series A of EUR 5 million.",
                    country="Finland", country_basis="registered_address", country_sources=[source()],
                    metadata=[EntityMetadataProperty(name="industry", value="AI", sources=[]),
                              EntityMetadataProperty(name="entity_type", value="Company", sources=[source()])])
    fields, refs = startup_prefill(entity)
    assert fields.model_dump() == {"company_name": "Known name", "website": "", "sector": "", "country": ""}
    assert refs["website"] == refs["sector"] == refs["country"] == []


def test_website_prefill_normalizes_bare_host_without_borrowing_other_field_sources():
    website_source, other_source = source("explicit-website"), source("other-field")
    entity = Entity(id="startup", name="Known company", website="https://cala.ai/", sources=[other_source],
                    metadata=[EntityMetadataProperty(name="website", value="cala.ai", sources=[website_source]),
                              EntityMetadataProperty(name="homepage_url", value="https://other.example.test/", sources=[other_source])])
    prefill, refs = startup_prefill(entity)
    assert prefill.website == "https://cala.ai/"
    assert refs["website"] == [website_source]
    assert other_source not in refs["website"]


@pytest.mark.parametrize("selection,code", [
    ({"startup_id": "missing", "lead_id": "lead"}, "invalid_startup_selection"),
    ({"startup_id": "startup", "lead_id": "missing"}, "invalid_lead_selection"),
])
def test_confirmation_rejects_ids_not_offered_by_lookup(tmp_path, selection, code):
    async def scenario():
        workflow = Workflow(settings(tmp_path), MatchingProvider())
        matches = await workflow.match_companies(query())
        with pytest.raises(WorkflowError) as error:
            workflow.confirm_companies(matches.id, ConfirmCompanyMatches(**selection))
        assert error.value.code == code and not workflow.onboarding_confirmations
    asyncio.run(scenario())


@pytest.mark.parametrize("changed,lead_id,lead_name,code", [
    ({"cala_entity_id": None}, "lead", "Canonical Lead", "confirmed_startup_changed"),
    ({"cala_entity_id": "other"}, "lead", "Canonical Lead", "confirmed_startup_changed"),
    ({"company_name": "Other Company"}, "lead", "Canonical Lead", "confirmed_startup_changed"),
    ({}, "other", "Canonical Lead", "confirmed_lead_changed"),
    ({}, "lead", "Other Lead", "confirmed_lead_changed"),
])
def test_start_rejects_confirmed_identity_tampering_even_for_filled_round(tmp_path, changed, lead_id, lead_name, code):
    async def scenario():
        provider = MatchingProvider()
        workflow = Workflow(settings(tmp_path), provider)
        matches = await workflow.match_companies(query())
        confirmed = workflow.confirm_companies(matches.id, choose())
        profile = completed_profile(**changed)
        profile.lead_name = lead_name
        profile.round.firm_commitments_eur = profile.round.target_eur
        with pytest.raises(WorkflowError) as error:
            workflow.start(StartRunRequest(resolution_id=confirmed.resolution_id, lead_id=lead_id,
                                           profile_confirmed=True, profile=profile))
        assert error.value.code == code and not workflow.runs
        assert not any(call[0] == "graph" for call in provider.calls)
    asyncio.run(scenario())


def test_completed_reviewed_fields_start_discovery_only_after_explicit_submission(tmp_path):
    async def scenario():
        provider = MatchingProvider()
        workflow = Workflow(settings(tmp_path), provider)
        matches = await workflow.match_companies(query())
        with pytest.raises(WorkflowError):
            workflow.start(StartRunRequest(resolution_id=matches.id, lead_id="lead", profile_confirmed=True, profile=completed_profile()))
        confirmed = workflow.confirm_companies(matches.id, choose())
        profile = completed_profile(website="https://edited.example.test/", country="Sweden")
        status = workflow.start(StartRunRequest(resolution_id=confirmed.resolution_id, lead_id="lead",
                                                profile_confirmed=True, profile=profile))
        await workflow.get(status.id).task
        result = workflow.get(status.id).result
        assert result.profile.startup.sector == "Founder reviewed sector"
        assert result.profile.startup.country == "Sweden"
        assert any(call[0] == "graph" for call in provider.calls)
        assert result.error is None
    asyncio.run(scenario())


def test_expired_lookup_and_confirmation_cannot_be_reused(tmp_path):
    async def scenario():
        workflow = Workflow(settings(tmp_path), MatchingProvider())
        matches = await workflow.match_companies(query())
        confirmed = workflow.confirm_companies(matches.id, choose())
        workflow.onboarding_matches[matches.id].matches.expires_at = utcnow() - timedelta(seconds=1)
        with pytest.raises(WorkflowError) as error:
            workflow.confirm_companies(matches.id, choose())
        assert error.value.code == "matches_not_found"
        with pytest.raises(WorkflowError) as error:
            workflow.start(StartRunRequest(resolution_id=confirmed.resolution_id, lead_id="lead", profile_confirmed=True, profile=completed_profile()))
        assert error.value.code == "resolution_not_found"
        assert not workflow.onboarding_matches and not workflow.onboarding_confirmations
    asyncio.run(scenario())


def test_selecting_a_different_offered_company_invalidates_prior_confirmation(tmp_path):
    async def scenario():
        class MultipleCompanies(MatchingProvider):
            async def search_entities(self, query, kind):
                matches = await super().search_entities(query, kind)
                return [*matches, Entity(id="alternate", name="Alternate Startup", kind="company")] if kind == "company" else matches

        workflow = Workflow(settings(tmp_path), MultipleCompanies())
        matches = await workflow.match_companies(query())
        first = workflow.confirm_companies(matches.id, choose())
        second = workflow.confirm_companies(matches.id, ConfirmCompanyMatches(startup_id="alternate", lead_id="lead"))
        assert first.resolution_id != second.resolution_id
        assert first.resolution_id not in workflow.onboarding_confirmations
        with pytest.raises(WorkflowError) as error:
            workflow.start(StartRunRequest(resolution_id=first.resolution_id, lead_id="lead", profile_confirmed=True, profile=completed_profile()))
        assert error.value.code == "resolution_not_found"
        assert second.startup.id == "alternate"
    asyncio.run(scenario())


def test_partial_lookup_and_failed_optional_metadata_keep_available_matches(tmp_path):
    async def scenario():
        provider = MatchingProvider(fail="investor", wrong_metadata=True)
        workflow = Workflow(settings(tmp_path), provider)
        matches = await workflow.match_companies(query())
        assert matches.startup_matches[0].id == "startup"
        assert matches.startup_matches[0].metadata_status == "unavailable"
        assert not matches.lead_matches and matches.notices
    asyncio.run(scenario())


def test_lookup_capacity_cancellation_and_metadata_deadline_release_resources(tmp_path):
    async def scenario():
        provider = MatchingProvider(delay=10)
        workflow = Workflow(settings(tmp_path, max_stored_runs=1), provider)
        pending = asyncio.create_task(workflow.match_companies(query()))
        await asyncio.sleep(.02)
        with pytest.raises(WorkflowError) as error:
            await workflow.match_companies(query())
        assert error.value.status == 429
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert workflow._pending_identity_lookups == 0 and not workflow.onboarding_matches
        assert provider.active == 0
        provider.delay, provider.metadata_delay = 0, 1
        workflow.settings.cala_request_timeout_seconds = .05
        started = asyncio.get_running_loop().time()
        matches = await workflow.match_companies(query())
        assert asyncio.get_running_loop().time() - started < .5
        assert matches.startup_matches and matches.lead_matches
        assert all(item.metadata_status == "unavailable" for item in matches.startup_matches + matches.lead_matches)
    asyncio.run(scenario())


def test_identity_first_http_contract_does_not_require_placeholder_profile(tmp_path):
    provider = MatchingProvider()
    app = create_app(settings(tmp_path), provider=provider)
    with TestClient(app) as client:
        for payload in ({"company_name": "", "lead_name": "Lead"}, {"company_name": "Company"},
                        {"company_name": "Company", "lead_name": "Lead", "round": {}}):
            assert client.post("/api/onboarding/matches", json=payload).status_code == 422
        response = client.post("/api/onboarding/matches", json=query().model_dump())
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"id", "startup_matches", "lead_matches", "resolver_ms", "created_at", "expires_at", "notices"}
        response = client.post(f"/api/onboarding/matches/{body['id']}/confirm", json=choose().model_dump())
        assert response.status_code == 200
        confirmed = response.json()
        assert confirmed["prefill"]["sector"] == "Software and services"
        assert confirmed["prefill_sources"]["sector"]
        assert not app.state.workflow.runs
        assert client.post("/api/onboarding/matches/missing/confirm", json=choose().model_dump()).status_code == 404


def test_unconfigured_cala_is_an_explicit_error_instead_of_empty_company_matches(tmp_path):
    class UnavailableProvider(MatchingProvider):
        async def search_entities(self, query, kind):
            raise CalaError("cala_not_configured", "Configure Cala")

    app = create_app(settings(tmp_path), provider=UnavailableProvider())
    with TestClient(app) as client:
        response = client.post("/api/onboarding/matches", json=query().model_dump())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "cala_not_configured"
        assert not app.state.workflow.onboarding_matches
        assert app.state.workflow._pending_identity_lookups == 0
