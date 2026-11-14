import asyncio
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.cala import CalaError, CalaMCPProvider, _project
from app.models import Entity, FounderInput, FundingRound, InvestorProfile, Source
from app.semantic import SemanticError, _apply, _facts


class Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.lists = 0

    async def list_tools(self, cursor=None):
        self.lists += 1
        return SimpleNamespace(tools=[SimpleNamespace(
            name="actual_search", description="test transport schema",
            inputSchema={"type": "object", "required": ["search"], "additionalProperties": False,
                         "properties": {"search": {"type": "string"}}},
        )], nextCursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(structuredContent=self.payload, isError=False)


def provider(tmp_path, session, mapping=None):
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(mapping or {"search_entities": {
        "tool": "actual_search", "arguments": {"search": "{query}"}, "response_path": "/entities",
    }}), encoding="utf-8")
    instance = CalaMCPProvider(endpoint="http://127.0.0.1:1/mcp", mapping_path=path)

    @asynccontextmanager
    async def local_session():
        yield session

    instance._session = local_session
    return instance


def test_no_configuration_fails_instead_of_empty_success():
    with pytest.raises(CalaError, match="CALA_MCP_URL"):
        asyncio.run(CalaMCPProvider().search_entities("Lead", "investor"))


def test_official_cala_api_key_and_optional_proxy_bearer_headers():
    instance = CalaMCPProvider(api_key="synthetic-cala-key")
    assert instance._headers() == {"X-API-KEY": "synthetic-cala-key"}
    instance = CalaMCPProvider(token="synthetic-proxy-token", api_key="synthetic-cala-key")
    assert instance._headers() == {"X-API-KEY": "synthetic-cala-key", "Authorization": "Bearer synthetic-proxy-token"}


def test_exact_schema_checked_args_and_metadata_cache(tmp_path):
    session = Session({"entities": [{"id": "lead", "name": "A lead", "sources": [{
        "url": "https://cala.test/source", "retrieved_at": "2000-01-01T00:00:00Z",
        "published_at": "2025-01-01",
    }]}]})
    instance = provider(tmp_path, session)

    async def run():
        first = await instance.search_entities("Exact founder query", "investor")
        await instance.search_entities("Second query", "investor")
        return first

    result = asyncio.run(run())
    assert session.lists == 1
    assert session.calls == [("actual_search", {"search": "Exact founder query"}),
                             ("actual_search", {"search": "Second query"})]
    assert result[0].sources[0].retrieved_at.year >= 2026
    assert result[0].sources[0].published_at.year == 2025


def test_schema_rejection_makes_no_data_call(tmp_path):
    session = Session({})
    instance = provider(tmp_path, session, {"search_entities": {
        "tool": "actual_search", "arguments": {"search": 42},
    }})
    with pytest.raises(CalaError) as error:
        asyncio.run(instance.search_entities("Lead", "investor"))
    assert error.value.code == "cala_arguments_invalid"
    assert session.calls == []


def test_missing_tool_and_bad_response_do_not_invent_entities(tmp_path):
    session = Session({"entities": [{"name": "Missing stable Cala ID"}]})
    instance = provider(tmp_path, session)
    with pytest.raises(CalaError) as error:
        asyncio.run(instance.search_entities("Lead", "investor"))
    assert error.value.code == "cala_response_invalid"


def test_projection_is_deterministic_and_preserves_typed_values():
    result = _project({"$each": "/data", "template": {
        "id": {"$path": "/stable_id"}, "name": {"$path": "/display_name"},
        "sources": [{"url": {"$path": "/url"}, "retrieved_at": {"$retrieved_at": True}}],
    }}, {"data": [{"stable_id": "123", "display_name": "Example", "url": "https://cala.test/123"}]}, "STAMP")
    assert result == [{"id": "123", "name": "Example", "sources": [{"url": "https://cala.test/123", "retrieved_at": "STAMP"}]}]


def test_real_streamable_http_sdk_transport(tmp_path):
    """Local test MCP server only; never substitutes for live Cala readiness."""
    import socket
    import time
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    server_mcp = FastMCP("test-only-cala-contract", stateless_http=True, json_response=True)

    @server_mcp.tool()
    def search_entities(search: str) -> dict:
        return {"entities": [{"id": "test-id", "name": search, "kind": "vc"}]}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(server_mcp.streamable_http_app(), log_level="error")
    server = uvicorn.Server(config)
    worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    worker.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.02)
        assert server.started
        path = tmp_path / "real-transport-map.json"
        path.write_text(json.dumps({"search_entities": {
            "tool": "search_entities", "arguments": {"search": "{query}"}, "response_path": "/entities",
        }}), encoding="utf-8")
        result = asyncio.run(CalaMCPProvider(
            endpoint=f"http://127.0.0.1:{port}/mcp", mapping_path=path,
        ).search_entities("Test lead", "investor"))
        assert result[0].id == "test-id"
        assert result[0].name == "Test lead"
    finally:
        server.should_exit = True
        worker.join(timeout=5)
        sock.close()


def test_semantic_rejects_hallucinated_ids_and_sources():
    source = Source(url="https://cala.test/company", retrieved_at=datetime.now(timezone.utc))
    profile = InvestorProfile(investor=Entity(id="investor", name="Investor"), rounds=[FundingRound(
        id="round", company=Entity(id="company", name="Company", description="A product", sources=[source]),
    )])
    allowed = {("investor", "company"): {str(source.url): source}}
    record = {"investor_id": "investor", "company_id": "company", "sector_fit": "same",
              "overlap": "unknown", "rationale": "Test inference", "source_urls": [str(source.url)]}
    assert _apply(json.dumps({"assessments": [record]}), [profile], allowed)[0].assessments[0].label == "LLM assessment"
    record["source_urls"] = ["https://invented.test/"]
    with pytest.raises(SemanticError):
        _apply(json.dumps({"assessments": [record]}), [profile], allowed)
    record.update(company_id="invented-company", source_urls=[])
    with pytest.raises(SemanticError):
        _apply(json.dumps({"assessments": [record]}), [profile], allowed)


def test_openai_compatible_transport_uses_runner_model_and_validates_output(monkeypatch):
    import httpx
    from app import semantic

    founder = FounderInput.model_validate({
        "startup": {"company_name": "Synthetic", "product_description": "Synthetic", "sector": "Synthetic",
                    "target_customer": "Synthetic", "problem_solved": "Synthetic", "country": "Finland"},
        "round": {"stage": "seed", "target_eur": "10", "firm_commitments_eur": "0", "lead_status": "conditional"},
        "lead_name": "Synthetic lead",
    })
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"assessments":[]}'}}]})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(semantic.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    provider = semantic.OpenAICompatibleSemantic(
        base_url="https://runner-model.example.test/v1", api_key="test-only-secret", model="runner-selected-model",
    )
    assert asyncio.run(provider.assess(founder, [])) == []
    assert str(requests[0].url) == "https://runner-model.example.test/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == "runner-selected-model"
    assert requests[0].headers["Authorization"] == "Bearer test-only-secret"


def test_openai_compatible_semantic_falls_back_to_json_object_mode(monkeypatch):
    """DeepSeek-style providers reject json_schema with 400; retry once in json_object mode."""
    import httpx
    from app import semantic

    founder = FounderInput.model_validate({
        "startup": {"company_name": "Synthetic", "product_description": "Synthetic", "sector": "Synthetic",
                    "target_customer": "Synthetic", "problem_solved": "Synthetic", "country": "Finland"},
        "round": {"stage": "seed", "target_eur": "10", "firm_commitments_eur": "0", "lead_status": "conditional"},
        "lead_name": "Synthetic lead",
    })
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if requests[-1]["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format type is unavailable"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"assessments":[]}'}}]})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(semantic.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    provider = semantic.OpenAICompatibleSemantic(
        base_url="https://api.deepseek.example.test", api_key="test-only-secret", model="deepseek-chat",
    )
    assert asyncio.run(provider.assess(founder, [])) == []
    assert [r["response_format"]["type"] for r in requests] == ["json_schema", "json_object"]
    assert requests[1]["messages"][0]["role"] == "system" and "json" in requests[1]["messages"][0]["content"]


def test_openai_compatible_semantic_does_not_retry_other_errors(monkeypatch):
    import httpx
    from app import semantic

    founder = FounderInput.model_validate({
        "startup": {"company_name": "Synthetic", "product_description": "Synthetic", "sector": "Synthetic",
                    "target_customer": "Synthetic", "problem_solved": "Synthetic", "country": "Finland"},
        "round": {"stage": "seed", "target_eur": "10", "firm_commitments_eur": "0", "lead_status": "conditional"},
        "lead_name": "Synthetic lead",
    })
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(semantic.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    provider = semantic.OpenAICompatibleSemantic(base_url="https://x.test", api_key="k", model="m")
    with pytest.raises(SemanticError):
        asyncio.run(provider.assess(founder, []))
    assert len(calls) == 1


def test_semantic_profile_window_prefers_recent_companies_before_bound():
    founder = FounderInput.model_validate({
        "startup": {"company_name": "Synthetic", "product_description": "Synthetic", "sector": "Synthetic",
                    "target_customer": "Synthetic", "problem_solved": "Synthetic", "country": "Finland"},
        "round": {"stage": "seed", "target_eur": "10", "firm_commitments_eur": "0", "lead_status": "conditional"},
        "lead_name": "Synthetic lead",
    })
    today = datetime.now(timezone.utc).date()
    events = [FundingRound(id=f"z-{index}", company=Entity(id=f"company-{index}", name=f"Company {index}"),
                           event_date=today - timedelta(days=index)) for index in range(21)]
    events.append(FundingRound(id="a-stale", company=Entity(id="stale", name="Stale"),
                               event_date=today - timedelta(days=1000)))
    _, allowed = _facts(founder, [InvestorProfile(investor=Entity(id="investor", name="Investor"), rounds=events)])
    assert len(allowed) == 20
    assert ("investor", "company-0") in allowed
    assert ("investor", "company-20") not in allowed
    assert ("investor", "stale") not in allowed
