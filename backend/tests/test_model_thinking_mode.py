"""Optional thinking extension stays explicit across all model request paths."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app import chat_model
from app.assessment_agent import AssessmentAgent
from app.chat_agent import ChatAgent
from app.chat_model import LiveResultChat, OpenAICompatibleChatTransport
from app.config import Settings
from app.main import create_app
from app.report_agent import ReportAgent
from app.research_agent import ResearchAgent
from app.semantic import OpenAICompatibleSemantic
from test_workflow import founder


def install_http(monkeypatch, respond):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(chat_model.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))


def assert_thinking(payloads, mode):
    for payload in payloads:
        if mode is None:
            assert "thinking" not in payload
        else:
            assert payload["thinking"] == {"type": mode}
        assert "extra_body" not in payload


@pytest.mark.parametrize("mode", [None, "disabled", "enabled"])
def test_chat_schema_and_json_fallback_keep_optional_top_level_thinking(monkeypatch, mode):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"message\":\"ok\"}"}}]})

    install_http(monkeypatch, respond)
    transport = OpenAICompatibleChatTransport("https://unused-model.example.test/v1", "test-key", "test-model",
                                              thinking_mode=mode)
    output = asyncio.run(transport.generate([{"role": "user", "content": "Return JSON."}]))
    assert json.loads(output) == {"message": "ok"}
    assert [payload["response_format"]["type"] for payload in payloads] == ["json_schema", "json_object"]
    assert_thinking(payloads, mode)


@pytest.mark.parametrize("mode", [None, "disabled", "enabled"])
@pytest.mark.parametrize("choice", ["required", {"type": "function", "function": {"name": "final_answer"}}])
def test_tool_turn_and_auto_fallback_keep_optional_thinking(monkeypatch, mode, choice):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, json={"error": "forced choice unsupported by this fake provider"})
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    install_http(monkeypatch, respond)
    transport = OpenAICompatibleChatTransport("https://unused-model.example.test/v1", "test-key", "test-model",
                                              thinking_mode=mode)
    tools = [{"type": "function", "function": {"name": "final_answer", "parameters": {"type": "object"}}}]
    message = asyncio.run(transport.complete([{"role": "user", "content": "Answer."}], tools, choice))
    assert message["content"] == "ok"
    assert [payload["tool_choice"] for payload in payloads] == [choice, "auto"]
    assert all(payload["tools"] == tools for payload in payloads)
    assert_thinking(payloads, mode)


@pytest.mark.parametrize("mode", [None, "disabled", "enabled"])
@pytest.mark.parametrize("provider_type", [OpenAICompatibleSemantic, AssessmentAgent])
def test_assessment_schema_json_fallback_and_repair_keep_optional_thinking(monkeypatch, mode, provider_type):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(400, json={"error": "json_schema unsupported"})
        raw = "invalid JSON" if provider_type is AssessmentAgent and len(payloads) == 2 else '{"assessments":[]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})

    install_http(monkeypatch, respond)
    provider = provider_type("https://unused-model.example.test/v1", "test-key", "test-model", thinking_mode=mode)
    assert asyncio.run(provider.assess(founder(), [])) == []
    assert [payload["response_format"]["type"] for payload in payloads] == (
        ["json_schema", "json_object", "json_object"] if provider_type is AssessmentAgent else ["json_schema", "json_object"]
    )
    assert_thinking(payloads, mode)


@pytest.mark.parametrize("mode", [None, "disabled", "enabled"])
@pytest.mark.parametrize("multi_agents", [True, False])
def test_settings_option_reaches_every_role_and_app_factory(tmp_path, mode, multi_agents):
    config = Settings(_env_file=None, model_provider="openai_compatible", model_name="test-model",
                      model_base_url="https://unused-model.example.test/v1", model_api_key="test-key",
                      model_thinking_mode=mode, multi_agents_enabled=multi_agents, cache_dir=tmp_path / "cache")
    roles = [ChatAgent(config, SimpleNamespace()), ResearchAgent(config), ReportAgent(config), LiveResultChat(config)]
    assert all(role.transport.thinking_mode == mode for role in roles)
    app = create_app(config, provider=SimpleNamespace(configured=True))
    assert app.state.chat.transport.thinking_mode == mode
    assert app.state.report.transport.thinking_mode == mode
    assert app.state.workflow.semantic.thinking_mode == mode
    expected = AssessmentAgent if multi_agents else OpenAICompatibleSemantic
    assert type(app.state.workflow.semantic) is expected


def test_thinking_setting_is_optional_and_rejects_unrecognized_modes(monkeypatch):
    monkeypatch.delenv("MODEL_THINKING_MODE", raising=False)
    assert Settings(_env_file=None).model_thinking_mode is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, model_thinking_mode="automatic")
