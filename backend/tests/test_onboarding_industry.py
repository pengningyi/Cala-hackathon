"""Onboarding sector prefill uses explicit, sourced company classifications."""
import asyncio
from datetime import datetime, timezone

from app.cala_native import CalaNativeProvider, ToolPayload


AT = datetime(2026, 11, 14, tzinfo=timezone.utc)


def edge(identifier, name, *, sourced=True, entity_type="Industry", **properties):
    return {"id": identifier, "name": name, "entity_type": entity_type, "properties": {
        "sources": [{"name": "Synthetic classification", "document": f"https://synthetic.example.test/{identifier}",
                     "date": "2026-10-01"}] if sourced else [], **properties,
    }}


def read(edges):
    async def caller(tool, arguments):
        if tool == "entity_introspection":
            data = {"properties": ["name"], "relationships": {"outgoing": ["OPERATES_IN_INDUSTRY"]}}
        else:
            data = {"id": "startup", "name": "Synthetic startup", "entity_type": "Company",
                    "description": "An AI company in Finland", "properties": {},
                    "relationships": {"outgoing": {"OPERATES_IN_INDUSTRY": edges}}}
        return ToolPayload(data=data, retrieved_at=AT)
    return asyncio.run(CalaNativeProvider().with_tool_caller(caller).entity_metadata("startup", company=True))


def test_company_industries_keep_explicit_labels_and_exact_field_sources():
    entity = read([edge("software", "SOFTWARE_AND_SERVICES"), edge("internet", "INTERNET_AND_WEB_SERVICES"),
                   edge("software", "SOFTWARE_AND_SERVICES")])
    classification = next(row for row in entity.metadata if row.name == "operates_in_industry")
    assert classification.value == ["SOFTWARE_AND_SERVICES", "INTERNET_AND_WEB_SERVICES"]
    assert [str(source.url) for source in classification.sources] == [
        "https://synthetic.example.test/software", "https://synthetic.example.test/internet"]
    assert all(source.retrieved_at == AT and source.published_at.isoformat() == "2026-10-01"
               for source in classification.sources)
    assert entity.kind == "company" and entity.country is None


def test_unreliable_or_inactive_industry_relationships_do_not_prefill_sector():
    entity = read([edge("old", "SOFTWARE", valid_until="2020-01-01"),
                   edge("future", "SOFTWARE", valid_since="2030-01-01"),
                   edge("malformed-date", "SOFTWARE", valid_until="invalid"),
                   edge("unsourced", "AI", sourced=False), edge("", "SOFTWARE"),
                   edge("bad-type", "SOFTWARE", entity_type="Country"),
                   {"id": "bad-properties", "name": "SOFTWARE", "entity_type": "Industry", "properties": "invalid"},
                   edge("ambiguous", "SOFTWARE"), edge("ambiguous", "HARDWARE"),
                   edge("safe", "SOFTWARE_AND_SERVICES"), None, "malformed"])
    classification = next(row for row in entity.metadata if row.name == "operates_in_industry")
    assert classification.value == ["SOFTWARE_AND_SERVICES"]
    assert [str(source.url) for source in classification.sources] == ["https://synthetic.example.test/safe"]


def test_missing_industry_does_not_infer_sector_or_country_from_description():
    for edges in ([], {}, [edge("empty", "", sourced=True)]):
        entity = read(edges)
        assert not any(row.name == "operates_in_industry" for row in entity.metadata)
        assert entity.country is None
