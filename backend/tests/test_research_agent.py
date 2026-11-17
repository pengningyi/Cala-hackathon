"""Research-agent contracts with scripted models and an in-memory tool session."""
import asyncio
from copy import deepcopy
import json
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.chat_model import ChatModelError
from app.research_agent import RESEARCH_SYSTEM_MESSAGE, ResearchAgent, ResearchRequest


def settings(**overrides):
    return SimpleNamespace(**{
        "research_max_tool_rounds": 3, "research_max_model_calls": 5,
        "chat_knowledge_search": False, "cala_query_timeout_seconds": 40, **overrides,
    })


def call(identifier, name, **arguments):
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}


def turn(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def finish(source_ids=("s1",), text="Acme appears in the supplied Cala evidence.", **overrides):
    return turn(call("done", "finish_research", **{
        "findings": [{"text": text, "source_ids": list(source_ids)}],
        "unknowns": [], "coverage": "sufficient", **overrides,
    }))


class ScriptedTransport:
    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def complete(self, messages, tools, tool_choice="required"):
        self.requests.append(deepcopy({"messages": messages, "tools": tools, "tool_choice": tool_choice}))
        response = self.turns.pop(0)
        return response(messages) if callable(response) else response


class FakeSession:
    """The session, rather than model findings, owns sources and observations."""
    def __init__(self, seconds=60):
        self.stop_at = time.monotonic() + seconds
        self.retrieval = SimpleNamespace(remaining=lambda: max(0, self.stop_at - time.monotonic()))
        self.evidence = SimpleNamespace(sources={"s1": {"url": "https://news.example.test/saved"}})
        self.names = {"acme": "Acme Capital"}
        self.steps = []
        self.serializer = SimpleNamespace(websites=set())
        self.observations = []
        self.calls = []

    def tools(self, knowledge_search):
        names = ["search_entities"] + (["knowledge_search"] if knowledge_search else [])
        return [{"type": "function", "function": {
            "name": name, "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }} for name in names]

    def catalog(self):
        return dict(self.evidence.sources)

    async def execute(self, call):
        arguments = json.loads(call["function"]["arguments"])
        if not isinstance(arguments.get("query"), str):
            return json.dumps({"error": "Invalid arguments: query is required."})
        self.calls.append(call)
        source_id = f"s{len(self.evidence.sources) + 1}"
        self.evidence.sources[source_id] = {"url": f"https://news.example.test/{source_id}"}
        observation = {"result": {"name": "Acme Capital", "source_ids": [source_id]},
                       "sources": {source_id: self.evidence.sources[source_id]}}
        self.observations.append(observation)
        return json.dumps(observation)


def research(transport, session=None, **overrides):
    return asyncio.run(ResearchAgent(settings(**overrides), transport).run(
        ResearchRequest(question="Who is Acme?", entity_ids=["acme"]), session or FakeSession(),
        context={"question_scope": "confirmed funding shortlist"}))


def test_specialist_owns_its_prompt_context_and_cited_tool_handoff():
    session = FakeSession()
    transport = ScriptedTransport([
        turn(call("lookup", "search_entities", query="Acme")),
        lambda messages: finish([json.loads(messages[-1]["content"])["result"]["source_ids"][0]]),
    ])
    result = research(transport, session)
    assert result.coverage == "sufficient" and result.findings[0].source_ids == ["s2"]
    assert len(session.calls) == len(session.observations) == 1
    assert transport.requests[0]["messages"][0]["content"] == RESEARCH_SYSTEM_MESSAGE
    initial = transport.requests[0]["messages"][1]["content"]
    assert '"entity_ids": ["acme"]' in initial and "confirmed funding shortlist" in initial
    offered = {tool["function"]["name"] for tool in transport.requests[0]["tools"]}
    assert offered == {"search_entities", "finish_research"}
    assert "final_answer" not in offered and "request_research" not in offered
    # Generated text is a handoff only, never a new registered observation/source.
    assert len(session.evidence.sources) == 2
    assert "findings" not in session.observations[0]


@pytest.mark.parametrize("bad", [finish(["s999"]), finish([]), finish(findings=[{
    "text": "A valid-looking claim.", "source_ids": ["s1"], "new_entity": "fabricated",
}])])
def test_invalid_citations_or_schema_get_bounded_repair(bad):
    transport = ScriptedTransport([bad, finish()])
    result = research(transport)
    assert result.findings[0].source_ids == ["s1"]
    repair = transport.requests[1]
    assert "finish_research rejected" in repair["messages"][-1]["content"]
    assert [tool["function"]["name"] for tool in repair["tools"]] == ["finish_research"]
    assert repair["tool_choice"] == {"type": "function", "function": {"name": "finish_research"}}


def test_repeated_invalid_handoffs_stop_after_two_repairs():
    transport = ScriptedTransport([finish(["s999"])] * 10)
    with pytest.raises(ChatModelError) as error:
        research(transport)
    assert error.value.code == "research_invalid_output" and "s999" in error.value.reason
    assert len(transport.requests) == 3


def test_empty_findings_cannot_claim_sufficient_research_coverage():
    transport = ScriptedTransport([finish(findings=[], unknowns=[], coverage="sufficient")])
    result = research(transport)
    assert result.coverage == "partial" and result.findings == []
    assert "no supported findings" in result.unknowns[0]


def test_tool_round_cap_offers_only_finish_and_prevents_further_lookups():
    session = FakeSession()
    transport = ScriptedTransport([
        turn(call("first", "search_entities", query="Acme")),
        turn(call("ignored", "search_entities", query="Other")),
        finish(),
    ])
    research(transport, session, research_max_tool_rounds=1)
    assert len(session.calls) == 1
    assert [tool["function"]["name"] for tool in transport.requests[1]["tools"]] == ["finish_research"]
    assert "Tool unavailable" in transport.requests[2]["messages"][-1]["content"]


def test_last_model_call_is_reserved_for_finish_and_hard_cap_is_enforced():
    session = FakeSession()
    lookup = turn(call("lookup", "search_entities", query="Acme"))
    transport = ScriptedTransport([lookup] * 10)
    with pytest.raises(ChatModelError) as error:
        research(transport, session, research_max_model_calls=2)
    assert error.value.code == "research_invalid_output"
    assert len(transport.requests) == 2 and len(session.calls) == 1
    assert [tool["function"]["name"] for tool in transport.requests[-1]["tools"]] == ["finish_research"]


def test_parallel_lookup_turn_is_limited_to_four_calls():
    session = FakeSession()
    transport = ScriptedTransport([
        turn(*(call(str(index), "search_entities", query=f"Fund {index}") for index in range(5))),
        finish(),
    ])
    research(transport, session)
    assert len(session.calls) == 4
    results = [message for message in transport.requests[1]["messages"] if message["role"] == "tool"]
    assert len(results) == 5 and "At most 4" in results[-1]["content"]


def test_unknown_tools_are_never_delegated_to_the_shared_session():
    session = FakeSession()
    transport = ScriptedTransport([turn(call("unknown", "publish_report", body="Do it")), finish()])
    research(transport, session)
    assert session.calls == []
    assert "Tool unavailable" in transport.requests[1]["messages"][-1]["content"]


def test_session_argument_errors_remain_observations_and_do_not_create_facts():
    session = FakeSession()
    transport = ScriptedTransport([
        turn(call("invalid", "search_entities", wrong="argument")),
        finish(findings=[], unknowns=["The lookup arguments were invalid."], coverage="partial"),
    ])
    result = research(transport, session)
    assert result.coverage == "partial" and result.findings == []
    assert session.calls == session.observations == []
    assert "Invalid arguments" in transport.requests[1]["messages"][-1]["content"]


@pytest.mark.parametrize("response", [
    {"content": "Here is an unsupported answer."},
    {"tool_calls": [{"id": "broken", "function": {"name": "search_entities", "arguments": {"query": "Acme"}}}]},
])
def test_free_text_or_malformed_tool_calls_are_redirected_to_finish(response):
    transport = ScriptedTransport([response, finish()])
    result = research(transport)
    assert result.findings and transport.requests[1]["tool_choice"]["function"]["name"] == "finish_research"


def test_finish_prevents_other_tools_in_the_same_turn_from_running():
    session = FakeSession()
    transport = ScriptedTransport([turn(
        call("not-run", "search_entities", query="Acme"), *finish()["tool_calls"],
    )])
    research(transport, session)
    assert session.calls == []


def test_expired_shared_deadline_returns_partial_without_using_answer_reserve():
    transport = ScriptedTransport([])
    result = research(transport, FakeSession(seconds=0))
    assert result.coverage == "partial" and result.findings == []
    assert "deadline" in result.unknowns[0] and transport.requests == []


def test_shared_deadline_cancels_slow_model_and_preserves_partial_observations():
    class SlowTransport:
        cancelled = False

        async def complete(self, *args):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    transport = SlowTransport()
    session = FakeSession()
    session.retrieval.remaining = lambda: 0.02
    session.observations.append({"existing": "observation"})
    result = research(transport, session)
    assert result.coverage == "partial" and transport.cancelled is True
    assert session.observations == [{"existing": "observation"}]


def test_shared_deadline_cancels_slow_lookup():
    class SlowSession(FakeSession):
        cancelled = False

        async def execute(self, call):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    session = SlowSession()
    session.retrieval.remaining = lambda: 0.02
    transport = ScriptedTransport([turn(call("slow", "search_entities", query="Acme"))])
    result = research(transport, session)
    assert result.coverage == "partial" and session.cancelled is True
    assert len(transport.requests) == 1


def test_browser_cancellation_propagates_to_research_model():
    async def scenario():
        started = asyncio.Event()

        class BlockingTransport:
            cancelled = False

            async def complete(self, *args):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        transport = BlockingTransport()
        task = asyncio.create_task(ResearchAgent(settings(), transport).run(
            ResearchRequest(question="Why?"), FakeSession()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.cancelled is True

    asyncio.run(scenario())


def test_oversized_model_output_is_rejected_without_replaying_it():
    transport = ScriptedTransport([{"content": "x" * 33_000}])
    with pytest.raises(ChatModelError) as error:
        research(transport)
    assert error.value.code == "research_invalid_output" and len(transport.requests) == 1


def test_slow_knowledge_search_is_only_offered_if_opted_in_and_time_fits():
    for enabled, seconds, expected in ((False, 60, False), (True, 60, True), (True, 10, False)):
        transport = ScriptedTransport([finish()])
        research(transport, FakeSession(seconds), chat_knowledge_search=enabled)
        names = {tool["function"]["name"] for tool in transport.requests[0]["tools"]}
        assert ("knowledge_search" in names) is expected


@pytest.mark.parametrize("payload", [
    {"question": "x" * 2001}, {"question": " "},
    {"question": "Why?", "entity_ids": [str(index) for index in range(11)]},
    {"question": "Why?", "execute": "arbitrary command"},
])
def test_delegation_request_rejects_unbounded_or_unexpected_fields(payload):
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate(payload)


def test_context_size_is_bounded_before_model_work():
    transport = ScriptedTransport([])
    with pytest.raises(ChatModelError) as error:
        asyncio.run(ResearchAgent(settings(), transport).run(
            ResearchRequest(question="Why?"), FakeSession(), context={"too_large": "x" * 160_000}))
    assert error.value.code == "research_context_too_large" and transport.requests == []
