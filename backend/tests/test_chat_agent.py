"""Tool-calling chat agent with a scripted model and a fake Cala provider; no network."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.cala_native import CalaNativeProvider, ToolPayload
from app.chat_agent import AGENT_SYSTEM_MESSAGE, ChatAgent, tool_definitions
from app.chat_model import ChatModelError
from app.config import Settings
from app.main import create_app
from app.models import ChatRequest, Entity, FundingRound, Participation, Source
from app.pipeline import RunRecord
from app.runtime import ResponseCache
from test_chat import saved_result  # noqa: F401  (fixture)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
TOOL_URL = "https://news.example.test/acme-seed"


def settings(**overrides):
    return Settings(_env_file=None, **{"model_provider": "disabled", "cache_ttl_seconds": 0, **overrides})


class FakeProvider:
    configured = True

    def __init__(self):
        self.calls = []

    async def search_entities(self, query, kind):
        self.calls.append(("search_entities", query, kind))
        return [Entity(id="acme-capital", name="Acme Capital", kind="vc",
                       sources=[Source(url=TOOL_URL, retrieved_at=NOW, label="Example News")])]

    async def funding_round(self, event_id):
        self.calls.append(("funding_round", event_id))
        if event_id != "round-1":
            return None
        source = [Source(url=TOOL_URL, retrieved_at=NOW, label="Example News")]
        return FundingRound(id="round-1", company=Entity(id="acme-co", name="Acme Co", kind="company"),
                            stage="Seed", sources=source, participants=[
                                Participation(investor=Entity(id="fund-x", name="Fund X"), is_lead=True),
                                Participation(investor=Entity(id="fund-y", name="Fund Y"), is_lead=False)])


def call(identifier, name, **arguments):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def tool_turn(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


class ScriptedTransport:
    """Replays assistant turns; callables receive the conversation so far."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    async def complete(self, messages, tools, tool_choice="required"):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice})
        turn = self.turns.pop(0)
        return turn(messages) if callable(turn) else turn


def tool_source_id(messages):
    """The catalog ID the server assigned to the fake tool's source."""
    for message in reversed(messages):
        if message.get("role") == "tool" and "acme-capital" in message["content"]:
            content = json.loads(message["content"])
            identifier = content["result"][0]["source_ids"][0]
            assert content["sources"][identifier]["url"] == TOOL_URL
            return identifier
    raise AssertionError("tool result missing")


def final(source_ids=(), candidate_ids=(), message="Acme Capital appears in Cala (live Cala lookup)."):
    return tool_turn(call("final", "final_answer", message=message, source_ids=list(source_ids),
                          candidate_ids=list(candidate_ids)))


def agent(tmp_path, transport, provider=None, **overrides):
    workflow = SimpleNamespace(provider=provider or FakeProvider(), cache=ResponseCache(tmp_path, 0, "test"),
                               semaphore=asyncio.Semaphore(4))
    return ChatAgent(settings(**overrides), workflow, transport)


def test_tool_results_join_the_catalog_and_can_be_cited(saved_result, tmp_path):
    transport = ScriptedTransport([
        tool_turn(call("c1", "search_entities", query="Acme", kind="investor")),
        lambda messages: final([tool_source_id(messages)]),
    ])
    steps = []
    before = saved_result.model_dump_json()
    reply = asyncio.run(agent(tmp_path, transport).reply(saved_result, ChatRequest(message="Who is Acme?"),
                                                          on_step=steps.append))
    assert reply.model_used is True and [str(s.url) for s in reply.citations] == [TOOL_URL]
    assert [(s.status, s.tool) for s in steps] == [("started", "search_entities"), ("done", "search_entities")]
    assert reply.tool_steps[0].status == "done" and reply.tool_steps[0].count == 1
    assert "Acme" in reply.tool_steps[0].label
    assert transport.requests[0]["messages"][0]["content"] == AGENT_SYSTEM_MESSAGE
    assert saved_result.model_dump_json() == before


def test_invented_citation_gets_one_correction_then_fails(saved_result, tmp_path):
    bad = final(["s999"])
    transport = ScriptedTransport([bad, lambda m: final([])])
    reply = asyncio.run(agent(tmp_path, transport).reply(saved_result, ChatRequest(message="Why?")))
    assert reply.citations == []
    assert "rejected" in transport.requests[1]["messages"][-1]["content"]
    assert transport.requests[1]["tools"] == [t for t in transport.requests[1]["tools"] if t["function"]["name"] == "final_answer"]

    always_bad = ScriptedTransport([final(["s999"])] * 10)
    with pytest.raises(ChatModelError) as error:
        asyncio.run(agent(tmp_path, always_bad, chat_max_tool_rounds=1).reply(saved_result, ChatRequest(message="Why?")))
    assert error.value.code == "chat_invalid_output"


def test_unlisted_url_and_non_shortlist_candidate_are_rejected(saved_result, tmp_path):
    for turn in (final(message="See https://invented.example.test/"), final(candidate_ids=["acme-capital"])):
        transport = ScriptedTransport([turn] * 10)
        with pytest.raises(ChatModelError):
            asyncio.run(agent(tmp_path, transport, chat_max_tool_rounds=1).reply(saved_result, ChatRequest(message="Why?")))


def test_round_cap_forces_final_answer_only(saved_result, tmp_path):
    search = tool_turn(call("c", "search_entities", query="Acme", kind="investor"))
    transport = ScriptedTransport([search, search, final()])
    reply = asyncio.run(agent(tmp_path, transport, chat_max_tool_rounds=2).reply(saved_result, ChatRequest(message="Why?")))
    assert reply.message.startswith("Acme")
    forced = transport.requests[2]
    assert [t["function"]["name"] for t in forced["tools"]] == ["final_answer"]
    assert forced["tool_choice"] == {"type": "function", "function": {"name": "final_answer"}}
    assert transport.requests[0]["tool_choice"] == "required"


def test_cala_budget_exhaustion_is_reported_to_the_model_and_answer_still_returns(saved_result, tmp_path):
    provider = FakeProvider()
    transport = ScriptedTransport([
        tool_turn(call("a", "search_entities", query="Acme", kind="investor")),
        tool_turn(call("b", "search_entities", query="Other", kind="investor")),
        final(),
    ])
    reply = asyncio.run(agent(tmp_path, transport, provider, chat_cala_call_limit=1).reply(
        saved_result, ChatRequest(message="Why?")))
    assert len(provider.calls) == 1
    assert [s.status for s in reply.tool_steps] == ["done", "failed"]
    assert "budget" in transport.requests[2]["messages"][-1]["content"]


def test_invalid_tool_arguments_do_not_call_cala(saved_result, tmp_path):
    provider = FakeProvider()
    transport = ScriptedTransport([tool_turn(call("a", "search_entities", query="Acme", kind="planet")), final()])
    reply = asyncio.run(agent(tmp_path, transport, provider).reply(saved_result, ChatRequest(message="Why?")))
    assert provider.calls == [] and reply.tool_steps == []
    assert "Invalid arguments" in transport.requests[1]["messages"][-1]["content"]


def test_plain_text_reply_is_redirected_to_final_answer(saved_result, tmp_path):
    transport = ScriptedTransport([{"role": "assistant", "content": "Here is my answer"}, final()])
    reply = asyncio.run(agent(tmp_path, transport).reply(saved_result, ChatRequest(message="Why?")))
    assert reply.message.startswith("Acme") and transport.requests[1]["tool_choice"]["function"]["name"] == "final_answer"


def test_tools_are_limited_to_what_the_provider_implements():
    names = [tool["function"]["name"] for tool in tool_definitions(FakeProvider())]
    assert names == ["search_entities", "get_funding_round", "final_answer"]
    native = [tool["function"]["name"] for tool in tool_definitions(CalaNativeProvider())]
    assert {"get_investor_profile", "get_funding_round", "get_person_roles", "knowledge_search", "final_answer"} <= set(native)
    assert "knowledge_search" not in [t["function"]["name"] for t in tool_definitions(CalaNativeProvider(), False)]


def test_knowledge_search_is_opt_in(saved_result, tmp_path):
    for enabled in (False, True):
        transport = ScriptedTransport([final()])
        asyncio.run(agent(tmp_path, transport, CalaNativeProvider(), chat_knowledge_search=enabled).reply(
            saved_result, ChatRequest(message="Why?")))
        offered = [tool["function"]["name"] for tool in transport.requests[0]["tools"]]
        assert ("knowledge_search" in offered) is enabled


def test_funding_round_tool_returns_company_and_co_investors(saved_result, tmp_path):
    transport = ScriptedTransport([
        tool_turn(call("a", "get_funding_round", round_id="round-1"), call("b", "get_funding_round", round_id="nope")),
        final(),
    ])
    reply = asyncio.run(agent(tmp_path, transport).reply(saved_result, ChatRequest(message="Who co-invested?")))
    tool_messages = [m for m in transport.requests[1]["messages"] if m.get("role") == "tool"]
    found = json.loads(tool_messages[0]["content"])["result"]
    assert found["company"]["name"] == "Acme Co"
    assert [p["name"] for p in found["participants"]] == ["Fund X", "Fund Y"]
    assert "Not a readable funding round" in tool_messages[1]["content"]
    assert [s.count for s in reply.tool_steps] == [2, None]


def _app(saved_result, transport):
    config = settings(model_provider="openai_compatible", model_base_url="https://model.example.test",
                      model_api_key="key", model_name="model")
    app = create_app(config, provider=FakeProvider())
    assert isinstance(app.state.chat, ChatAgent)
    app.state.chat.transport = transport
    app.state.workflow.runs[saved_result.run_id] = RunRecord(
        id=saved_result.run_id, profile=saved_result.profile, lead=saved_result.lead,
        confirmed_at=saved_result.confirmed_at, started=0, result=saved_result)
    return app


def _events(body):
    frames = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append((lines["event"], json.loads(lines["data"])))
    return frames


def test_stream_endpoint_emits_steps_then_answer(saved_result):
    transport = ScriptedTransport([
        tool_turn(call("c1", "search_entities", query="Acme", kind="investor")),
        lambda messages: final([tool_source_id(messages)]),
    ])
    with TestClient(_app(saved_result, transport)) as client:
        response = client.post(f"/api/runs/{saved_result.run_id}/chat/stream", json={"message": "Who is Acme?"})
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        events = _events(response.text)
    assert [name for name, _ in events] == ["step", "step", "answer"]
    assert [data["status"] for _, data in events[:2]] == ["started", "done"]
    assert events[-1][1]["citations"][0]["url"] == TOOL_URL and events[-1][1]["tool_steps"]


def test_stream_endpoint_reports_errors_as_events_and_validates_up_front(saved_result):
    transport = ScriptedTransport([final(["s999"])] * 20)
    with TestClient(_app(saved_result, transport)) as client:
        route = f"/api/runs/{saved_result.run_id}/chat/stream"
        events = _events(client.post(route, json={"message": "Why?"}).text)
        assert events[-1][0] == "error" and events[-1][1]["code"] == "chat_invalid_output"
        assert client.post(route, json={"message": "Why?", "selected_investor_id": "other"}).status_code == 400
        assert client.post("/api/runs/missing/chat/stream", json={"message": "Why?"}).status_code == 404


def test_json_endpoint_still_returns_a_chat_response(saved_result):
    transport = ScriptedTransport([final()])
    with TestClient(_app(saved_result, transport)) as client:
        body = client.post(f"/api/runs/{saved_result.run_id}/chat", json={"message": "Why?"}).json()
    assert body["model_used"] is True and body["tool_steps"] == []


def test_agent_needs_configured_cala_otherwise_snapshot_chat_is_used():
    from app.chat_model import LiveResultChat

    class Unconfigured(FakeProvider):
        configured = False

    config = settings(model_provider="openai_compatible", model_base_url="https://model.example.test",
                      model_api_key="key", model_name="model")
    assert isinstance(create_app(config, provider=Unconfigured()).state.chat, LiveResultChat)
    off = config.model_copy(update={"chat_agent_enabled": False})
    assert isinstance(create_app(off, provider=FakeProvider()).state.chat, LiveResultChat)


def test_knowledge_search_uses_advertised_argument_and_keeps_sources():
    provider = CalaNativeProvider(api_key="key")
    seen = {}

    async def tools():
        return [{"name": "knowledge_search", "inputSchema": {"type": "object", "required": ["input"],
                                                              "properties": {"input": {"type": "string"}}}}]

    async def caller(tool, arguments):
        seen[tool] = arguments
        return ToolPayload(retrieved_at=NOW, data={"content": "Acme led the seed.", "explainability": [
            {"content": "x", "sources": [{"document": TOOL_URL, "name": "Example News", "date": "2025-03-01"}]}]})

    provider.discover_tools = tools
    bound = provider.with_tool_caller(caller)
    bound.discover_tools = tools
    answer = asyncio.run(bound.knowledge_search("Who led Acme's seed?"))
    assert seen == {"knowledge_search": {"input": "Who led Acme's seed?"}}
    assert answer.text == "Acme led the seed." and str(answer.sources[0].url) == TOOL_URL


def test_slow_tool_turn_past_the_lookup_window_still_yields_an_answer(saved_result, tmp_path):
    class Slow(ScriptedTransport):
        async def complete(self, messages, tools, tool_choice="required"):
            if tool_choice == "required":
                await asyncio.sleep(5)  # cut off by the ~1 s lookup window
            return await super().complete(messages, tools, tool_choice)

    transport = Slow([final()])
    # 31 s total minus the 30 s answer reserve leaves a 1 s lookup window.
    reply = asyncio.run(agent(tmp_path, transport, chat_timeout_seconds=31).reply(saved_result, ChatRequest(message="Why?")))
    assert reply.message.startswith("Acme")
    assert transport.requests[0]["tool_choice"]["function"]["name"] == "final_answer"


class WebsiteProvider(FakeProvider):
    async def search_entities(self, query, kind):
        self.calls.append(("search_entities", query, kind))
        return [Entity(id="kfund", name="K Fund", kind="vc", website="https://kfund.vc/",
                       sources=[Source(url=TOOL_URL, retrieved_at=NOW, label="Example News")])]


@pytest.mark.parametrize("url", ["https://kfund.vc/", "https://www.kfund.vc", "kfund.vc", "https://kfund.vc/."])
def test_entity_website_returned_by_cala_can_be_shared(saved_result, tmp_path, url):
    transport = ScriptedTransport([tool_turn(call("a", "search_entities", query="K Fund", kind="investor")),
                                   final(message=f"Reach K Fund through its website {url}")])
    reply = asyncio.run(agent(tmp_path, transport, WebsiteProvider()).reply(saved_result, ChatRequest(message="How do I contact K Fund?")))
    assert "kfund.vc" in reply.message


def test_rejection_feedback_names_the_exact_problem(saved_result, tmp_path):
    transport = ScriptedTransport([final(message="Email them via https://unknown.example.test/contact"),
                                   final(["s999"]), final()])
    reply = asyncio.run(agent(tmp_path, transport).reply(saved_result, ChatRequest(message="Contact?")))
    assert reply.message.startswith("Acme")
    assert "https://unknown.example.test/contact" in transport.requests[1]["messages"][-1]["content"]
    assert "s999" in transport.requests[2]["messages"][-1]["content"]


def test_prompt_excludes_trust_data_and_sets_concise_style(saved_result):
    from app.chat_model import build_messages
    messages, _ = build_messages(saved_result, ChatRequest(message="How do I contact them?"), system=AGENT_SYSTEM_MESSAGE)
    snapshot = json.loads(messages[1]["content"].split("\n", 1)[1])
    assert all("trust" not in card and "trust_loops" not in card for card in snapshot["snapshot"]["candidates"])
    assert "Never mention trust scores" in AGENT_SYSTEM_MESSAGE and "150 words" in AGENT_SYSTEM_MESSAGE
