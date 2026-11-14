"""Sourced startup field prefill after explicit company identity confirmation."""
from __future__ import annotations

import re

from pydantic import HttpUrl

from .models import CompanyPrefill, Entity, Source


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _unique(sources: list[Source]) -> list[Source]:
    result, seen = [], set()
    for source in sources:
        key = (str(source.url), source.published_at, source.retrieved_at, source.label)
        if key not in seen:
            seen.add(key)
            result.append(source)
    return result


def _same_website(value, expected: HttpUrl) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    elif "://" not in value:
        value = "https://" + value
    try:
        return str(HttpUrl(value)).rstrip("/") == str(expected).rstrip("/")
    except ValueError:
        return False


def startup_prefill(startup: Entity) -> tuple[CompanyPrefill, dict[str, list[Source]]]:
    """Copy explicit fields only; the founder supplies current-round information."""
    prefill = CompanyPrefill(company_name=startup.name,
                             website=str(startup.website) if startup.website else "")
    refs = {"company_name": list(startup.sources), "website": [], "sector": [], "country": []}
    sectors: list[str] = []
    sector_sources: list[Source] = []
    for prop in startup.metadata:
        key = _key(prop.name)
        if key in ("website", "company_website", "homepage", "homepage_url") and startup.website:
            values = prop.value if isinstance(prop.value, list) else [prop.value]
            if any(_same_website(value, startup.website) for value in values):
                refs["website"].extend(prop.sources)
        if key not in ("sector", "sectors", "industry", "industries", "operates_in_industry") or not prop.sources:
            continue
        values = prop.value if isinstance(prop.value, list) else [prop.value]
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            if len(value) > 150 or _key(value) in ("unknown", "company", "organization", "vc", "venture_capital", "none", "null"):
                continue
            # Cala industry taxonomy enums are explicit broad classifications.
            label = value.replace("_", " ").capitalize() if value.isupper() and "_" in value else value
            if label.casefold() not in {item.casefold() for item in sectors}:
                sectors.append(label)
                sector_sources.extend(prop.sources)
    if sectors:
        # Preserve whole classifications within the existing founder-sector limit.
        selected: list[str] = []
        for item in sectors:
            if len(", ".join([*selected, item])) <= 200:
                selected.append(item)
        prefill.sector = ", ".join(selected)
        refs["sector"] = sector_sources if selected else []
    # The owner's form asks for headquarters and its rules compare that fact.
    # Keep address proxies visible in metadata without silently confirming HQ.
    address_fallback = (startup.country_basis or "").casefold().startswith(("office", "registered", "registration"))
    if startup.country and len(startup.country) <= 100 and startup.country_sources and not address_fallback:
        prefill.country = startup.country
        refs["country"] = list(startup.country_sources)
    return prefill, {key: _unique(values) for key, values in refs.items()}
