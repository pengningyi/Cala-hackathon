"""Bounded Cala retrieval, real-response cache, and replayable progress records."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, TypeAdapter

from .config import Settings
from .models import ProgressEvent


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BudgetExceeded(Exception):
    pass


@dataclass
class Trace:
    events: list[ProgressEvent] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def emit(self, step: str, message: str, type: str = "progress", **kwargs):
        self.events.append(ProgressEvent(
            id=len(self.events) + 1, at=utcnow(), step=step, message=message, type=type, **kwargs
        ))
        self.changed.set()


class ResponseCache:
    def __init__(self, directory: Path, ttl: int, namespace: str):
        self.directory, self.ttl, self.namespace = directory, ttl, namespace

    def path(self, operation, args):
        key = json.dumps([self.namespace, operation, args], sort_keys=True, default=str).encode()
        return self.directory / (hashlib.sha256(key).hexdigest() + ".json")

    def get(self, operation, args, adapter: TypeAdapter):
        if self.ttl == 0:
            return None
        try:
            data = json.loads(self.path(operation, args).read_text(encoding="utf-8"))
            retrieved = datetime.fromisoformat(data["retrieved_at"])
            if (utcnow() - retrieved).total_seconds() > self.ttl:
                return None
            return adapter.validate_python(data["value"]), retrieved
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, operation, args, value, adapter: TypeAdapter, retrieved: datetime):
        if self.ttl == 0:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.path(operation, args)
            # Atomic replacement keeps parallel runs from reading partial JSON.
            import uuid
            temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temp.write_text(json.dumps({"retrieved_at": retrieved.isoformat(),
                                        "value": adapter.dump_python(value, mode="json")}), encoding="utf-8")
            temp.replace(path)
        except OSError:
            pass  # Cache availability must not turn retrieved facts into a failed run.


class Retrieval:
    def __init__(self, provider, cache: ResponseCache, settings: Settings, trace: Trace,
                 started: float | None = None, semaphore: asyncio.Semaphore | None = None):
        self.provider, self.cache, self.settings, self.trace = provider, cache, settings, trace
        self.started = started if started is not None else time.monotonic()
        self.stop_at = self.started + settings.run_deadline_seconds - settings.assembly_reserve_seconds
        self.semaphore = semaphore or asyncio.Semaphore(settings.cala_concurrency)
        self.calls = self.cache_hits = 0
        self.timestamps: list[datetime] = []
        self._native = None
        self._step = ContextVar("cala_step", default="cala")
        self._inflight: dict[str, asyncio.Task] = {}

    def remaining(self):
        return max(0.0, self.stop_at - time.monotonic())

    async def call(self, operation: str, args: dict[str, Any], result_type, step: str):
        if self.remaining() <= 0:
            raise BudgetExceeded("Cala retrieval deadline reached")
        adapter = TypeAdapter(result_type)
        # Native graph joins may need several tools. Charge and cache every real
        # tool invocation; never hide a multi-call join behind one operation count.
        if operation != "call_data_tool" and hasattr(self.provider, "with_tool_caller"):
            if self._native is None:
                from .cala_native import ToolPayload

                async def raw_call(tool, arguments):
                    return await self.call("call_data_tool", {"tool": tool, "arguments": arguments}, ToolPayload, self._step.get())

                self._native = self.provider.with_tool_caller(raw_call)
            token = self._step.set(step)
            try:
                async with asyncio.timeout(self.remaining()):
                    return adapter.validate_python(await getattr(self._native, operation)(**args))
            except TimeoutError:
                raise BudgetExceeded("Cala retrieval deadline reached") from None
            finally:
                self._step.reset(token)
        if operation == "call_data_tool":
            key = json.dumps(args, sort_keys=True, default=str)
            if key in self._inflight:
                value = await asyncio.shield(self._inflight[key])
                self.cache_hits += 1
                self.trace.emit(step, "Reusing the same in-flight Cala response", type="tool_result",
                                tool=args["tool"], query=args["arguments"], cached=True, count=_count(value))
                return value
            task = asyncio.create_task(self._call_cached(operation, args, adapter, step))
            self._inflight[key] = task
            try:
                return await task
            finally:
                self._inflight.pop(key, None)
        return await self._call_cached(operation, args, adapter, step)

    async def _call_cached(self, operation, args, adapter, step):
        from .cala import CalaError

        trace_tool = args["tool"] if operation == "call_data_tool" else operation
        trace_query = args["arguments"] if operation == "call_data_tool" else args
        hit = self.cache.get(operation, args, adapter)
        if hit:
            self.cache_hits += 1
            value, retrieved = hit
            self.timestamps.append(retrieved)
            self.trace.emit(step, "Using cached Cala response", type="tool_result", tool=trace_tool,
                            query=trace_query, cached=True, count=_count(value))
            return value
        slow = operation == "knowledge_query_entities" or (
            operation == "call_data_tool" and args.get("tool") in ("knowledge_search", "knowledge_query"))
        for attempt in range(2):
            # Semaphore wait counts against the same global deadline.
            try:
                async with asyncio.timeout(self.remaining()):
                    async with self.semaphore:
                        if self.calls >= self.settings.cala_call_limit or self.remaining() <= 0:
                            raise BudgetExceeded("Cala call or time budget reached")
                        self.calls += 1
                        self.trace.emit(step, "Calling Cala", type="tool_call", tool=trace_tool,
                                        query=trace_query, cached=False)
                        per_call = (self.settings.cala_query_timeout_seconds if slow
                                    else self.settings.cala_request_timeout_seconds)
                        async with asyncio.timeout(min(self.remaining(), per_call)):
                            value = await getattr(self.provider, operation)(**args)
                            value = adapter.validate_python(value)
                retrieved = utcnow()
                self.timestamps.append(retrieved)
                self.cache.put(operation, args, value, adapter, retrieved)
                self.trace.emit(step, "Cala response received", type="tool_result", tool=trace_tool,
                                query=trace_query, cached=False, count=_count(value))
                return value
            except TimeoutError:
                # A slow free-text query that timed out once is not retried.
                if attempt or self.remaining() <= 0 or (slow and operation == "call_data_tool"):
                    raise BudgetExceeded("Cala retrieval timed out") from None
                self.trace.emit(step, "Transient request timeout; retrying once", type="retry", tool=trace_tool)
            except CalaError as exc:
                delay = max(0, exc.retry_after or 0)
                if (not exc.transient or attempt or self.calls >= self.settings.cala_call_limit
                        or delay >= self.remaining()):
                    raise
                self.trace.emit(step, "Transient Cala failure; retrying once", type="retry", tool=trace_tool)
                if delay:
                    await asyncio.sleep(delay)
        raise BudgetExceeded("Cala retrieval unavailable")


def _count(value):
    if hasattr(value, "data") and isinstance(value.data, dict):
        return len(value.data.get("entities", value.data.get("results", []))) or None
    if isinstance(value, list):
        return len(value)
    if hasattr(value, "rounds"):
        return len(value.rounds)
    return 1
