"""Source identity, JSON validity and tool permissions survive real handoffs."""
import asyncio
import json

from app.agent_tools import AgentToolSession, MAX_TOOL_RESULT_CHARACTERS, _dump
from app.chat_model import _Evidence
from app.models import KnowledgeAnswer
from app.runtime import ResponseCache, Retrieval, Trace
from test_chat_agent import FakeProvider, call, settings


def test_large_observations_remain_json_with_exact_entity_ids_and_source_urls():
    entity_id = "company-" + "a" * 260
    url = "https://evidence.example.test/" + "b" * 400
    value = {"result": [{"id": entity_id, "description": "detail " * 2000,
                         "source_ids": ["s123"], "website": url}],
             "sources": {"s123": {"url": url, "label": "evidence " * 2000}}}
    raw = _dump(value)
    parsed = json.loads(raw)
    assert len(raw) <= MAX_TOOL_RESULT_CHARACTERS and parsed["truncated"] is True
    assert parsed["result"][0]["id"] == entity_id
    assert parsed["result"][0]["website"] == url
    assert parsed["result"][0]["source_ids"] == ["s123"]
    assert parsed["sources"]["s123"]["url"] == url


def session(tmp_path, provider, **overrides):
    config = settings(**overrides)
    return AgentToolSession(Retrieval(provider, ResponseCache(tmp_path, 0, "tools"), config, Trace()),
                            _Evidence(), {}, knowledge_search=config.chat_knowledge_search)


def test_disabled_knowledge_search_cannot_be_called_even_when_provider_implements_it(tmp_path):
    class Provider(FakeProvider):
        async def knowledge_search(self, query):
            self.calls.append(query)
            return KnowledgeAnswer(text="unexpected")

    provider = Provider()
    tools = session(tmp_path, provider, chat_knowledge_search=False)
    result = asyncio.run(tools.execute(call("slow", "knowledge_search", query="Anything")))
    assert "error" in json.loads(result) and provider.calls == [] and tools.steps == []


def test_malformed_tool_call_is_rejected_before_any_provider_access(tmp_path):
    provider = FakeProvider()
    tools = session(tmp_path, provider)
    output = asyncio.run(tools.execute({"id": "bad", "function": None}))
    assert "error" in json.loads(output) and provider.calls == [] and tools.steps == []
