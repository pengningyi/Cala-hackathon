"""Onboarding identity lookup through Cala REST POST /v1/knowledge/query."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import cala_rest
from app.cala import CalaError
from app.cala_rest import CalaKnowledgeResolver, entities_from_query, query_name
from app.config import Settings
from app.models import CompanyMatchRequest, ConfirmCompanyMatches
from app.pipeline import Workflow
from app.runtime import utcnow

STARTUP_RESPONSE = {
    "results": [{"name": "Cala AI", "website": "https://www.cala.ai/",
                 "industry": "Software & Services; Internet & Web Services (AI infrastructure)",
                 "country": "Spain", "headquarters": "Barcelona, Spain",
                 "description": "Verified knowledge graph for AI agents.", "investor_type": None}],
    "entities": [{"id": "81cfdabc", "name": "Cala AI", "entity_type": "Company", "mentions": ["Cala AI"]},
                 {"id": "spain", "name": "Spain", "entity_type": "Country", "mentions": ["Spain"]}],
}
LEAD_RESPONSE = {
    "results": [{"name": "Lightspeed Venture Partners", "website": "lsvp.com", "industry": "Venture Capital",
                 "country": "United States (headquartered in Menlo Park, California)",
                 "headquarters": "Menlo Park, CA", "description": "A venture capital firm.",
                 "investor_type": "Venture Capital"}],
    "entities": [{"id": "5a9d0c1c", "name": "Lightspeed Venture Partners", "entity_type": "Company",
                  "mentions": ["Lightspeed Venture Partners", "Lightspeed"]}],
}


def settings(tmp_path):
    return Settings(_env_file=None, model_provider="disabled", cache_dir=tmp_path / "cache", cache_ttl_seconds=0)


def mock_cala(monkeypatch, respond):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(cala_rest.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))


def test_rows_are_paired_with_cala_ids_and_carry_sourced_metadata():
    startup = entities_from_query(STARTUP_RESPONSE, utcnow(), company=True)
    assert [(e.id, e.kind, e.country) for e in startup] == [("81cfdabc", "company", "Spain")]
    assert str(startup[0].website) == "https://www.cala.ai/" and startup[0].metadata_status == "retrieved"
    industry = next(p for p in startup[0].metadata if p.name == "industry")
    assert industry.value == ["Software & Services", "Internet & Web Services (AI infrastructure)"] and industry.sources
    lead = entities_from_query(LEAD_RESPONSE, utcnow(), company=False)[0]
    assert (lead.id, lead.kind, lead.country) == ("5a9d0c1c", "vc", "United States")
    assert str(lead.website) == "https://lsvp.com/" and lead.aliases == ["Lightspeed"]


def test_only_top_three_identified_rows_are_kept():
    rows = [{"name": f"Fund {i}"} for i in range(6)] + [{"name": "Unidentified"}]
    refs = [{"id": f"id{i}", "name": f"Fund {i}", "entity_type": "Organization"} for i in range(6)]
    found = entities_from_query({"results": rows, "entities": refs}, utcnow(), company=False)
    assert [e.id for e in found] == ["id0", "id1", "id2"]


def test_query_uses_knowledge_query_with_sanitised_name_and_limit_three(monkeypatch):
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json=STARTUP_RESPONSE)

    mock_cala(monkeypatch, respond)
    resolver = CalaKnowledgeResolver("test-only-key", "https://cala.example.test")
    asyncio.run(resolver.knowledge_query_entities("Cala.ai (Spain)", "company"))
    request = sent[0]
    assert request.method == "POST" and str(request.url) == "https://cala.example.test/v1/knowledge/query"
    assert request.headers["x-api-key"] == "test-only-key"
    body = json.loads(request.content)
    assert body["return_entities"] is True
    assert body["input"].startswith("companies.name=Cala ai Spain.limit=3.return(")
    assert query_name("A.B=C(D)") == "A B C D"


@pytest.mark.parametrize("status, transient", [(429, True), (503, True), (401, False), (400, False)])
def test_http_errors_become_cala_errors(monkeypatch, status, transient):
    mock_cala(monkeypatch, lambda request: httpx.Response(status, json={"secret": "test-only-key"}))
    with pytest.raises(CalaError) as error:
        asyncio.run(CalaKnowledgeResolver("test-only-key").knowledge_query_entities("Cala AI", "company"))
    assert error.value.transient is transient and "test-only-key" not in error.value.message


class FailingMCP:
    """Any MCP call during onboarding lookup is a test failure."""
    configured = True

    def __getattr__(self, name):
        raise AssertionError(f"MCP provider was called: {name}")


def test_confirm_matches_uses_rest_only_and_prefills_from_query(tmp_path, monkeypatch):
    queries = []

    def respond(request):
        text = json.loads(request.content)["input"]
        queries.append(text)
        return httpx.Response(200, json=STARTUP_RESPONSE if "Cala AI" in text else LEAD_RESPONSE)

    mock_cala(monkeypatch, respond)
    workflow = Workflow(settings(tmp_path), FailingMCP(), identity=CalaKnowledgeResolver("test-only-key"))
    matches = asyncio.run(workflow.match_companies(
        CompanyMatchRequest(company_name="Cala AI", lead_name="Lightspeed Venture Partners")))
    assert len(queries) == 2 and all(".limit=3." in q for q in queries)
    assert [e.id for e in matches.startup_matches] == ["81cfdabc"]
    assert [e.id for e in matches.lead_matches] == ["5a9d0c1c"]
    assert not any("metadata was unavailable" in notice for notice in matches.notices)
    confirmation = workflow.confirm_companies(matches.id, ConfirmCompanyMatches(startup_id="81cfdabc", lead_id="5a9d0c1c"))
    prefill = confirmation.prefill
    assert prefill.website == "https://www.cala.ai/" and prefill.country == "Spain"
    assert prefill.sector == "Software & Services, Internet & Web Services (AI infrastructure)"
    assert all(confirmation.prefill_sources[field] for field in ("website", "sector", "country"))
