"""Live-chat integration with injected model outputs; no paid or Cala calls."""
import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl

from app import chat_model
from app.chat_model import (
    ChatModelError, CodexChatTransport, LiveResultChat, OpenAICompatibleChatTransport, build_messages,
)
from app.config import Settings
from app.main import _connected_chat_reply, create_app
from app.models import AllocationItem, ChatRequest, Source
from app.pipeline import RunRecord, WorkflowError
from test_chat import saved_result


class Generator:
    def __init__(self, output=None):
        self.output = output
        self.messages = []

    async def generate(self, messages):
        self.messages.append(messages)
        return self.output or json.dumps({"message": "Generated from the saved evidence.",
                                          "source_ids": ["s1"], "candidate_ids": []})


def config(**overrides):
    return Settings(_env_file=None, model_provider="disabled", **overrides)


def app_with_result(saved_result, service=None, settings=None):
    class NoCallsProvider:
        configured = True

        async def search_entities(self, *args, **kwargs):
            raise AssertionError("Chat must not query Cala")

    app = create_app(settings or config(), provider=NoCallsProvider(), chat=service)
    app.state.workflow.runs[saved_result.run_id] = RunRecord(
        id=saved_result.run_id, profile=saved_result.profile, lead=saved_result.lead,
        confirmed_at=saved_result.confirmed_at, started=time.monotonic(), result=saved_result,
    )
    return app


def test_live_generation_uses_history_focus_snapshot_and_exact_source_metadata(saved_result):
    result = saved_result.model_copy(deep=True)
    result.allocation = [AllocationItem(investor_id=result.candidates[0].investor.id,
                                        name=result.candidates[0].investor.name, amount_eur="600000", percentage=100,
                                        basis="Equal-share heuristic")]
    selected = result.candidates[1].investor.id
    request = ChatRequest(message="What would you ask them next?", selected_investor_id=selected, history=[
        {"role": "user", "content": "Compare the first two investors"},
        {"role": "assistant", "content": "We discussed both investors."},
    ])
    messages, sources = build_messages(result, request)
    identifier = next(iter(sources))
    transport = Generator(json.dumps({"message": "A new generated follow-up answer.",
                                       "source_ids": [identifier, identifier], "candidate_ids": [selected]}))
    before = result.model_dump_json()
    reply = asyncio.run(LiveResultChat(config(), transport).reply(result, request))
    assert reply.model_used is True and reply.message == "A new generated follow-up answer."
    assert reply.candidate_ids == [selected] and reply.citations == [sources[identifier]]
    assert result.model_dump_json() == before
    sent = transport.messages[0]
    assert sent[0]["role"] == "system" and "never probability" in sent[0]["content"]
    assert sent[-3:-1] == [item.model_dump() for item in request.history]
    assert json.loads(sent[-1]["content"])["selected_investor_id"] == selected
    snapshot = json.loads(sent[1]["content"].split("\n", 1)[1])["snapshot"]
    assert snapshot["allocation"][0]["amount_eur"] == "600000"
    assert snapshot["candidates"][0]["fit_score"] == result.candidates[0].fit_score
    assert snapshot["candidates"][0]["shared_event_ids"][0] == "shared-event"
    assert snapshot["funding_events"][0]["id"] == "shared-event"


@pytest.mark.parametrize("output", [
    '{"message":"Invented reference", "source_ids":["not-a-source"], "candidate_ids":[]}',
    '{"message":"Invented investor", "source_ids":[], "candidate_ids":["not-a-candidate"]}',
    '{"message":" ", "source_ids":[], "candidate_ids":[]}',
    '{"message":"Extra field", "source_ids":[], "candidate_ids":[], "url":"https://invented.test"}',
    '{"message":"Bad types", "source_ids":"s1", "candidate_ids":[]}',
    '{"message":"Visit https://invented.example.test/", "source_ids":[], "candidate_ids":[]}',
    'Not JSON',
    'x' * 33_000,
])
def test_unvalidated_model_outputs_never_become_successful_answers(saved_result, output):
    with pytest.raises(ChatModelError) as error:
        asyncio.run(LiveResultChat(config(), Generator(output)).reply(saved_result, ChatRequest(message="Why?")))
    assert error.value.code == "chat_invalid_output" and error.value.status == 502


def test_default_http_chat_requires_model_and_does_not_silently_use_offline_answers(saved_result):
    app = app_with_result(saved_result)
    assert isinstance(app.state.chat, LiveResultChat)
    with TestClient(app) as client:
        response = client.post(f"/api/runs/{saved_result.run_id}/chat", json={"message": "Why this investor?"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "chat_disabled"
        assert "model_used" not in response.json()


def test_http_chat_returns_only_validated_live_response_and_checks_run_identity(saved_result):
    generator = Generator()
    app = app_with_result(saved_result, LiveResultChat(config(), generator))
    with TestClient(app) as client:
        route = f"/api/runs/{saved_result.run_id}/chat"
        response = client.post(route, json={"message": "Why?"})
        assert response.status_code == 200
        assert response.json()["model_used"] is True and response.json()["citations"]
        assert client.post(route, json={"message": "Why?", "selected_investor_id": "other"}).status_code == 400
        assert client.post("/api/runs/missing/chat", json={"message": "Why?"}).status_code == 404
        app.state.workflow.runs[saved_result.run_id].result = None
        assert client.post(route, json={"message": "Why?"}).status_code == 409
    assert len(generator.messages) == 1


def test_live_chat_bounds_concurrency_and_releases_slot_after_cancellation(saved_result):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class WaitingGenerator(Generator):
            async def generate(self, messages):
                started.set()
                await release.wait()
                return await super().generate(messages)

        service = LiveResultChat(config(chat_max_concurrency=1), WaitingGenerator())
        task = asyncio.create_task(service.reply(saved_result, ChatRequest(message="First")))
        await started.wait()
        with pytest.raises(ChatModelError) as busy:
            await service.reply(saved_result, ChatRequest(message="Second"))
        assert busy.value.status == 429 and busy.value.code == "chat_busy"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert (await service.reply(saved_result, ChatRequest(message="Retry"))).model_used

    asyncio.run(scenario())


def test_browser_disconnect_cancels_generation_and_releases_model_slot(saved_result):
    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()

        class WaitingGenerator:
            async def generate(self, messages):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        class DisconnectedRequest:
            async def is_disconnected(self):
                return started.is_set()

        service = LiveResultChat(config(chat_max_concurrency=1), WaitingGenerator())
        with pytest.raises(WorkflowError) as error:
            await _connected_chat_reply(service, saved_result, ChatRequest(message="Why?"), DisconnectedRequest())
        assert error.value.code == "chat_client_disconnected" and error.value.status == 499
        assert stopped.is_set() and not service._slots.locked()

    asyncio.run(scenario())


def test_snapshot_deduplicates_events_reserves_sources_and_accepts_full_history(saved_result):
    result = saved_result.model_copy(deep=True)
    first, second = result.candidates
    second.investor.sources = [Source(url="https://synthetic.example.test/second-investor",
                                      retrieved_at=result.finished_at)]
    first.sources = [Source(url=f"https://synthetic.example.test/first-{index}", retrieved_at=result.finished_at)
                     for index in range(100)]
    for card in result.candidates:
        card.shared_deals = card.shared_deals * 8
        card.profile.rounds = card.shared_deals[:6]
    request = ChatRequest(message="Compare them", history=[
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 4000} for index in range(8)
    ])
    messages, sources = build_messages(result, request)
    snapshot = json.loads(messages[1]["content"].split("\n", 1)[1])["snapshot"]
    assert len(snapshot["funding_events"]) == 1
    assert len(snapshot["candidates"][0]["shared_event_ids"]) == 8
    assert snapshot["candidates"][1]["investor"]["source_ids"]
    assert any(str(source.url).endswith("second-investor") for source in sources.values())
    assert sum(len(message["content"]) for message in messages) < chat_model.MAX_PROMPT_CHARACTERS


def test_missing_codex_is_an_explicit_retryable_error(saved_result, monkeypatch):
    monkeypatch.setattr(chat_model.shutil, "which", lambda _: None)
    with pytest.raises(ChatModelError) as error:
        asyncio.run(LiveResultChat(config(), CodexChatTransport()).reply(saved_result, ChatRequest(message="Why?")))
    assert error.value.code == "chat_provider_unavailable" and error.value.status == 503


def test_codex_uses_stdin_schema_read_only_isolation_and_filters_credentials(saved_result, monkeypatch):
    calls = []
    monkeypatch.setattr(chat_model.shutil, "which", lambda _: "codex-binary")
    monkeypatch.setenv("CALA_API_KEY", "test-only-cala-secret")
    monkeypatch.setenv("MODEL_API_KEY", "test-only-model-secret")
    monkeypatch.setenv("CODEX_HOME", "test-home-for-login")

    class Process:
        returncode = 0

        async def communicate(self, payload):
            calls[0]["payload"] = payload.decode("utf-8")
            Path(calls[0]["command"][calls[0]["command"].index("--output-last-message") + 1]).write_text(
                '{"message":"A live transport answer", "source_ids":[], "candidate_ids":[]}', encoding="utf-8")

    async def start(*command, **kwargs):
        calls.append({"command": command, **kwargs})
        return Process()

    monkeypatch.setattr(chat_model.asyncio, "create_subprocess_exec", start)
    reply = asyncio.run(LiveResultChat(config(), CodexChatTransport()).reply(saved_result, ChatRequest(message="Why?")))
    assert reply.model_used and reply.message == "A live transport answer"
    call = calls[0]
    assert "--ignore-user-config" in call["command"] and "--ephemeral" in call["command"]
    assert call["command"][call["command"].index("--sandbox") + 1] == "read-only"
    assert call["command"][-1] == "-" and "Why?" in call["payload"]
    assert all("test-only" not in part for part in call["command"])
    assert "CALA_API_KEY" not in call["env"] and "MODEL_API_KEY" not in call["env"]
    assert call["env"]["CODEX_HOME"] == "test-home-for-login"
    assert not Path(call["cwd"]).exists()


def test_codex_timeout_kills_process_cleans_temporary_directory_and_returns_no_fallback(saved_result, monkeypatch):
    calls = []
    monkeypatch.setattr(chat_model.shutil, "which", lambda _: "codex-binary")

    class Process:
        returncode = None
        killed = False

        async def communicate(self, payload):
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()

    async def start(*command, **kwargs):
        calls.append(kwargs)
        return process

    monkeypatch.setattr(chat_model.asyncio, "create_subprocess_exec", start)
    with pytest.raises(ChatModelError) as error:
        asyncio.run(LiveResultChat(config(chat_timeout_seconds=.01), CodexChatTransport()).reply(
            saved_result, ChatRequest(message="Why?")))
    assert error.value.status == 504 and error.value.code == "chat_timeout"
    assert process.killed and not Path(calls[0]["cwd"]).exists()


def test_openai_compatible_transport_uses_runner_configuration_and_real_history(saved_result, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "message": "A provider-generated answer", "source_ids": ["s1"], "candidate_ids": [],
        })}}]})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(chat_model.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    provider = OpenAICompatibleChatTransport("https://runner-model.example.test/v1/", "test-only-secret", "runner-model")
    request = ChatRequest(message="And the second investor?", history=[{"role": "user", "content": "Compare them"}])
    reply = asyncio.run(LiveResultChat(config(), provider).reply(saved_result, request))
    assert reply.model_used is True
    sent = requests[0]
    assert str(sent.url) == "https://runner-model.example.test/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer test-only-secret"
    payload = json.loads(sent.content)
    assert payload["model"] == "runner-model"
    assert payload["messages"][-2] == {"role": "user", "content": "Compare them"}
    assert payload["response_format"]["json_schema"]["strict"] is True



def test_model_api_falls_back_to_json_object_when_structured_outputs_are_rejected(saved_result, monkeypatch):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if requests[-1]["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "This response_format type is unavailable now"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "message": "A provider-generated answer", "source_ids": ["s1"], "candidate_ids": [],
        })}}]})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(chat_model.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    provider = OpenAICompatibleChatTransport("https://model.example.test/v1", "test-only-secret", "model")
    reply = asyncio.run(LiveResultChat(config(), provider).reply(saved_result, ChatRequest(message="Why?")))
    assert reply.model_used is True
    assert [r["response_format"]["type"] for r in requests] == ["json_schema", "json_object"]
    assert "JSON Schema" in requests[1]["messages"][0]["content"] and "max_tokens" in requests[1]

@pytest.mark.parametrize("timeout", [False, True])
def test_model_api_errors_are_sanitized_and_never_expose_response_or_credentials(saved_result, monkeypatch, timeout):
    def respond(request):
        if timeout:
            raise httpx.ReadTimeout("test-only-secret", request=request)
        return httpx.Response(401, json={"secret": "test-only-secret"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(chat_model.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs))
    provider = OpenAICompatibleChatTransport("https://model.example.test/v1", "test-only-secret", "model")
    with pytest.raises(ChatModelError) as error:
        asyncio.run(LiveResultChat(config(), provider).reply(saved_result, ChatRequest(message="Why?")))
    assert error.value.status == (504 if timeout else 503)
    assert "test-only-secret" not in error.value.message


def test_empty_result_still_supports_live_conversation_without_fake_sources(saved_result):
    result = saved_result.model_copy(update={"state": "no_open_allocation", "candidates": [], "lead": None})
    response = asyncio.run(LiveResultChat(config(), Generator(
        '{"message":"Your allocation is filled based on the saved round.","source_ids":[],"candidate_ids":[]}'
    )).reply(result, ChatRequest(message="What should I prepare next?")))
    assert response.model_used is True and not response.citations and not response.candidate_ids


def test_supplied_founder_website_can_appear_in_draft_without_becoming_verified_citation(saved_result):
    result = saved_result.model_copy(deep=True)
    result.profile.startup.website = HttpUrl("https://founder.example.test/")
    response = asyncio.run(LiveResultChat(config(), Generator(
        json.dumps({"message": "Our company website is https://founder.example.test.",
                    "source_ids": [], "candidate_ids": []})
    )).reply(result, ChatRequest(message="Include my company website in the introduction.")))
    assert response.model_used is True and "https://founder.example.test" in response.message
    assert response.citations == []
