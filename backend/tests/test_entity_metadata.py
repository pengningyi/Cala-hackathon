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


def test_registered_address_is_last_country_fallback_and_unsourced_headquarters_stays_unknown():
    registered = parse_entity(payload(registered_address=sourced("Port Louis, Mauritius")), AT)
    assert registered.country == "Mauritius" and registered.country_basis == "registered_address"
    unsourced = parse_entity(payload(headquarters_address={"value": "Paris, France", "sources": []},
                                     website=sourced("example.test")), AT)
    assert unsourced.country is None and unsourced.country_sources == []


@pytest.mark.parametrize("address", ["Menlo Park, CA", "1 Road, California", "1 Road, 94025", "1 Road, UK", "Unknown place, Atlantica"])
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


def test_headquarters_wins_over_office_and_registration_without_borrowing_their_sources():
    entity = parse_entity(payload(
        headquarters_address=sourced("1 Road, Paris, France", path="hq"),
        office_address=sourced("2 Road, London, United Kingdom", path="office"),
        registered_address=sourced("Port Louis, Mauritius", path="registered"),
    ), AT)
    assert entity.country == "France" and entity.country_basis == "headquarters_address"
    assert [str(item.url) for item in entity.country_sources] == ["https://synthetic.example.test/hq"]
    assert entity.office_address == "2 Road, London, United Kingdom"


@pytest.mark.parametrize("office", [
    "1 Road, Paris, France",
    {"streetAddress": "1 Road", "city": "Paris", "addressCountry": "FR"},
    [{"city": "Paris", "country_code": "FRA"}, {"city": "Lyon", "country": "France"}],
])
def test_office_address_country_is_preferred_over_registration(office):
    entity = parse_entity(payload(office_address=sourced(office, path="office"),
                                  registered_address=sourced("Port Louis, Mauritius", path="registered")), AT)
    assert entity.country == "France" and entity.country_basis == "office_address"
    assert [str(item.url) for item in entity.country_sources] == ["https://synthetic.example.test/office"]
    assert entity.office_address and "France" in entity.office_address


@pytest.mark.parametrize("office", ["Menlo Park, CA", "Unknown place, Atlantica", {"streetAddress": "Office address without a country"}])
def test_present_but_unresolved_office_address_never_falls_through_to_registration(office):
    entity = parse_entity(payload(office_address=sourced(office, path="office"),
                                  registered_address=sourced("Port Louis, Mauritius", path="registered")), AT)
    assert entity.country is None and entity.country_basis == "office_address"
    assert [str(item.url) for item in entity.country_sources] == ["https://synthetic.example.test/office"]


def test_unsourced_office_blocks_registration_without_borrowing_generic_or_registration_evidence():
    entity = parse_entity(payload(office_address={"value": "Paris, France", "sources": []},
                                  registered_address=sourced("Port Louis, Mauritius", path="registered"),
                                  description=sourced("An office in France", path="description")), AT)
    assert entity.country is None and entity.country_basis == "office_address" and entity.country_sources == []


@pytest.mark.parametrize("office", [None, "", [], {}])
def test_absent_or_empty_office_allows_registration_country_fallback(office):
    entity = parse_entity(payload(office_address=sourced(office, path="empty-office"),
                                  registered_address=sourced("Port Louis, Mauritius", path="registered")), AT)
    assert entity.country == "Mauritius" and entity.country_basis == "registered_address"
    assert [str(item.url) for item in entity.country_sources] == ["https://synthetic.example.test/registered"]


def test_conflicting_offices_remain_unknown_and_cannot_be_resolved_by_registration():
    entity = parse_entity(payload(office_addresses={"value": [
        sourced({"city": "Paris", "country": "FR"}, path="office-fr"),
        sourced({"city": "Berlin", "country": "DE"}, path="office-de"),
    ], "sources": []}, registered_address=sourced("Paris, France", path="registered")), AT)
    assert entity.country is None and entity.country_basis == "conflicting_metadata"
    assert {str(item.url) for item in entity.country_sources} == {
        "https://synthetic.example.test/office-fr", "https://synthetic.example.test/office-de"}


def test_higher_priority_headquarters_conflict_is_not_hidden_by_office_fallback():
    entity = parse_entity(payload(country=sourced("US", path="country"),
                                  headquarters_address=sourced("Paris, France", path="hq"),
                                  office_address=sourced("Madrid, Spain", path="office")), AT)
    assert entity.country is None and entity.country_basis == "conflicting_metadata"
    assert {str(item.url) for item in entity.country_sources} == {
        "https://synthetic.example.test/country", "https://synthetic.example.test/hq"}


def test_registration_country_property_relationship_and_address_keep_specific_provenance():
    prop = parse_entity(payload(registration_country=sourced("MU", path="registration-country")), AT)
    assert prop.country == "Mauritius" and prop.country_basis == "registration_country_property"
    data = payload()
    data["relationships"] = {"outgoing": {"IS_REGISTERED_IN": [relation("France", path="registration-relation")]}}
    rel = parse_entity(data, AT)
    assert rel.country == "France" and rel.country_basis == "registration_relationship"
    assert str(rel.country_sources[0].url) == "https://synthetic.example.test/registration-relation"
    address = parse_entity(payload(registration_address=sourced({"city": "Port Louis", "country": "MU"}, path="address")), AT)
    assert address.country == "Mauritius" and address.country_basis == "registration_address"
    assert address.registered_address == "Port Louis, Mauritius"


def test_expired_registration_relation_is_not_used_and_registration_conflicts_stay_unknown():
    data = payload()
    data["relationships"] = {"outgoing": {"IS_REGISTERED_IN": [relation("France", valid_until="2020-01-01")]}}
    assert parse_entity(data, AT).country is None
    conflict = parse_entity(payload(registered_country=sourced("FR", path="reg-country"),
                                    registered_address=sourced("Port Louis, Mauritius", path="reg-address")), AT)
    assert conflict.country is None and conflict.country_basis == "conflicting_metadata"


@pytest.mark.parametrize("address", [
    "2200 Sand Hill Road, Menlo Park, California 94025",
    "1 Road, CA 94025",
    "1 Road, New York 10001-1234",
])
def test_terminal_us_state_and_zip_resolve_sourced_registration_address(address):
    entity = parse_entity(payload(registered_address=sourced(address, path="real-shape")), AT)
    assert entity.country == "United States" and entity.country_basis == "registered_address"
    assert str(entity.country_sources[0].url) == "https://synthetic.example.test/real-shape"


def test_us_state_zip_never_overrides_explicit_country_or_uses_description():
    string = parse_entity(payload(office_address=sourced("1 Road, CA 94025, United Kingdom")), AT)
    assert string.country == "United Kingdom"
    structured = parse_entity(payload(office_address=sourced({"region": "California", "postalCode": "94025", "country": "FR"})), AT)
    assert structured.country == "France"
    unknown = parse_entity(payload(office_address=sourced({"region": "California", "postalCode": "94025", "country": "Atlantica"})), AT)
    assert unknown.country is None
    description = parse_entity(payload(description=sourced("California 94025", path="description")), AT)
    assert description.country is None


def test_nested_address_fields_require_their_own_country_or_both_state_zip_sources():
    unsourced_country = parse_entity(payload(office_address={"value": {
        "country": "France", "region": sourced("California", path="region"),
        "postalCode": sourced("94025", path="postal"),
    }, "sources": []}), AT)
    assert unsourced_country.country is None and unsourced_country.country_basis == "office_address"
    assert {str(item.url) for item in unsourced_country.country_sources} == {
        "https://synthetic.example.test/region", "https://synthetic.example.test/postal"}
    state_only = parse_entity(payload(office_address={"value": {
        "region": sourced("California", path="region"), "postalCode": "94025",
    }, "sources": []}), AT)
    assert state_only.country is None
    both = parse_entity(payload(office_address={"value": {
        "region": sourced("California", path="region"), "postalCode": sourced("94025", path="postal"),
    }, "sources": []}), AT)
    assert both.country == "United States" and both.country_basis == "office_address"
    assert {str(item.url) for item in both.country_sources} == {
        "https://synthetic.example.test/region", "https://synthetic.example.test/postal"}
    assert both.office_address == "California, 94025"


def test_per_office_country_sources_are_not_borrowed_from_other_offices():
    entity = parse_entity(payload(office_addresses={"value": [
        {"city": "Paris", "country": sourced("FR", path="fr-country")},
        {"city": "Berlin", "country": "DE", "streetAddress": sourced("1 Road", path="de-street")},
    ], "sources": []}), AT)
    assert entity.country == "France" and entity.country_basis == "office_address"
    assert [str(item.url) for item in entity.country_sources] == ["https://synthetic.example.test/fr-country"]


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
