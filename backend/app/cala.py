"""Explicit, schema-checked adapter for a supplied Cala MCP server.

There are deliberately no guessed Cala tool names or endpoint URLs. Discover the
server's tools, inspect their schemas, and provide a reviewed operation mapping.
One provider operation makes one tools/call; MCP initialization and tools/list are
protocol metadata. The orchestrator owns call budgets, retries and response cache.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError as SchemaValidationError
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import TypeAdapter, ValidationError

from .models import Entity, GraphSnapshot, InvestorProfile


class CalaError(Exception):
    def __init__(self, code: str, message: str, *, transient: bool = False,
                 retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.transient = transient
        self.retry_after = retry_after


class CalaProvider(Protocol):
    async def search_entities(self, query: str, kind: str) -> list[Entity]: ...
    async def lead_graph(self, lead_id: str, lookback_months: int) -> GraphSnapshot: ...
    async def investor_profile(self, investor_id: str, lookback_months: int) -> InvestorProfile: ...


def _lookup(data: Any, path: str) -> Any:
    """A small JSON-pointer reader, not executable expressions or guessed paths."""
    if path in ("", "/"):
        return data
    if not path.startswith("/"):
        raise ValueError("Response paths must be JSON pointers starting with /")
    for piece in path[1:].split("/"):
        key = piece.replace("~1", "/").replace("~0", "~")
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def _arguments(template: Any, variables: dict[str, Any]) -> Any:
    if isinstance(template, str) and template.startswith("{") and template.endswith("}"):
        key = template[1:-1]
        if key not in variables:
            raise ValueError("Unknown operation argument variable")
        return variables[key]
    if isinstance(template, dict):
        return {key: _arguments(value, variables) for key, value in template.items()}
    if isinstance(template, list):
        return [_arguments(value, variables) for value in template]
    return template


def _project(template: Any, data: Any, retrieved_at: str) -> Any:
    """Optional deterministic field projection for noncanonical Cala responses.

    {"$path":"/id"} copies a field; {"$each":"/items","template":...}
    projects array elements; {"$retrieved_at":true} records this actual retrieval.
    Constants are allowed, but the reviewed mapping must never invent public facts.
    """
    if isinstance(template, dict):
        if set(template) == {"$path"}:
            return _lookup(data, template["$path"])
        if set(template) == {"$retrieved_at"}:
            return retrieved_at
        if set(template) == {"$each", "template"}:
            values = _lookup(data, template["$each"])
            if not isinstance(values, list):
                raise ValueError("Projection $each must point to an array")
            return [_project(template["template"], value, retrieved_at) for value in values]
        return {key: _project(value, data, retrieved_at) for key, value in template.items()}
    if isinstance(template, list):
        return [_project(value, data, retrieved_at) for value in template]
    return template


def _stamp_sources(data: Any, retrieved_at: str) -> Any:
    if isinstance(data, list):
        return [_stamp_sources(value, retrieved_at) for value in data]
    if isinstance(data, dict):
        result = {key: _stamp_sources(value, retrieved_at) for key, value in data.items()}
        if "sources" in result:
            for source in result["sources"]:
                # This is a fresh network response; never accept an old/future
                # retrieval timestamp claimed inside the returned tool payload.
                source["retrieved_at"] = retrieved_at
        return result
    return data


def _translate_error(error: BaseException) -> CalaError:
    if isinstance(error, CalaError):
        return error
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            translated = _translate_error(nested)
            if translated.code != "cala_transport_error":
                return translated
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        retry_after = None
        if raw := error.response.headers.get("Retry-After"):
            try:
                retry_after = max(0.0, float(raw))
            except ValueError:
                from email.utils import parsedate_to_datetime
                try:
                    retry_after = max(0.0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError):
                    pass
        return CalaError("cala_http_error", f"Cala returned HTTP {status}",
                         transient=status in (408, 429, 500, 502, 503, 504), retry_after=retry_after)
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return CalaError("cala_timeout", "Cala request timed out", transient=True)
    if isinstance(error, httpx.RequestError):
        return CalaError("cala_transport_error", "Cala could not be reached", transient=True)
    return CalaError("cala_transport_error", "Cala MCP request failed")


class CalaMCPProvider:
    def __init__(self, endpoint: str | None = None, token: str | None = None,
                 mapping_path: str | Path | None = None,
                 transport: Literal["streamable_http", "sse"] = "streamable_http",
                 timeout_seconds: float = 15, api_key: str | None = None,
                 api_key_header: str = "X-API-KEY"):
        self.endpoint = endpoint
        self._token = token
        self._api_key = api_key
        self._api_key_header = api_key_header
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self._mapping_path = Path(mapping_path) if mapping_path else None
        self._tools: dict[str, dict[str, Any]] | None = None
        self._tools_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self._mapping_path and self._mapping_path.is_file())

    def _mapping(self, operation: str) -> dict[str, Any]:
        if not self.endpoint:
            raise CalaError("cala_not_configured", "Set CALA_MCP_URL to the supplied Cala MCP endpoint")
        if self._mapping_path is None or not self._mapping_path.is_file():
            raise CalaError("cala_mapping_required", "Inspect Cala tools and set CALA_TOOL_MAPPING to a reviewed operation mapping")
        try:
            mapping = json.loads(self._mapping_path.read_text(encoding="utf-8"))[operation]
            if not isinstance(mapping["tool"], str) or not isinstance(mapping.get("arguments", {}), dict):
                raise ValueError("Invalid operation mapping")
            return mapping
        except (OSError, ValueError, KeyError, TypeError):
            raise CalaError("cala_mapping_invalid", f"Invalid mapping for operation {operation}") from None

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        if self._api_key:
            headers[self._api_key_header] = self._api_key
        return headers

    @asynccontextmanager
    async def _session(self):
        if not self.endpoint:
            raise CalaError("cala_not_configured", "Set CALA_MCP_URL to the supplied Cala MCP endpoint")
        headers = self._headers()
        try:
            if self.transport == "sse":
                async with sse_client(self.endpoint, headers=headers, timeout=self.timeout_seconds,
                                      sse_read_timeout=self.timeout_seconds) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
            else:
                async with httpx.AsyncClient(
                    headers=headers, timeout=httpx.Timeout(self.timeout_seconds),
                ) as client:
                    async with streamable_http_client(self.endpoint, http_client=client) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _translate_error(error) from None

    async def discover_tools(self) -> list[dict[str, Any]]:
        async with self._tools_lock:
            if self._tools is None:
                found: dict[str, dict[str, Any]] = {}
                async with self._session() as session:
                    cursor = None
                    for _ in range(10):
                        response = await session.list_tools(cursor=cursor)
                        for tool in response.tools:
                            found[tool.name] = {
                                "name": tool.name, "description": tool.description,
                                "inputSchema": tool.inputSchema,
                            }
                        cursor = response.nextCursor
                        if not cursor:
                            break
                    else:
                        raise CalaError("cala_tool_limit", "Cala tool discovery exceeded ten metadata pages")
                self._tools = found
            return list(self._tools.values())

    async def _call(self, operation: str, variables: dict[str, Any]) -> Any:
        mapping = self._mapping(operation)
        try:
            args = _arguments(mapping.get("arguments", {}), variables)
        except ValueError:
            raise CalaError("cala_mapping_invalid", f"Invalid arguments for {operation}") from None
        await self.discover_tools()
        tool = self._tools.get(mapping["tool"]) if self._tools else None
        if tool is None:
            raise CalaError("cala_tool_missing", f"Mapped tool for {operation} is not advertised by Cala")
        try:
            Draft202012Validator.check_schema(tool["inputSchema"])
            Draft202012Validator(tool["inputSchema"]).validate(args)
        except (SchemaError, SchemaValidationError):
            raise CalaError("cala_arguments_invalid", f"Mapped arguments for {operation} do not match Cala's tool schema") from None
        async with self._session() as session:
            response = await session.call_tool(mapping["tool"], arguments=args)
        if response.isError:
            raise CalaError("cala_tool_error", f"Cala tool failed during {operation}")
        try:
            payload = response.structuredContent
            if payload is None:
                texts = [content.text for content in response.content if content.type == "text"]
                payload = json.loads("\n".join(texts))
            payload = _lookup(payload, mapping.get("response_path", ""))
            stamp = datetime.now(timezone.utc).isoformat()
            if "projection" in mapping:
                payload = _project(mapping["projection"], payload, stamp)
            return _stamp_sources(payload, stamp)
        except (ValueError, TypeError, KeyError, IndexError):
            raise CalaError("cala_response_invalid", f"Cala response for {operation} does not match its reviewed mapping") from None

    async def search_entities(self, query: str, kind: str) -> list[Entity]:
        payload = await self._call("search_entities", {"query": query, "kind": kind})
        try:
            return TypeAdapter(list[Entity]).validate_python(payload)
        except ValidationError:
            raise CalaError("cala_response_invalid", "Cala entity response does not match the normalized contract") from None

    async def lead_graph(self, lead_id: str, lookback_months: int) -> GraphSnapshot:
        payload = await self._call("lead_graph", {"lead_id": lead_id, "lookback_months": lookback_months})
        try:
            return GraphSnapshot.model_validate(payload)
        except ValidationError:
            raise CalaError("cala_response_invalid", "Cala graph response does not match the normalized contract") from None

    async def investor_profile(self, investor_id: str, lookback_months: int) -> InvestorProfile:
        payload = await self._call("investor_profile", {"investor_id": investor_id, "lookback_months": lookback_months})
        try:
            profile = InvestorProfile.model_validate(payload)
            if profile.investor.id != investor_id:
                raise CalaError("cala_entity_mismatch", "Cala profile does not identify the requested investor")
            # Assessments are computed by the separately bounded semantic step.
            return profile.model_copy(update={"assessments": []})
        except ValidationError:
            raise CalaError("cala_response_invalid", "Cala profile response does not match the normalized contract") from None
