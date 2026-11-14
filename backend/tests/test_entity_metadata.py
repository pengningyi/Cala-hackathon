"""Country/category normalization must keep each field's own evidence."""
from datetime import datetime, timezone

import pytest

from app.entity_metadata import parse_entity


AT = datetime(2026, 11, 14, tzinfo=timezone.utc)


def sourced(value, *, path="metadata", date="2026-10-01"):
    return {"value": value, "sources": [{"name": "Synthetic evidence", "document": f"https://synthetic.example.test/{path}", "date": date}]}


def payload(**properties):
    return {"id": "synthetic-fund", "name": "Synthetic fund", "entity_type": "Company", "properties": properties}


def relation(name, *, kind="Country", path="headquarters", **extra):
    return {"id": f"synthetic-{name}", "name": name, "entity_type": kind,
            "properties": {"sources": sourced(None, path=path)["sources"], **extra}}


def test_full_metadata_extracts_headquarters_country_without_using_registered_jurisdiction():
    data = payload(headquarters_address=sourced("1 Test Road, Menlo Park, CA 94025, United States", path="hq"),
                   registered_address=sourced("2 Test Road, Port Louis, Mauritius", path="registered"),
                   website=sourced("lsvp.example.test", path="website"),
                   legal_name=sourced("Synthetic Fund LP", path="legal"))
    entity = parse_entity(data, AT, metadata_retrieved=True)
    assert entity.country == "United States"
    assert entity.country_basis == "headquarters_address"
    assert [str(item.url) for item in entity.country_sources] == ["https://synthetic.example.test/hq"]
    assert entity.registered_address.endswith("Mauritius")
    assert str(entity.website) == "https://lsvp.example.test/"
    assert entity.metadata_status == "retrieved" and entity.metadata_retrieved_at == AT
    assert next(item for item in entity.metadata if item.name == "legal_name").value == "Synthetic Fund LP"


def test_registered_address_and_unsourced_headquarters_do_not_become_country():
    registered = parse_entity(payload(registered_address=sourced("Port Louis, Mauritius")), AT)
    assert registered.country is None and registered.country_basis is None
    unsourced = parse_entity(payload(headquarters_address={"value": "Paris, France", "sources": []},
                                     website=sourced("example.test")), AT)
    assert unsourced.country is None and unsourced.country_sources == []


@pytest.mark.parametrize("address", ["Menlo Park, CA", "1 Road, CA 94025", "1 Road, UK", "Unknown place, Atlantica"])
def test_address_tail_must_be_an_exact_full_country_name(address):
    entity = parse_entity(payload(headquarters_address=sourced(address)), AT)
    assert entity.country is None


def test_structured_headquarters_and_explicit_country_codes_are_normalized():
    data = payload(headquarters_address=sourced({"streetAddress": "1 Road", "city": "Port Louis", "country": "MU"}),
                   country=sourced("MUS"))
    entity = parse_entity(data, AT)
    assert entity.country == "Mauritius"
    assert entity.headquarters_address == "1 Road, Port Louis, Mauritius"


def test_iso_address_country_code_requires_a_matching_valid_subdivision_in_same_hq_address():
    data = payload(headquarters_address=sourced("6th Floor, Trianon, Quatre Bornes, MU-PW, 72261, MU", path="hq"))
    entity = parse_entity(data, AT)
    assert entity.country == "Mauritius" and entity.country_basis == "headquarters_address"
    assert str(entity.country_sources[0].url).endswith("/hq")
    mismatch = parse_entity(payload(headquarters_address=sourced("1 Road, US-CA, 72261, MU")), AT)
    assert mismatch.country is None


def test_conflicting_country_observations_stay_unknown_with_evidence_retained():
    data = payload(country=sourced("US", path="country"),
                   headquarters_address=sourced("Paris, France", path="hq"))
    entity = parse_entity(data, AT)
    assert entity.country is None and entity.country_basis == "conflicting_metadata"
    assert {str(item.url) for item in entity.country_sources} == {
        "https://synthetic.example.test/country", "https://synthetic.example.test/hq"}


def test_country_relationship_is_sourced_and_expired_edges_do_not_assert_current_country():
    data = payload()
    data["relationships"] = {"outgoing": {"HAS_HEADQUARTERS_IN": [relation("Mauritius")]}}
    entity = parse_entity(data, AT)
    assert entity.country == "Mauritius" and entity.country_basis == "headquarters_relationship"
    data["relationships"]["outgoing"]["HAS_HEADQUARTERS_IN"][0]["properties"]["valid_until"] = "2020-01-01"
    assert parse_entity(data, AT).country is None


def test_legal_entity_type_and_description_do_not_invent_investor_category():
    data = payload(description={"value": "A venture capital firm in France", "sources": []})
    entity = parse_entity(data, AT)
    assert entity.entity_type == "Company"
    assert entity.kind == "unknown" and entity.kind_sources == []
    assert entity.country is None


def test_explicit_sourced_investor_category_and_exact_industry_classification():
    entity = parse_entity(payload(investor_type=sourced("CORPORATE_VC", path="category")), AT)
    assert entity.kind == "corporate_vc" and entity.kind_basis == "explicit_category"
    assert str(entity.kind_sources[0].url).endswith("/category")
    data = payload()
    data["relationships"] = {"outgoing": {"OPERATES_IN_INDUSTRY": [
        relation("FINANCIAL_SERVICES", kind="Industry", path="industry", original_label="Venture capital firm")]}}
    entity = parse_entity(data, AT)
    assert entity.kind == "vc" and entity.kind_basis == "industry_classification"
    assert str(entity.kind_sources[0].url).endswith("/industry")
    del data["relationships"]["outgoing"]["OPERATES_IN_INDUSTRY"][0]["properties"]["original_label"]
    assert parse_entity(data, AT).kind == "unknown"


def test_conflicting_or_unsourced_investor_categories_stay_unknown():
    conflict = parse_entity(payload(investor_type=sourced("VC"), fund_type=sourced("Corporate venture capital")), AT)
    assert conflict.kind == "unknown" and conflict.kind_basis == "conflicting_metadata"
    unsourced = parse_entity(payload(investor_type={"value": "VC", "sources": []}, name=sourced("Named fund")), AT)
    assert unsourced.kind == "unknown" and unsourced.kind_sources == []


def test_all_property_values_and_their_source_dates_are_preserved():
    data = payload(lei=sourced("Synthetic identifier", path="lei", date="2024-03-05"),
                   arbitrary_property=sourced({"nested": [1, "two", None]}, path="extra"),
                   empty_property={"value": None, "sources": []})
    entity = parse_entity(data, AT, metadata_retrieved=True)
    rows = {item.name: item for item in entity.metadata}
    assert rows["arbitrary_property"].value == {"nested": [1, "two", None]}
    assert rows["empty_property"].value is None and rows["empty_property"].sources == []
    assert rows["lei"].sources[0].published_at.isoformat() == "2024-03-05"
    assert rows["lei"].sources[0].retrieved_at == AT


@pytest.mark.parametrize("website", ["javascript:alert(1)", "https://user:password@example.test", "not a url"])
def test_invalid_or_credential_bearing_websites_are_not_linked(website):
    assert parse_entity(payload(website=sourced(website)), AT).website is None
