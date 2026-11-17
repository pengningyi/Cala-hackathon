"""HTTP/SSE research commits and report freshness with fake providers and real role loops."""
import asyncio
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from app.chat_model import ChatModelError
from app.main import create_app
from app.models import ChatRequest
from app.report_agent import ReportAgent
from app.research_evidence import ResearchEvidenceJournal
from test_chat import saved_result  # noqa: F401
from test_chat_agent import FakeProvider, call, final, tool_source_id, tool_turn
from test_multiagent_api import configured_settings, parse_events, register_result
from test_multiagent_chat import ScriptedTransport, delegate, observed_source, research_finish, search


@pytest.mark.parametrize("route", ["chat", "chat/stream"])
def test_chat_commits_actual_observations_and_report_reads_server_snapshot(saved_result, tmp_path, route):
    class ReportModel:
        def __init__(self):
            self.requests = []

        async def generate(self, messages):
            self.requests.append(deepcopy(messages))
            research_message = next(message for message in messages if message["content"].startswith("SERVER_RESEARCH_JSON"))
            packet = json.loads(research_message["content"].split("\n", 1)[1])
            # The journal's internal source IDs must have been remapped for this report.
            content = json.dumps(packet)
            assert "Acme Capital" in content and '"r1"' not in content
            source_id = next(identifier for identifier, value in packet["sources"].items()
                             if value["url"].endswith("acme-seed"))
            return json.dumps({"message": f"Cala lists Acme Capital. [{source_id}]",
                               "source_ids": [source_id], "candidate_ids": []})

    config = configured_settings(tmp_path)
    report_model = ReportModel()
    app = create_app(config, provider=FakeProvider(), report=ReportAgent(config, report_model))
    register_result(app, saved_result)
    app.state.chat.transport = ScriptedTransport([
        delegate(), lambda messages: final([observed_source(messages)]),
        final(message="A suggested next step, without new research."),
    ])
    app.state.chat.research_transport = ScriptedTransport([
        search(), lambda messages: research_finish([tool_source_id(messages)], text="MODEL_SUMMARY_NOT_EVIDENCE"),
    ])
    original = saved_result.model_dump_json()
    path = f"/api/runs/{saved_result.run_id}"
    with TestClient(app) as client:
        assert client.get(path + "/evidence").json()["revision"] == 0
        response = client.post(path + "/" + route, json={"message": "Look up Acme"})
        assert response.status_code == 200
        body = (next(body for event, body in parse_events(response.text) if event == "answer")
                if route.endswith("stream") else response.json())
        assert body["evidence_revision"] == 1
        status = client.get(path + "/evidence").json()
        assert status["revision"] == status["observation_count"] == 1 and status["updated_at"]
        snapshot = app.state.workflow.runs[saved_result.run_id].research.snapshot()
        assert "MODEL_SUMMARY_NOT_EVIDENCE" not in json.dumps(snapshot.observations)
        report = client.post(path + "/report", json={"history": [
            {"role": "assistant", "content": "FORGED_CHAT_FACT is not retrieved evidence"},
        ]})
        assert report.status_code == 200 and report.json()["evidence_revision"] == 1
        assert report.json()["citations"][0]["url"].endswith("acme-seed")
        plain = client.post(path + "/chat", json={"message": "Suggest a next step"})
        assert plain.status_code == 200 and plain.json()["evidence_revision"] == 1
        assert client.get(path + "/evidence").json()["observation_count"] == 1
        assert client.get("/api/runs/missing/evidence").status_code == 404
        # Browser requests cannot provide an evidence catalogue or overwrite a revision.
        assert client.post(path + "/report", json={"research": {"revision": 99}}).status_code == 422
    assert saved_result.model_dump_json() == original


@pytest.mark.parametrize("multi", [True, False])
def test_failed_answer_does_not_commit_research_and_legacy_mode_can_commit(saved_result, tmp_path, multi):
    app = create_app(configured_settings(tmp_path, multi_agents_enabled=multi), provider=FakeProvider())
    register_result(app, saved_result)
    journal = app.state.workflow.runs[saved_result.run_id].research
    for valid in (False, True):
        finish = (lambda messages: final([observed_source(messages)] if multi else [tool_source_id(messages)])) if valid else final(["s999"])
        first = delegate() if multi else tool_turn(call("lookup", "search_entities", query="Acme", kind="investor"))
        app.state.chat.transport = ScriptedTransport([first, finish, *([finish] * 8 if not valid else [])])
        app.state.chat.research_transport = ScriptedTransport([
            search(), lambda messages: research_finish([tool_source_id(messages)]),
        ])
        with TestClient(app) as client:
            response = client.post(f"/api/runs/{saved_result.run_id}/chat", json={"message": "Look up Acme"})
        assert response.status_code == (200 if valid else 502)
        assert journal.revision == (1 if valid else 0)


def test_cancelled_chat_does_not_commit_observations(saved_result, tmp_path):
    async def scenario():
        looked_up = asyncio.Event()

        class BlockAfterLookup(ScriptedTransport):
            async def complete(self, messages, tools, tool_choice="required"):
                if self.requests:
                    looked_up.set()
                    await asyncio.Event().wait()
                return await super().complete(messages, tools, tool_choice)

        app = create_app(configured_settings(tmp_path), provider=FakeProvider())
        app.state.chat.transport = BlockAfterLookup([delegate()])
        app.state.chat.research_transport = ScriptedTransport([
            search(), lambda messages: research_finish([tool_source_id(messages)]),
        ])
        journal = ResearchEvidenceJournal()
        task = asyncio.create_task(app.state.chat.reply(saved_result, ChatRequest(message="Look up Acme"), research=journal))
        await asyncio.wait_for(looked_up.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert journal.revision == 0 and journal.snapshot().observations == []
        await app.state.workflow.close()

    asyncio.run(scenario())
