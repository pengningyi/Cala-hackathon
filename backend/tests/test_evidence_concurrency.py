"""Request completion merges actual Cala evidence without sharing run state."""
import asyncio
from datetime import timedelta
import json
import time

import pytest

from app.chat_agent import ChatAgent
from app.models import ChatRequest, Entity, Source
from app.pipeline import RunRecord, Workflow
from app.research_evidence import ResearchEvidenceJournal, source_references
from app.runtime import utcnow
from test_chat import saved_result  # noqa: F401 (fixture)
from test_chat_agent import FakeProvider, NOW, TOOL_URL, final, settings, tool_source_id
from test_multiagent_chat import ScriptedTransport, delegate, handoff, multi_agent, observed_source, research_finish, search


def record_for(result):
    return RunRecord(id=result.run_id, profile=result.profile, lead=result.lead,
                     confirmed_at=result.confirmed_at, started=time.monotonic(), result=result)


def local_tool_source(messages):
    for message in reversed(messages):
        if message.get("role") == "tool":
            packet = json.loads(message["content"])
            if "result" in packet:
                return packet["result"][0]["source_ids"][0]
    raise AssertionError("Actual tool observation missing")


class DistinctProvider(FakeProvider):
    def __init__(self, blocking=False):
        super().__init__()
        self.blocking = blocking
        self.both_started = asyncio.Event()
        self.release = asyncio.Event()

    async def search_entities(self, query, kind):
        self.calls.append(("search_entities", query, kind))
        if self.blocking:
            if len(self.calls) == 2:
                self.both_started.set()
            await self.release.wait()
        return [Entity(id=f"capital-{query.lower()}", name=f"{query} Capital", kind="vc", sources=[
            Source(url=f"https://synthetic.example.test/{query.lower()}", retrieved_at=NOW, label=f"Retrieved {query}"),
        ])]


class RoutingChatTransport:
    """Select a scripted turn by this request's own original question."""

    async def complete(self, messages, tools, tool_choice="required"):
        label = None
        for message in messages:
            if message.get("role") != "user":
                continue
            try:
                packet = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            if isinstance(packet, dict) and "question" in packet:
                label = packet["question"]
                break
        assert label is not None
        if not any(message.get("role") == "tool" for message in messages):
            return delegate(question=label)
        packet = handoff(messages)
        identifier = packet["observations"][0]["result"][0]["source_ids"][0]
        return final([identifier], message=f"{label} Capital appears in the retrieved evidence.")


class RoutingResearchTransport:
    async def complete(self, messages, tools, tool_choice="required"):
        context = json.loads(messages[1]["content"].split("\n", 1)[1])
        label = context["task"]["question"]
        if not any(message.get("role") == "tool" for message in messages):
            return search(query=label)
        return research_finish([local_tool_source(messages)], text=f"Supplied Cala evidence names {label} Capital.")


def test_concurrent_validated_chat_requests_merge_both_observations(saved_result, tmp_path):
    async def scenario():
        provider = DistinctProvider(blocking=True)
        service = multi_agent(tmp_path, RoutingChatTransport(), RoutingResearchTransport(), provider)
        run = record_for(saved_result)
        before = saved_result.model_dump_json()
        first = asyncio.create_task(service.reply(saved_result, ChatRequest(message="Alpha"), research=run.research))
        second = asyncio.create_task(service.reply(saved_result, ChatRequest(message="Beta"), research=run.research))
        await asyncio.wait_for(provider.both_started.wait(), 2)
        assert run.research.revision == 0 and run.research.snapshot().observations == []
        provider.release.set()
        answers = await asyncio.wait_for(asyncio.gather(first, second), 2)

        assert sorted(answer.evidence_revision for answer in answers) == [1, 2]
        assert [str(answer.citations[0].url) for answer in answers] == [
            "https://synthetic.example.test/alpha", "https://synthetic.example.test/beta",
        ]
        snapshot = run.research.snapshot()
        assert snapshot.revision == 2 and len(snapshot.observations) == 2
        assert {item["result"][0]["name"] for item in snapshot.observations} == {"Alpha Capital", "Beta Capital"}
        assert set(source_references(snapshot.observations)) == set(snapshot.sources)
        assert {str(source.url) for source in snapshot.sources.values()} == {
            "https://synthetic.example.test/alpha", "https://synthetic.example.test/beta",
        }
        assert len(provider.calls) == 2 and not service._slots.locked()
        assert saved_result.model_dump_json() == before

    asyncio.run(scenario())


@pytest.mark.parametrize("multi", [True, False])
def test_json_content_final_answer_commits_in_delegated_and_direct_modes(saved_result, tmp_path, multi):
    def json_answer(messages):
        identifier = observed_source(messages) if multi else tool_source_id(messages)
        return {"role": "assistant", "content": json.dumps({
            "message": f"Cala lists Acme Capital. [{identifier}]", "source_ids": [identifier], "candidate_ids": [],
        })}

    chat = ScriptedTransport([delegate() if multi else search(), json_answer])
    researcher = ScriptedTransport([search(), lambda messages: research_finish([tool_source_id(messages)])])
    service = multi_agent(tmp_path, chat, researcher, multi_agents_enabled=multi)
    journal = ResearchEvidenceJournal()
    before = saved_result.model_dump_json()
    answer = asyncio.run(service.reply(saved_result, ChatRequest(message="Who is Acme?"), research=journal))

    assert answer.evidence_revision == journal.revision == 1
    assert [str(source.url) for source in answer.citations] == [TOOL_URL]
    snapshot = journal.snapshot()
    assert len(snapshot.observations) == 1 and snapshot.observations[0]["tool"] == "search_entities"
    assert snapshot.observations[0]["result"][0]["name"] == "Acme Capital"
    assert set(source_references(snapshot.observations)) == set(snapshot.sources)
    assert len(researcher.requests) == (2 if multi else 0)
    assert saved_result.model_dump_json() == before


def test_rejected_research_summary_does_not_discard_actual_tool_observation(saved_result, tmp_path):
    chat = ScriptedTransport([delegate(), lambda messages: final([observed_source(messages)])])
    researcher = ScriptedTransport([
        search(), *[research_finish(["s999"], text="REJECTED_MODEL_SUMMARY_NOT_EVIDENCE") for _ in range(3)],
    ])
    service = multi_agent(tmp_path, chat, researcher)
    journal = ResearchEvidenceJournal()
    answer = asyncio.run(service.reply(saved_result, ChatRequest(message="Who is Acme?"), research=journal))

    assert answer.evidence_revision == journal.revision == 1
    assert [str(source.url) for source in answer.citations] == [TOOL_URL]
    assert [(step.tool, step.status) for step in answer.tool_steps] == [
        ("request_research", "failed"), ("search_entities", "done"),
    ]
    packet = handoff(chat.requests[-1]["messages"])
    assert "error" in packet and "research" not in packet
    snapshot = journal.snapshot()
    assert len(snapshot.observations) == 1
    assert set(snapshot.observations[0]) == {"tool", "arguments", "result"}
    assert "REJECTED_MODEL_SUMMARY_NOT_EVIDENCE" not in json.dumps(snapshot.observations)
    assert "Acme Capital" in json.dumps(snapshot.observations)


def test_completed_run_cleanup_keeps_other_run_journal_and_revision_isolated(saved_result, tmp_path):
    async def scenario():
        config = settings(multi_agents_enabled=True, cache_dir=tmp_path / "cache", retention_seconds=60)
        provider = DistinctProvider()
        workflow = Workflow(config, provider)
        first_result = saved_result.model_copy(update={"run_id": "first-run"})
        second_result = saved_result.model_copy(update={"run_id": "second-run"})
        first, second = record_for(first_result), record_for(second_result)
        workflow.runs = {first.id: first, second.id: second}
        service = ChatAgent(config, workflow, RoutingChatTransport(), RoutingResearchTransport())
        try:
            first_answer = await service.reply(first_result, ChatRequest(message="Alpha"), research=first.research)
            assert first_answer.evidence_revision == 1
            assert second.research.revision == 0 and second.research.snapshot().observations == []
            second_answer = await service.reply(second_result, ChatRequest(message="Beta"), research=second.research)
            assert second_answer.evidence_revision == first.research.revision == 1
            first_urls = {str(source.url) for source in first.research.snapshot().sources.values()}
            second_urls = {str(source.url) for source in second.research.snapshot().sources.values()}
            assert first_urls == {"https://synthetic.example.test/alpha"}
            assert second_urls == {"https://synthetic.example.test/beta"}

            first.result = first_result.model_copy(update={"finished_at": utcnow() - timedelta(seconds=61)})
            workflow.cleanup()
            assert first.id not in workflow.runs and workflow.get(second.id) is second
            assert second.research.revision == 1
            assert {str(source.url) for source in second.research.snapshot().sources.values()} == second_urls
        finally:
            await workflow.close()

    asyncio.run(scenario())
