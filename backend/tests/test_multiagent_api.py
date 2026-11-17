"""Default application routing executes separate chat/research roles offline."""
import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from app.assessment_agent import AssessmentAgent
from app.chat_agent import MULTI_AGENT_SYSTEM_MESSAGE, ChatAgent
from app.config import Settings
from app.main import create_app
from app.pipeline import RunRecord
from app.research_agent import RESEARCH_SYSTEM_MESSAGE
from app.semantic import OpenAICompatibleSemantic
from test_chat import saved_result  # noqa: F401 (fixture)
from test_chat_agent import FakeProvider, TOOL_URL, final, tool_source_id
from test_multiagent_chat import ScriptedTransport, delegate, observed_source, research_finish, search


def configured_settings(tmp_path, **overrides):
    return Settings(_env_file=None, model_provider="openai_compatible",
                    model_base_url="https://unused-model.example.test/v1", model_api_key="not-a-real-key",
                    model_name="scripted-test-model", cache_dir=tmp_path / "cache", cache_ttl_seconds=0,
                    **overrides)


def register_result(app, result):
    app.state.workflow.runs[result.run_id] = RunRecord(
        id=result.run_id, profile=result.profile, lead=result.lead,
        confirmed_at=result.confirmed_at, started=time.monotonic(), result=result,
    )


def parse_events(text):
    events = []
    for frame in text.split("\n\n"):
        lines = frame.splitlines()
        event = next((line[len("event: "):] for line in lines if line.startswith("event: ")), None)
        data = next((line[len("data: "):] for line in lines if line.startswith("data: ")), None)
        if event is not None and data is not None:
            events.append((event, json.loads(data)))
    return events


@pytest.mark.parametrize("route", ["chat", "chat/stream"])
def test_default_http_and_sse_routes_execute_distinct_chat_research_handoff(saved_result, tmp_path, monkeypatch, route):
    def reject_network(*args, **kwargs):
        raise AssertionError("The scripted API integration must not create a network client")

    monkeypatch.setattr(httpx, "AsyncClient", reject_network)
    provider = FakeProvider()
    config = configured_settings(tmp_path)
    assert config.multi_agents_enabled is True
    app = create_app(config, provider=provider)
    assert isinstance(app.state.chat, ChatAgent)
    assert type(app.state.workflow.semantic) is AssessmentAgent
    chat = ScriptedTransport([delegate(), lambda messages: final([observed_source(messages)])])
    researcher = ScriptedTransport([search(), lambda messages: research_finish([tool_source_id(messages)])])
    app.state.chat.transport = chat
    app.state.chat.research_transport = researcher
    register_result(app, saved_result)
    before = saved_result.model_dump_json()

    with TestClient(app) as client:
        response = client.post(f"/api/runs/{saved_result.run_id}/{route}", json={
            "message": "Who is Acme?", "history": [{"role": "user", "content": "PRIVATE_CHAT_TOPIC"}],
        })
        assert response.status_code == 200
        if route.endswith("stream"):
            assert response.headers["content-type"].startswith("text/event-stream")
            events = parse_events(response.text)
            assert not [payload for event, payload in events if event == "error"]
            steps = [payload for event, payload in events if event == "step"]
            assert [(step["tool"], step["status"], step["agent"]) for step in steps] == [
                ("request_research", "started", "research"), ("search_entities", "started", "research"),
                ("search_entities", "done", "research"), ("request_research", "done", "research"),
            ]
            answers = [payload for event, payload in events if event == "answer"]
            assert len(answers) == 1 and events[-1][0] == "answer"
            body = answers[0]
        else:
            body = response.json()

    assert set(body) == {"message", "citations", "citation_ids", "candidate_ids", "model_used", "tool_steps", "evidence_revision"}
    assert body["evidence_revision"] == 1
    assert body["model_used"] is True and [source["url"] for source in body["citations"]] == [TOOL_URL]
    assert len(body["citation_ids"]) == 1 and body["candidate_ids"] == []
    assert [(step["tool"], step["status"], step["agent"]) for step in body["tool_steps"]] == [
        ("request_research", "done", "research"), ("search_entities", "done", "research"),
    ]
    assert provider.calls == [("search_entities", "Acme", "investor")]
    assert len(chat.requests) == len(researcher.requests) == 2
    assert chat.requests[0]["messages"][0]["content"] == MULTI_AGENT_SYSTEM_MESSAGE
    assert researcher.requests[0]["messages"][0]["content"] == RESEARCH_SYSTEM_MESSAGE
    assert "PRIVATE_CHAT_TOPIC" in json.dumps(chat.requests[0]["messages"])
    assert "PRIVATE_CHAT_TOPIC" not in json.dumps(researcher.requests[0]["messages"])
    assert {tool["function"]["name"] for tool in chat.requests[0]["tools"]} == {"request_research", "final_answer"}
    assert "request_research" not in {tool["function"]["name"] for tool in researcher.requests[0]["tools"]}
    assert saved_result.model_dump_json() == before


@pytest.mark.parametrize("enabled,assessment_type", [(True, AssessmentAgent), (False, OpenAICompatibleSemantic)])
def test_multiagent_flag_selects_assessment_role_and_is_reported_in_health(tmp_path, enabled, assessment_type):
    provider = FakeProvider()
    app = create_app(configured_settings(tmp_path, multi_agents_enabled=enabled), provider=provider)
    assert type(app.state.workflow.semantic) is assessment_type
    assert isinstance(app.state.chat, ChatAgent)
    assert app.state.chat.settings.multi_agents_enabled is enabled
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["multi_agents_enabled"] is enabled
    assert response.json()["model_provider"] == "openai_compatible"
    assert provider.calls == []
