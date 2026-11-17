"""Real chat/research handoffs with separate scripted models and fake Cala data."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import app.chat_agent as chat_module
from app.chat_agent import AGENT_SYSTEM_MESSAGE, MULTI_AGENT_SYSTEM_MESSAGE, ChatAgent
from app.chat_model import ChatModelError
from app.models import ChatRequest, Entity, Source
from app.research_agent import RESEARCH_SYSTEM_MESSAGE
from app.runtime import ResponseCache
from test_chat import saved_result  # noqa: F401 (fixture)
from test_chat_agent import FakeProvider, NOW, TOOL_URL, call, final, settings, tool_source_id, tool_turn
from test_native_budget import SyntheticNativeProvider


class ScriptedTransport:
    """Keep independent immutable histories for each specialist."""
    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    async def complete(self, messages, tools, tool_choice="required"):
        self.requests.append(deepcopy({"messages": messages, "tools": tools, "tool_choice": tool_choice}))
        reply = self.turns.pop(0)
        return reply(messages) if callable(reply) else reply


def delegate(question="Find Acme Capital's sourced identity.", entity_ids=(), identifier="delegate"):
    return tool_turn(call(identifier, "request_research", question=question, entity_ids=list(entity_ids)))


def research_finish(source_ids=(), text="Acme Capital appears in Cala.", unknowns=(), coverage=None):
    findings = [{"text": text, "source_ids": list(source_ids)}] if source_ids else []
    return tool_turn(call("research-done", "finish_research", findings=findings,
                          unknowns=list(unknowns), coverage=coverage or ("sufficient" if findings else "partial")))


def search(identifier="lookup", query="Acme"):
    return tool_turn(call(identifier, "search_entities", query=query, kind="investor"))


def handoff(messages):
    for message in reversed(messages):
        if message.get("role") == "tool":
            payload = json.loads(message["content"])
            if "research" in payload or "observations" in payload:
                return payload
    raise AssertionError("Research handoff missing")


def observed_source(messages):
    observation = handoff(messages)["observations"][0]
    identifier = observation["result"][0]["source_ids"][0]
    assert observation["sources"][identifier]["url"] == TOOL_URL
    return identifier


def multi_agent(tmp_path, chat, researcher, provider=None, **overrides):
    config = settings(**{"multi_agents_enabled": True, **overrides})
    workflow = SimpleNamespace(provider=provider or FakeProvider(), cache=ResponseCache(tmp_path, 0, "multi-agent"),
                               semaphore=asyncio.Semaphore(4))
    return ChatAgent(config, workflow, transport=chat, research_transport=researcher)


def test_distinct_models_exchange_typed_evidence_and_keep_result_immutable(saved_result, tmp_path):
    chat = ScriptedTransport([delegate(), lambda messages: final([observed_source(messages)])])
    researcher = ScriptedTransport([search(), lambda messages: research_finish([tool_source_id(messages)])])
    provider = FakeProvider()
    service = multi_agent(tmp_path, chat, researcher, provider)
    before = saved_result.model_dump_json()
    steps = []
    request = ChatRequest(message="Who is Acme?", history=[{"role": "user", "content": "CHAT_PRIVATE_CONTEXT"}])
    reply = asyncio.run(service.reply(saved_result, request, on_step=steps.append))

    assert reply.model_used is True and [str(source.url) for source in reply.citations] == [TOOL_URL]
    assert chat.requests[0]["messages"][0]["content"] == MULTI_AGENT_SYSTEM_MESSAGE
    assert researcher.requests[0]["messages"][0]["content"] == RESEARCH_SYSTEM_MESSAGE
    assert "CHAT_PRIVATE_CONTEXT" in json.dumps(chat.requests[0]["messages"])
    assert "CHAT_PRIVATE_CONTEXT" not in json.dumps(researcher.requests[0]["messages"])
    assert {tool["function"]["name"] for tool in chat.requests[0]["tools"]} == {"request_research", "final_answer"}
    assert {tool["function"]["name"] for tool in researcher.requests[0]["tools"]} == {
        "search_entities", "get_funding_round", "finish_research",
    }
    packet = handoff(chat.requests[1]["messages"])
    assert packet["research"]["findings"][0]["source_ids"] == reply.citation_ids
    assert packet["observations"][0]["tool"] == "search_entities"
    assert provider.calls == [("search_entities", "Acme", "investor")]
    assert [(step.tool, step.status, step.agent) for step in reply.tool_steps] == [
        ("request_research", "done", "research"), ("search_entities", "done", "research"),
    ]
    assert [(step.tool, step.status) for step in steps] == [
        ("request_research", "started"), ("search_entities", "started"),
        ("search_entities", "done"), ("request_research", "done"),
    ]
    assert saved_result.model_dump_json() == before


def test_rejected_researcher_citations_are_not_promoted_to_chat_facts(saved_result, tmp_path):
    chat = ScriptedTransport([delegate(), final(message="The requested facts remain unknown.")])
    researcher = ScriptedTransport([research_finish(["s999"], text="UNSUPPORTED_GENERATED_CLAIM")] * 3)
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher).reply(saved_result, ChatRequest(message="Who is Acme?")))

    assert reply.message == "The requested facts remain unknown." and reply.citations == []
    packet = handoff(chat.requests[1]["messages"])
    assert "error" in packet and "research" not in packet and packet["observations"] == []
    assert "UNSUPPORTED_GENERATED_CLAIM" not in json.dumps(packet)
    assert reply.tool_steps[0].status == "failed" and len(researcher.requests) == 3
    assert [tool["function"]["name"] for tool in chat.requests[1]["tools"]] == ["final_answer"]


def test_multiple_delegations_share_one_raw_cala_call_budget(saved_result, tmp_path):
    chat = ScriptedTransport([
        delegate("Read Acme's sourced identity.", identifier="first"),
        delegate("Read Other Capital's sourced identity.", identifier="second"),
        final(message="The second lookup is unknown because its retrieval budget was exhausted."),
    ])
    researcher = ScriptedTransport([
        search(query="Acme"), lambda messages: research_finish([tool_source_id(messages)]),
        search(query="Other"), research_finish(unknowns=["The shared Cala budget prevented the second lookup."]),
    ])
    provider = FakeProvider()
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, provider, chat_cala_call_limit=1).reply(
        saved_result, ChatRequest(message="Compare these two identities.")))

    assert provider.calls == [("search_entities", "Acme", "investor")]
    assert [(step.tool, step.status) for step in reply.tool_steps] == [
        ("request_research", "done"), ("search_entities", "done"),
        ("request_research", "done"), ("search_entities", "failed"),
    ]
    failed_tool_message = researcher.requests[3]["messages"][-1]["content"]
    assert "budget" in failed_tool_message
    assert handoff(chat.requests[2]["messages"])["research"]["coverage"] == "partial"


def test_native_subcalls_share_budget_across_research_delegations(saved_result, tmp_path):
    class NativeSearchProvider(SyntheticNativeProvider):
        configured = True

        async def search_entities(self, query, kind):
            caller = self._caller or self.call_data_tool
            await caller("name_index", {"query": query, "kind": kind})
            await caller("entity_details", {"query": query, "kind": kind})
            return [Entity(id="acme-capital", name="Acme Capital", kind="vc",
                           sources=[Source(url=TOOL_URL, retrieved_at=NOW, label="Example News")])]

    provider = NativeSearchProvider()
    chat = ScriptedTransport([
        delegate("Read Acme.", identifier="first"), delegate("Read Other.", identifier="second"),
        final(message="Only the first identity lookup completed; the other remains unknown."),
    ])
    researcher = ScriptedTransport([
        search(query="Acme"), lambda messages: research_finish([tool_source_id(messages)]),
        search(query="Other"), research_finish(unknowns=["The second native join exhausted its raw-call budget."]),
    ])
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, provider, chat_cala_call_limit=3).reply(
        saved_result, ChatRequest(message="Read both identities.")))

    # Each semantic search performs two native calls. The second cannot reset the
    # shared budget: its first call is charged and its second is refused.
    assert provider.state.calls == [
        ("name_index", {"query": "Acme", "kind": "investor"}),
        ("entity_details", {"query": "Acme", "kind": "investor"}),
        ("name_index", {"query": "Other", "kind": "investor"}),
    ]
    assert [(step.tool, step.status) for step in reply.tool_steps if step.tool == "search_entities"] == [
        ("search_entities", "done"), ("search_entities", "failed"),
    ]


def test_shared_model_budget_keeps_one_final_chat_call_even_when_research_cannot_finish(saved_result, tmp_path):
    chat = ScriptedTransport([delegate(), lambda messages: final([observed_source(messages)])])
    researcher = ScriptedTransport([search(), research_finish(["never-reached"])])
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, agent_model_call_limit=3).reply(
        saved_result, ChatRequest(message="Who is Acme?")))

    assert len(chat.requests) + len(researcher.requests) == 3
    assert len(researcher.requests) == 1 and len(researcher.turns) == 1
    assert [tool["function"]["name"] for tool in chat.requests[-1]["tools"]] == ["final_answer"]
    assert "error" in handoff(chat.requests[-1]["messages"])
    assert [str(source.url) for source in reply.citations] == [TOOL_URL]
    assert reply.tool_steps[0].status == "failed"


def test_repeated_research_task_reuses_its_existing_handoff_without_rerunning(saved_result, tmp_path):
    request = delegate()
    chat = ScriptedTransport([request, request, final(message="The requested fact is still unknown.")])
    researcher = ScriptedTransport([research_finish(unknowns=["No supported fact is available."])])
    provider = FakeProvider()
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, provider).reply(
        saved_result, ChatRequest(message="Investigate this question.")))

    assert len(researcher.requests) == 1 and provider.calls == []
    repeated_error = json.loads(chat.requests[2]["messages"][-1]["content"])
    assert "repeated" in repeated_error["error"]
    assert len(reply.tool_steps) == 1


@pytest.mark.parametrize("arguments", [
    "not json", json.dumps({"question": "Why?", "entity_ids": ["unknown-id"]}),
    json.dumps({"question": "Why?", "arbitrary_action": "change score"}),
    json.dumps({"question": "x" * 2001}),
])
def test_malformed_or_unknown_entity_delegation_never_reaches_research(saved_result, tmp_path, arguments):
    malformed = {"id": "bad", "type": "function", "function": {
        "name": "request_research", "arguments": arguments,
    }}
    chat = ScriptedTransport([tool_turn(malformed), final(message="The requested fact is unknown.")])
    researcher = ScriptedTransport([])
    provider = FakeProvider()
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, provider).reply(
        saved_result, ChatRequest(message="Why?")))

    assert researcher.requests == provider.calls == [] and reply.tool_steps == []
    assert "Invalid" in chat.requests[1]["messages"][-1]["content"]


def test_chat_cannot_execute_a_raw_cala_tool(saved_result, tmp_path):
    chat = ScriptedTransport([search(), final(message="No permitted research lookup was requested.")])
    researcher = ScriptedTransport([])
    provider = FakeProvider()
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, provider).reply(
        saved_result, ChatRequest(message="Search directly.")))
    assert researcher.requests == provider.calls == [] and reply.tool_steps == []
    assert "Only request_research and final_answer" in chat.requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize("response", [None, [], {"content": "x" * 33_000}])
def test_invalid_or_oversized_assistant_response_is_an_explicit_error(saved_result, tmp_path, response):
    chat = ScriptedTransport([response])
    researcher = ScriptedTransport([])
    provider = FakeProvider()
    with pytest.raises(ChatModelError) as error:
        asyncio.run(multi_agent(tmp_path, chat, researcher, provider).reply(
            saved_result, ChatRequest(message="Why?")))
    assert error.value.code == "chat_invalid_output"
    assert len(chat.requests) == 1 and researcher.requests == provider.calls == []


@pytest.mark.parametrize("calls", [
    "not an array", [{"id": "bad", "function": None}],
    [call("duplicate", "request_research", question="First?"),
     call("duplicate", "request_research", question="Second?")],
    [{"id": "bad", "function": {"name": "request_research", "arguments": {"question": "Why?"}}}],
])
def test_malformed_assistant_tool_calls_are_not_executed_or_replayed(saved_result, tmp_path, calls):
    chat = ScriptedTransport([{"role": "assistant", "content": None, "tool_calls": calls},
                              final(message="The requested fact remains unknown.")])
    researcher = ScriptedTransport([])
    provider = FakeProvider()
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, provider).reply(
        saved_result, ChatRequest(message="Why?")))
    assert reply.tool_steps == [] and researcher.requests == provider.calls == []
    assert [tool["function"]["name"] for tool in chat.requests[1]["tools"]] == ["final_answer"]
    assert all("tool_calls" not in message for message in chat.requests[1]["messages"])


def test_only_one_delegation_per_chat_turn_and_final_skips_all_delegations(saved_result, tmp_path):
    first = delegate("First question.", identifier="one")["tool_calls"][0]
    second = delegate("Second question.", identifier="two")["tool_calls"][0]
    chat = ScriptedTransport([tool_turn(first, second), final(message="No supported facts are available.")])
    researcher = ScriptedTransport([research_finish(unknowns=["Unknown."])])
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher).reply(saved_result, ChatRequest(message="Why?")))
    assert len(researcher.requests) == 1 and len(reply.tool_steps) == 1
    assert "One research delegation" in chat.requests[1]["messages"][-1]["content"]

    chat = ScriptedTransport([tool_turn(first, *final(message="Answer from the saved run.")["tool_calls"])])
    researcher = ScriptedTransport([])
    asyncio.run(multi_agent(tmp_path, chat, researcher).reply(saved_result, ChatRequest(message="Why?")))
    assert researcher.requests == []


def test_cancellation_stops_research_and_releases_chat_capacity(saved_result, tmp_path):
    async def scenario():
        entered = asyncio.Event()

        class BlockingResearchTransport:
            cancelled = False

            async def complete(self, *args):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        chat = ScriptedTransport([delegate()])
        researcher = BlockingResearchTransport()
        service = multi_agent(tmp_path, chat, researcher, chat_max_concurrency=1)
        task = asyncio.create_task(service.reply(saved_result, ChatRequest(message="Who is Acme?")))
        await entered.wait()
        with pytest.raises(ChatModelError) as busy:
            await service.reply(saved_result, ChatRequest(message="Another question."))
        assert busy.value.code == "chat_busy"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert researcher.cancelled is True and not service._slots.locked()

        service.transport = ScriptedTransport([final(message="Available again.")])
        reply = await service.reply(saved_result, ChatRequest(message="Retry."))
        assert reply.message == "Available again."

    asyncio.run(scenario())


def test_replayed_function_arguments_count_toward_context_limit_before_final_call(saved_result, tmp_path, monkeypatch):
    limit = {}
    response = search(query="x" * 4000)

    def return_large_arguments(messages):
        # Keep ordinary message content under this limit while the replayed
        # assistant function.arguments will put the serialized context over it.
        limit["characters"] = len(json.dumps(messages, ensure_ascii=False, default=str)) + 1000
        monkeypatch.setattr(chat_module, "MAX_CONVERSATION_CHARACTERS", limit["characters"])
        return response

    chat = ScriptedTransport([return_large_arguments, final(message="Must not be requested.")])
    researcher = ScriptedTransport([])
    provider = FakeProvider()
    with pytest.raises(ChatModelError) as error:
        asyncio.run(multi_agent(tmp_path, chat, researcher, provider).reply(
            saved_result, ChatRequest(message="Why?")))

    assert error.value.code == "chat_context_too_large" and error.value.status == 413
    assert len(chat.requests) == 1 and len(chat.turns) == 1
    assert researcher.requests == provider.calls == []
    replayed = [*chat.requests[0]["messages"], response]
    assert sum(len(str(message.get("content") or "")) for message in replayed) < limit["characters"]
    assert len(json.dumps(replayed, ensure_ascii=False, default=str)) > limit["characters"]


def test_legacy_direct_mode_remains_explicitly_reachable(saved_result, tmp_path):
    chat = ScriptedTransport([search(), lambda messages: final([tool_source_id(messages)])])
    researcher = ScriptedTransport([])
    reply = asyncio.run(multi_agent(tmp_path, chat, researcher, multi_agents_enabled=False).reply(
        saved_result, ChatRequest(message="Who is Acme?")))
    assert researcher.requests == [] and chat.requests[0]["messages"][0]["content"] == AGENT_SYSTEM_MESSAGE
    assert "search_entities" in {tool["function"]["name"] for tool in chat.requests[0]["tools"]}
    assert [str(source.url) for source in reply.citations] == [TOOL_URL]
