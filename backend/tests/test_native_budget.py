"""Synthetic native-tool joins: actual tool attempts own the retrieval budget.

No endpoint, credentials, live Cala facts, or language-model calls are used.
"""
from __future__ import annotations

import asyncio
import copy
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.cala import CalaError
from app.cala_native import ToolPayload
from app.config import Settings
from app.runtime import BudgetExceeded, ResponseCache, Retrieval, Trace, utcnow


@dataclass
class CallState:
    attempts: Counter = field(default_factory=Counter)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    active: int = 0
    max_active: int = 0
    joins: int = 0
    retrieved_at: datetime = field(default_factory=lambda: utcnow() - timedelta(minutes=2))


class SyntheticNativeProvider:
    """A native-style provider whose semantic operations make several raw calls."""

    def __init__(self, *, failures=0, fail_tool=None, delay=0):
        self.state = CallState()
        self.failures, self.fail_tool, self.delay = failures, fail_tool, delay
        self._caller = None

    def with_tool_caller(self, caller):
        bound = copy.copy(self)
        bound._caller = caller
        return bound

    async def call_data_tool(self, tool, arguments):
        key = json.dumps([tool, arguments], sort_keys=True)
        self.state.attempts[key] += 1
        self.state.calls.append((tool, dict(arguments)))
        self.state.active += 1
        self.state.max_active = max(self.state.max_active, self.state.active)
        try:
            await asyncio.sleep(self.delay)
            if ((self.fail_tool is None or tool == self.fail_tool)
                    and self.state.attempts[key] <= self.failures):
                raise CalaError("synthetic_transient", "Synthetic failure", transient=True)
            return ToolPayload(
                data={"tool": tool, "arguments": arguments,
                      "source_url": "https://synthetic.example.test/evidence",
                      "publication_date": "2025-08-14"},
                retrieved_at=self.state.retrieved_at,
            )
        finally:
            self.state.active -= 1

    async def join(self, investor_id, details):
        self.state.joins += 1
        caller = self._caller or self.call_data_tool
        # Model the real adapter's schema inspection, event listing and joins.
        schema = await caller("schema_inspection", {"investor_id": investor_id})
        events = await caller("investment_events", {"investor_id": investor_id})
        projected = await asyncio.gather(*(
            caller("event_detail", {"investor_id": investor_id, "event_id": f"event-{i}"})
            for i in range(details)
        ))
        return [schema, events, *projected]


def setup_retrieval(tmp_path, provider, *, ttl=0, semaphore=None, **overrides):
    config = Settings(_env_file=None, model_provider="disabled", cache_dir=tmp_path / "native-cache",
                      cache_ttl_seconds=ttl, **overrides)
    cache = ResponseCache(config.cache_dir, ttl, "synthetic-native-test")
    return Retrieval(provider, cache, config, Trace(), semaphore=semaphore)


def test_native_join_charges_every_subcall_instead_of_one_semantic_operation(tmp_path):
    async def scenario():
        provider = SyntheticNativeProvider()
        retrieval = setup_retrieval(tmp_path, provider)
        result = await retrieval.call("join", {"investor_id": "lead", "details": 3},
                                      list[ToolPayload], "lead_graph")
        assert len(result) == 5
        assert retrieval.calls == len(provider.state.calls) == 5
        tools = [tool for tool, _ in provider.state.calls]
        assert tools == ["schema_inspection", "investment_events", "event_detail", "event_detail", "event_detail"]
        calls = [event for event in retrieval.trace.events if event.type == "tool_call"]
        assert len(calls) == 5
        assert all(event.step == "lead_graph" and event.query["investor_id"] == "lead" for event in calls)
        assert {event.tool for event in calls} == {"schema_inspection", "investment_events", "event_detail"}
    asyncio.run(scenario())


def test_native_twenty_attempt_limit_includes_each_join_retry(tmp_path):
    async def scenario():
        provider = SyntheticNativeProvider(failures=1)
        retrieval = setup_retrieval(tmp_path, provider, cala_call_limit=20)
        result = await retrieval.call("join", {"investor_id": "lead", "details": 8},
                                      list[ToolPayload], "lead_graph")
        assert len(result) == 10
        assert retrieval.calls == len(provider.state.calls) == 20
        assert len(provider.state.attempts) == 10
        assert set(provider.state.attempts.values()) == {2}
        with pytest.raises(BudgetExceeded):
            await retrieval.call("join", {"investor_id": "over-budget", "details": 1},
                                 list[ToolPayload], "profiles")
        assert retrieval.calls == len(provider.state.calls) == 20
        assert all(arguments["investor_id"] != "over-budget" for _, arguments in provider.state.calls)
    asyncio.run(scenario())


def test_failed_native_subcall_is_retried_once_without_replaying_completed_join_steps(tmp_path):
    async def scenario():
        provider = SyntheticNativeProvider(failures=2, fail_tool="event_detail")
        retrieval = setup_retrieval(tmp_path, provider)
        with pytest.raises(CalaError, match="Synthetic failure"):
            await retrieval.call("join", {"investor_id": "lead", "details": 1},
                                 list[ToolPayload], "lead_graph")
        assert Counter(tool for tool, _ in provider.state.calls) == {
            "schema_inspection": 1, "investment_events": 1, "event_detail": 2,
        }
        assert retrieval.calls == 4 and provider.state.joins == 1
    asyncio.run(scenario())


def test_warm_native_raw_cache_preserves_tool_payload_dates_and_retrieval_metadata(tmp_path):
    async def scenario():
        provider = SyntheticNativeProvider()
        cold = setup_retrieval(tmp_path, provider, ttl=3600)
        args = {"investor_id": "lead", "details": 2}
        original = await cold.call("join", args, list[ToolPayload], "lead_graph")
        assert cold.calls == 4 and cold.cache_hits == 0
        warm = setup_retrieval(tmp_path, provider, ttl=3600)
        cached = await warm.call("join", args, list[ToolPayload], "lead_graph")
        assert provider.state.joins == 2  # Join reruns; each underlying tool response comes from cache.
        assert len(provider.state.calls) == 4
        assert warm.calls == 0 and warm.cache_hits == 4
        assert cached == original
        assert all(value.retrieved_at == provider.state.retrieved_at for value in cached)
        assert all(value.data["publication_date"] == "2025-08-14" for value in cached)
        assert sorted(warm.timestamps) == sorted(cold.timestamps)
        assert len(warm.trace.events) == 4
        assert all(event.cached is True for event in warm.trace.events)
        assert {event.tool for event in warm.trace.events} == {"schema_inspection", "investment_events", "event_detail"}
    asyncio.run(scenario())


def test_native_parallel_joins_share_four_request_limit_and_keep_trace_steps_isolated(tmp_path):
    async def scenario():
        provider = SyntheticNativeProvider(delay=0.005)
        retrieval = setup_retrieval(tmp_path, provider, cala_concurrency=4)
        results = await asyncio.gather(
            retrieval.call("join", {"investor_id": "graph-investor", "details": 6},
                           list[ToolPayload], "lead_graph"),
            retrieval.call("join", {"investor_id": "profile-investor", "details": 6},
                           list[ToolPayload], "profiles"),
        )
        assert [len(result) for result in results] == [8, 8]
        assert provider.state.max_active == 4
        assert provider.state.active == 0
        assert retrieval.calls == len(provider.state.calls) == 16
        calls = [event for event in retrieval.trace.events if event.type == "tool_call"]
        assert len(calls) == 16
        for event in calls:
            expected = "lead_graph" if event.query["investor_id"] == "graph-investor" else "profiles"
            assert event.step == expected
    asyncio.run(scenario())


def test_separate_native_runs_use_shared_global_semaphore(tmp_path):
    async def scenario():
        provider = SyntheticNativeProvider(delay=0.005)
        semaphore = asyncio.Semaphore(4)
        first = setup_retrieval(tmp_path / "first", provider, semaphore=semaphore)
        second = setup_retrieval(tmp_path / "second", provider, semaphore=semaphore)
        await asyncio.gather(
            first.call("join", {"investor_id": "run-one", "details": 5}, list[ToolPayload], "profiles"),
            second.call("join", {"investor_id": "run-two", "details": 5}, list[ToolPayload], "profiles"),
        )
        assert first.calls == second.calls == 7
        assert provider.state.max_active == 4 and provider.state.active == 0
    asyncio.run(scenario())


def test_identical_parallel_native_joins_reuse_each_inflight_raw_response(tmp_path):
    async def scenario():
        provider = SyntheticNativeProvider(delay=0.005)
        retrieval = setup_retrieval(tmp_path, provider)
        args = {"investor_id": "same-investor", "details": 2}
        first, second = await asyncio.gather(
            retrieval.call("join", args, list[ToolPayload], "lead_graph"),
            retrieval.call("join", args, list[ToolPayload], "profiles"),
        )
        assert first == second
        assert provider.state.joins == 2
        assert retrieval.calls == len(provider.state.calls) == 4
        assert retrieval.cache_hits == 4
        assert sum(event.cached is True for event in retrieval.trace.events) == 4
    asyncio.run(scenario())
