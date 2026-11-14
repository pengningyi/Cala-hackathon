"""Thin async client for the Cala API (https://api.cala.ai, spec: /openapi.json).

Endpoints used:
  GET  /v1/entities                      entity_search      (fuzzy name -> entity ids)
  GET  /v1/entities/{id}/introspection   entity_introspection (which fields/relationships exist)
  POST /v1/entities/{id}                 retrieve_entity    (properties + relationships, with sources)
  POST /v1/knowledge/query               knowledge_query    (structured rows, dot-notation input)
  POST /v1/knowledge/search              knowledge_search   (prose answer + KnowBits with source origins)

Every call is logged through `on_call` so the UI can show a live agent trace,
and cached (memory + cache.json) so repeated demo runs cost no credits.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

BASE_URL = os.getenv("CALA_BASE_URL", "https://api.cala.ai")
CACHE_PATH = Path(os.getenv("CALA_CACHE", Path(__file__).parent / "cache.json"))

CallLogger = Callable[[dict], Awaitable[None]]


class CalaError(RuntimeError):
    pass


class Cala:
    def __init__(self, api_key: str, on_call: Optional[CallLogger] = None, use_cache: bool = True):
        if not api_key:
            raise CalaError("CALA_API_KEY is not set")
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=60,
        )
        self.on_call = on_call
        self.use_cache = use_cache
        self.sem = asyncio.Semaphore(int(os.getenv("CALA_CONCURRENCY", "4")))
        self._cache: dict[str, Any] = {}
        if use_cache and CACHE_PATH.exists():
            try:
                self._cache = json.loads(CACHE_PATH.read_text())
            except Exception:
                self._cache = {}

    async def close(self):
        await self.client.aclose()
        if self.use_cache:
            try:
                CACHE_PATH.write_text(json.dumps(self._cache))
            except Exception:
                pass

    # ------------------------------------------------------------------ core
    async def _call(self, method: str, path: str, *, params=None, body=None, label: str = "") -> Any:
        key = hashlib.sha1(json.dumps([method, path, params, body], sort_keys=True).encode()).hexdigest()
        endpoint = f"{method} {re.sub(r'[0-9a-f-]{36}', '{id}', path)}"
        if self.use_cache and key in self._cache:
            data = self._cache[key]
            await self._log(endpoint, label, 0, data, cached=True)
            return data
        async with self.sem:
            t0 = time.perf_counter()
            for attempt in range(3):
                resp = await self.client.request(method, path, params=params, json=body)
                if resp.status_code == 429 and attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break
            ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code >= 400:
            await self._log(endpoint, label, ms, None, error=f"HTTP {resp.status_code}")
            raise CalaError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if self.use_cache:
            self._cache[key] = data
        await self._log(endpoint, label, ms, data)
        return data

    async def _log(self, endpoint, label, ms, data, cached=False, error=None):
        if not self.on_call:
            return
        n = None
        if isinstance(data, dict):
            for k in ("entities", "results", "context"):
                if isinstance(data.get(k), list):
                    n = len(data[k])
                    break
        await self.on_call({"type": "call", "endpoint": endpoint, "input": label[:160],
                            "ms": ms, "n": n, "cached": cached, "error": error})

    # ------------------------------------------------------------- endpoints
    async def search_entities(self, name: str, types: Optional[list[str]] = None, limit: int = 8) -> list[dict]:
        params: list[tuple[str, Any]] = [("name", name[:100]), ("limit", limit)]
        for t in types or []:
            params.append(("entity_types", t))
        data = await self._call("GET", "/v1/entities", params=params, label=name)
        return data.get("entities", []) if isinstance(data, dict) else []

    async def introspect(self, entity_id: str) -> dict:
        return await self._call("GET", f"/v1/entities/{entity_id}/introspection", label=entity_id)

    async def retrieve(self, entity_id: str, body: Optional[dict] = None, label: str = "") -> dict:
        return await self._call("POST", f"/v1/entities/{entity_id}", body=body or None, label=label or entity_id)

    async def query(self, text: str) -> dict:
        return await self._call("POST", "/v1/knowledge/query", body={"input": text, "return_entities": True}, label=text)

    async def search(self, text: str) -> dict:
        return await self._call("POST", "/v1/knowledge/search",
                                body={"input": text, "explainability": True, "return_entities": True}, label=text)
