"""Company and lead identity lookup through Cala's REST knowledge query.

The onboarding "Confirm matches" step finds the founder company and the lead investor with
`POST /v1/knowledge/query` (dot-notation input) instead of MCP. One query per name returns
both the matching rows (website, industry, country, headquarters, description, investor
type) and the Cala entities they refer to. The workflow then enriches these candidates
through MCP entity introspection and retrieval for full available property metadata.
Entity IDs are validated before a confirmed match hands off to the MCP run.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import HttpUrl, ValidationError

from .cala import CalaError
from .entity_metadata import country_name
from .models import Entity, EntityMetadataProperty, Source

QUERY_PATH = "/v1/knowledge/query"
TOP_MATCHES = 3
RETURN_FIELDS = "name, website, industry, country, headquarters, description, investor_type"
ENTITY_TYPES = ("company", "organization")


def query_name(name: str) -> str:
    """Remove characters that would change the meaning of a dot-notation query."""
    return re.sub(r"\s+", " ", re.sub(r"[.=()]", " ", name)).strip()[:100]


def _text(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and value.casefold() not in ("null", "none", "unknown", "n/a") else None


def _website(value: Any) -> HttpUrl | None:
    text = _text(value)
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        return HttpUrl(text)
    except ValidationError:
        return None


def _country(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    # Rows can read "United States (headquartered in Menlo Park, California)".
    return country_name(re.sub(r"\(.*?\)", "", text).strip(" ,;"))


def _kind(company: bool, investor_type: str | None, industry: str | None) -> str:
    if company:
        return "company"
    label = f"{investor_type or ''} {industry or ''}".casefold()
    if "angel" in label:
        return "angel"
    if "corporate" in label:
        return "corporate_vc"
    if "venture" in label or "capital" in label or "private equity" in label:
        return "vc"
    return "unknown"


def entities_from_query(data: Any, retrieved_at: datetime, *, company: bool) -> list[Entity]:
    """Pair each result row with its Cala entity id. Rows without an identified entity are dropped."""
    if not isinstance(data, dict):
        raise CalaError("cala_response_invalid", "Cala knowledge query did not return an object")
    rows, refs = data.get("results") or [], data.get("entities") or []
    if not isinstance(rows, list) or not isinstance(refs, list):
        raise CalaError("cala_response_invalid", "Cala knowledge query returned malformed results")
    ids: dict[str, dict] = {}
    for ref in refs:
        if (isinstance(ref, dict) and ref.get("id") and isinstance(ref.get("name"), str)
                and str(ref.get("entity_type", "")).casefold() in ENTITY_TYPES):
            for label in [ref["name"], *(ref.get("mentions") or [])]:
                if isinstance(label, str):
                    ids.setdefault(label.casefold().strip(), ref)
    source = Source(url=HttpUrl("https://api.cala.ai" + QUERY_PATH), retrieved_at=retrieved_at,
                    label="Cala knowledge_query")
    found: list[Entity] = []
    for row in rows:
        if not isinstance(row, dict) or not (name := _text(row.get("name"))):
            continue
        ref = ids.get(name.casefold())
        if ref is None or any(item.id == ref["id"] for item in found):
            continue
        industry, investor_type = _text(row.get("industry")), _text(row.get("investor_type"))
        country, headquarters = _country(row.get("country")), _text(row.get("headquarters"))
        website = _website(row.get("website"))
        metadata = []
        if website:
            metadata.append(EntityMetadataProperty(name="website", value=str(website), sources=[source]))
        if industry:
            metadata.append(EntityMetadataProperty(
                name="industry", value=[part.strip() for part in industry.split(";") if part.strip()],
                sources=[source]))
        if investor_type:
            metadata.append(EntityMetadataProperty(name="investor_type", value=investor_type, sources=[source]))
        found.append(Entity(
            id=str(ref["id"]), name=name, kind=_kind(company, investor_type, industry),
            country=country, website=website, description=_text(row.get("description")),
            aliases=[item for item in ref.get("mentions") or [] if isinstance(item, str) and item != name],
            sources=[source], entity_type=str(ref.get("entity_type")), headquarters_address=headquarters,
            country_basis="headquarters" if country else None, country_sources=[source] if country else [],
            kind_basis="investor_type" if investor_type and not company else None,
            kind_sources=[source] if investor_type and not company else [],
            metadata=metadata, metadata_status="retrieved", metadata_retrieved_at=retrieved_at,
        ))
        if len(found) == TOP_MATCHES:
            break
    return found


class CalaKnowledgeResolver:
    """Resolve onboarding identities with `POST /v1/knowledge/query` (top three matches)."""

    def __init__(self, api_key: str, base_url: str = "https://api.cala.ai", timeout_seconds: float = 15):
        self.base_url, self._api_key, self.timeout_seconds = base_url.rstrip("/"), api_key, timeout_seconds

    @staticmethod
    def query_text(name: str) -> str:
        return f"companies.name={query_name(name)}.limit={TOP_MATCHES}.return({RETURN_FIELDS})"

    async def knowledge_query_entities(self, query: str, kind: str) -> list[Entity]:
        if not query_name(query):
            return []
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds,
                                         headers={"X-API-KEY": self._api_key}) as client:
                response = await client.post(QUERY_PATH, json={"input": self.query_text(query), "return_entities": True})
        except httpx.TimeoutException:
            raise CalaError("cala_timeout", "Cala knowledge query timed out", transient=True) from None
        except httpx.HTTPError:
            raise CalaError("cala_unavailable", "Cala knowledge query could not be reached", transient=True) from None
        if response.status_code == 429 or response.status_code >= 500:
            retry = response.headers.get("retry-after")
            raise CalaError("cala_unavailable", "Cala knowledge query is temporarily unavailable", transient=True,
                            retry_after=float(retry) if retry and retry.isdigit() else None)
        if response.status_code in (401, 403):
            raise CalaError("cala_auth", "Cala rejected the API key")
        if response.status_code >= 400:
            raise CalaError("cala_query_rejected", "Cala rejected the knowledge query")
        try:
            data = response.json()
        except ValueError:
            raise CalaError("cala_response_invalid", "Cala knowledge query did not return JSON") from None
        return entities_from_query(data, datetime.now(timezone.utc), company=kind == "company")
