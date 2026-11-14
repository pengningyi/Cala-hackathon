"""Parse Cala entity properties without borrowing provenance across facts."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

import pycountry
from pydantic import HttpUrl, TypeAdapter, ValidationError

from .cala import CalaError
from .models import Entity, EntityMetadataProperty, Source


def date_value(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def property_value(data: dict, key: str, default=None):
    value = data.get("properties", {}).get(key, default)
    if isinstance(value, dict):
        if "value" in value:
            return value["value"]
        if "sources" in value:
            return default
    return value


def sources(data: Any, retrieved_at: datetime) -> list[Source]:
    """Retain actual public URLs, publication dates and retrieval timestamps."""
    found: dict[tuple[str, str | None], Source] = {}

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            entries = value.get("sources", [])
            if not isinstance(entries, list):
                entries = []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                document = item.get("document")
                url = document.get("endpoint") if isinstance(document, dict) else document
                url = url or item.get("url")
                if not isinstance(url, str):
                    continue
                try:
                    source = Source(url=url, retrieved_at=retrieved_at,
                                    published_at=date_value(item.get("date") or item.get("published_time")),
                                    label=str(item.get("name") or "Cala"))
                except ValidationError:
                    continue
                key = str(source.url), str(source.published_at) if source.published_at else None
                found[key] = source
            for key, child in value.items():
                if key != "sources":
                    visit(child)

    visit(data)
    return list(found.values())


def _field_sources(data: dict, name: str, retrieved_at: datetime) -> list[Source]:
    return sources(data.get("properties", {}).get(name), retrieved_at)


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normal(value: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold().replace("_", " ")))


def country_name(value: Any, *, allow_code: bool = True) -> str | None:
    """Exact ISO lookup only; no city, state or fuzzy-geography inference."""
    if isinstance(value, dict):
        values = [country_name(value.get(key), allow_code=allow_code)
                  for key in ("name", "code", "alpha_2", "alpha_3", "country_code") if key in value]
        distinct = {item for item in values if item}
        return next(iter(distinct)) if len(distinct) == 1 else None
    text = _text(value)
    if text is None or (not allow_code and len(text) <= 3):
        return None
    try:
        item = pycountry.countries.lookup(text)
    except LookupError:
        return None
    return item.name


def _address_countries(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_address_countries(item) for item in value)) if value else set()
    if isinstance(value, dict):
        result = {country_name(value[key]) for key in ("country", "addressCountry", "country_code") if key in value}
        return {item for item in result if item}
    text = _text(value)
    if text is None:
        return set()
    # Try complete terminal components, including ISO names containing commas.
    # A terminal "CA" is ambiguous with a US state and must not become Canada.
    starts = [0, *(match.end() for match in re.finditer(r"[,;\n]", text))]
    result = {name for start in starts if (name := country_name(text[start:].strip(), allow_code=False))}
    terminal = re.search(r"[,;\n]\s*([A-Za-z]{2,3})\s*$", text)
    if terminal:
        try:
            country = pycountry.countries.lookup(terminal.group(1))
        except LookupError:
            country = None
        # GLEIF addresses may encode country as '..., MU-PW, 72261, MU'.
        # Accept the terminal ISO code only with a valid matching subdivision
        # code in this same headquarters address, never from registration data.
        subdivision_countries = {
            subdivision.country_code for code in re.findall(r"\b[A-Z]{2}-[A-Z0-9]{1,3}\b", text.upper())
            if (subdivision := pycountry.subdivisions.get(code=code)) is not None
        }
        if country and subdivision_countries == {country.alpha_2}:
            result.add(country.name)
    return result


def _address_text(value: Any) -> str | None:
    text = _text(value)
    if text is not None:
        return text
    if isinstance(value, dict):
        parts = [value.get(key) for key in ("street_address", "streetAddress", "address_line_1", "locality",
                                           "city", "region", "postal_code", "postalCode")]
        country = country_name(value.get("country") or value.get("addressCountry") or value.get("country_code"))
        clean = list(dict.fromkeys(item.strip() for item in parts if isinstance(item, str) and item.strip()))
        return ", ".join([*clean, *([country] if country else [])]) or None
    return None


def _active(relation: dict, as_of: date) -> bool:
    since = date_value(property_value(relation, "valid_since"))
    until = date_value(property_value(relation, "valid_until"))
    return not ((since and since > as_of) or (until and until < as_of))


def _country(data: dict, retrieved_at: datetime):
    observations: list[tuple[str, str, list[Source]]] = []
    for key in ("country", "country_name", "headquarters_country", "headquarters_country_name", "hq_country", "hq_country_code"):
        evidence = _field_sources(data, key, retrieved_at)
        name = country_name(property_value(data, key))
        if name and evidence:
            observations.append((name, "country_property", evidence))
    outgoing = data.get("relationships", {}).get("outgoing", {})
    for relation in outgoing.get("HAS_HEADQUARTERS_IN", []):
        if not isinstance(relation, dict) or relation.get("entity_type") != "Country" or not _active(relation, retrieved_at.date()):
            continue
        evidence = sources(relation, retrieved_at)
        name = country_name(relation.get("name"))
        if name and evidence:
            observations.append((name, "headquarters_relationship", evidence))
    evidence = _field_sources(data, "headquarters_address", retrieved_at)
    if evidence:
        observations.extend((name, "headquarters_address", evidence)
                            for name in _address_countries(property_value(data, "headquarters_address")))
    distinct = {name for name, _, _ in observations}
    combined = _unique_sources([source for _, _, evidence in observations for source in evidence])
    if len(distinct) > 1:
        return None, "conflicting_metadata", combined
    if not observations:
        return None, None, []
    basis = next((basis for _, basis, _ in observations if basis == "headquarters_relationship"), observations[0][1])
    return observations[0][0], basis, combined


_KINDS = {
    "vc": "vc", "venture capital": "vc", "venture capital firm": "vc", "venture capital fund": "vc",
    "venture capitalist": "vc", "venture capital investor": "vc",
    "angel": "angel", "angel investor": "angel", "business angel": "angel", "individual investor": "angel",
    "corporate vc": "corporate_vc", "corporate venture capital": "corporate_vc", "cvc": "corporate_vc",
    "corporate venture capital firm": "corporate_vc", "corporate venture capital fund": "corporate_vc",
    "corporate venture arm": "corporate_vc",
}


def _unique_sources(values: list[Source]) -> list[Source]:
    return list({(str(item.url), item.published_at, item.retrieved_at): item for item in values}.values())


def _kind(data: dict, retrieved_at: datetime, *, company: bool):
    if company:
        return "company", "startup_context", []
    observations: list[tuple[str, str, list[Source]]] = []
    for key in ("investor_type", "investor_category", "fund_type", "organization_type", "organisation_type", "kind"):
        evidence = _field_sources(data, key, retrieved_at)
        value = property_value(data, key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            kind = _KINDS.get(_normal(item)) if isinstance(item, str) else None
            if kind and evidence:
                observations.append((kind, "explicit_category", evidence))
    for relation in data.get("relationships", {}).get("outgoing", {}).get("OPERATES_IN_INDUSTRY", []):
        if not isinstance(relation, dict) or not _active(relation, retrieved_at.date()):
            continue
        evidence = sources(relation, retrieved_at)
        for value in (relation.get("name"), property_value(relation, "original_label")):
            kind = _KINDS.get(_normal(value)) if isinstance(value, str) else None
            if kind and evidence:
                observations.append((kind, "industry_classification", evidence))
    distinct = {kind for kind, _, _ in observations}
    combined = _unique_sources([source for _, _, evidence in observations for source in evidence])
    if len(distinct) > 1:
        return "unknown", "conflicting_metadata", combined
    if observations:
        return observations[0][0], observations[0][1], combined
    if data.get("entity_type") == "Person":
        # A Person participating as an investor is an individual, not a fund.
        evidence = sources({"sources": data.get("sources", []),
                            "properties": {"sources": data.get("properties", {}).get("sources", []),
                                           "entity_type": data.get("properties", {}).get("entity_type")}}, retrieved_at)
        return "angel", "person_entity_type", evidence
    return "unknown", None, []


def _website(value: Any):
    text = _text(value)
    if text is None or re.search(r"\s", text):
        return None
    if text.startswith("//"):
        text = "https:" + text
    elif "://" not in text:
        text = "https://" + text
    try:
        parsed = urlsplit(text)
        if parsed.username or parsed.password or not parsed.hostname:
            return None
        return TypeAdapter(HttpUrl).validate_python(text)
    except (ValueError, ValidationError):
        return None


def parse_entity(data: dict[str, Any], retrieved_at: datetime, *, company: bool = False,
                 metadata_retrieved: bool = False) -> Entity:
    identifier = data.get("id") or property_value(data, "id")
    name = data.get("name") or property_value(data, "name")
    if not identifier or not isinstance(name, str) or not name.strip():
        raise CalaError("cala_entity_invalid", "Cala entity lacks its stable identity")
    aliases = property_value(data, "aliases", [])
    aliases = [item for item in aliases if isinstance(item, str)] if isinstance(aliases, list) else []
    country, country_basis, country_sources = _country(data, retrieved_at)
    kind, kind_basis, kind_sources = _kind(data, retrieved_at, company=company)
    metadata = [EntityMetadataProperty(name=key, value=property_value(data, key),
                                       sources=_field_sources(data, key, retrieved_at))
                for key in data.get("properties", {}) if key != "sources"]
    return Entity(
        id=str(identifier), name=name, entity_type=_text(data.get("entity_type")), kind=kind,
        kind_basis=kind_basis, kind_sources=kind_sources, country=country,
        country_basis=country_basis, country_sources=country_sources,
        website=_website(property_value(data, "website") or data.get("website")),
        description=_text(property_value(data, "description")) or _text(data.get("description")),
        headquarters_address=_address_text(property_value(data, "headquarters_address")),
        registered_address=_address_text(property_value(data, "registered_address")),
        aliases=aliases, sources=sources(data, retrieved_at), metadata=metadata,
        metadata_status="retrieved" if metadata_retrieved else "not_requested",
        metadata_retrieved_at=retrieved_at if metadata_retrieved else None,
    )
