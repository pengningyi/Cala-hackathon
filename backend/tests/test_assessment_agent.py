"""Offline API-contract checks for the separate, evidence-bound assessment role."""
import asyncio
import json

import httpx
import pytest

from app import assessment_agent
from app.assessment_agent import ASSESSMENT_SYSTEM_MESSAGE, AssessmentAgent
from app.chat_model import SYSTEM_MESSAGE
from app.models import InvestorProfile
from app.semantic import SemanticError
from test_workflow import entity, founder, funding


@pytest.fixture
def assessment_input():
    investor = entity("investor-a")
    event = funding("round-a", [investor], company="company-a")
    return founder(), [InvestorProfile(investor=investor, rounds=[event])]


def assessment(source_urls=None, **updates):
    value = {
        "investor_id": "investor-a", "company_id": "company-a", "sector_fit": "same",
        "overlap": "none", "rationale": "Assessment from supplied company facts.",
        "source_urls": source_urls or [],
    }
    return {**value, **updates}


def install_http(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(assessment_agent.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


def provider(timeout=2):
    return AssessmentAgent("https://model.example.test/v1/", "test-key", "local-test-model", timeout)


def response(raw):
    return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})


def test_assessment_owns_role_prompt_and_preserves_input_and_sources(monkeypatch, assessment_input):
    owner, profiles = assessment_input
    source = profiles[0].rounds[0].sources[0]
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.url == "https://model.example.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        return response(json.dumps({"assessments": [assessment([str(source.url)])]}))

    install_http(monkeypatch, respond)
    before = owner.model_dump_json(), [item.model_dump_json() for item in profiles]
    updated = asyncio.run(provider().assess(owner, profiles))
    sent = requests[0]
    assert sent["messages"][0] == {"role": "system", "content": ASSESSMENT_SYSTEM_MESSAGE}
    assert sent["messages"][0]["content"] != SYSTEM_MESSAGE
    assert "no research" in sent["messages"][0]["content"]
    assert "Return unknown" in sent["messages"][0]["content"]
    assert "Do not generate scores" in sent["messages"][0]["content"]
    assert "tools" not in sent and sent["response_format"]["type"] == "json_schema"
    assert updated[0] is not profiles[0]
    assert updated[0].assessments[0].sources == [source]
    assert updated[0].assessments[0].sector_fit == "same"
    assert before == (owner.model_dump_json(), [item.model_dump_json() for item in profiles])


@pytest.mark.parametrize("violation", ["unknown_investor", "unknown_company", "duplicate", "source", "missing_source", "classification"])
def test_assessment_repairs_identity_duplicate_and_source_errors(monkeypatch, assessment_input, violation):
    owner, profiles = assessment_input
    url = str(profiles[0].rounds[0].sources[0].url)
    bad = assessment([url])
    if violation == "unknown_investor":
        bad["investor_id"] = "invented-investor"
    elif violation == "unknown_company":
        bad["company_id"] = "invented-company"
    elif violation == "source":
        bad["source_urls"] = ["https://invented.example.test/evidence"]
    elif violation == "missing_source":
        bad["source_urls"] = []
    elif violation == "classification":
        bad["sector_fit"] = "guaranteed_investment"
    invalid = [bad, bad] if violation == "duplicate" else [bad]
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        items = invalid if len(requests) == 1 else [assessment(sector_fit="unknown", overlap="unknown")]
        return response(json.dumps({"assessments": items}))

    install_http(monkeypatch, respond)
    updated = asyncio.run(provider().assess(owner, profiles))
    assert len(requests) == 2
    assert "failed identity, classification or source validation" in requests[1]["messages"][-1]["content"]
    assert updated[0].assessments[0].sector_fit == "unknown"
    assert updated[0].assessments[0].overlap == "unknown"
    assert updated[0].assessments[0].sources == []
    assert profiles[0].assessments == []


def test_assessment_json_object_fallback_retains_role_and_validation(monkeypatch, assessment_input):
    owner, profiles = assessment_input
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        items = [assessment(source_urls=["https://invented.example.test"])] if len(requests) == 2 else [
            assessment(sector_fit="unknown", overlap="unknown")]
        return response(json.dumps({"assessments": items}))

    install_http(monkeypatch, respond)
    updated = asyncio.run(provider().assess(owner, profiles))
    assert [item["response_format"]["type"] for item in requests] == ["json_schema", "json_object", "json_object"]
    for payload in requests[1:]:
        assert payload["messages"][0]["content"].startswith(ASSESSMENT_SYSTEM_MESSAGE)
        assert "JSON Schema:" in payload["messages"][0]["content"]
        assert "tools" not in payload
    assert updated[0].assessments[0].sector_fit == "unknown"


@pytest.mark.parametrize("raw,expected_calls", [("not json", 2), ("x" * 256_001, 1), (None, 1)])
def test_assessment_invalid_or_oversized_output_never_changes_profiles(monkeypatch, assessment_input, raw, expected_calls):
    owner, profiles = assessment_input
    requests = []

    def respond(request):
        requests.append(request)
        return response(raw)

    install_http(monkeypatch, respond)
    before = [item.model_dump_json() for item in profiles]
    with pytest.raises(SemanticError):
        asyncio.run(provider().assess(owner, profiles))
    assert len(requests) == expected_calls
    assert before == [item.model_dump_json() for item in profiles]


def test_assessment_timeout_cancels_model_work_without_mutation(monkeypatch, assessment_input):
    owner, profiles = assessment_input
    state = {"cancelled": False, "calls": 0}

    async def respond(request):
        state["calls"] += 1
        try:
            await asyncio.Event().wait()
        finally:
            state["cancelled"] = True

    install_http(monkeypatch, respond)
    before = [item.model_dump_json() for item in profiles]
    with pytest.raises(SemanticError):
        asyncio.run(provider(timeout=.01).assess(owner, profiles))
    assert state == {"cancelled": True, "calls": 1}
    assert before == [item.model_dump_json() for item in profiles]


def test_assessment_http_failure_is_sanitized(monkeypatch, assessment_input):
    owner, profiles = assessment_input
    install_http(monkeypatch, lambda request: httpx.Response(500, text="secret-provider-diagnostic"))
    with pytest.raises(SemanticError) as error:
        asyncio.run(provider().assess(owner, profiles))
    assert "secret-provider-diagnostic" not in str(error.value)
    assert "test-key" not in str(error.value)
