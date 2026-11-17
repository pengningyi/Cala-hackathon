"""Actual tool observations survive request-local source IDs without changing scores."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import app.research_evidence as evidence_module
from app.models import Source
from app.research_evidence import ResearchEvidenceJournal, source_references


NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def source(name, **updates):
    return Source(url=f"https://example.test/{name}", retrieved_at=NOW, label=name, **updates)


def observation(identifier="s1", name="Alpha"):
    return {"tool": "search_entities", "arguments": {"query": name, "kind": "investor"},
            "result": [{"id": name, "name": name, "source_ids": [identifier]}]}


def test_two_request_local_source_ids_remain_distinct_and_snapshots_are_isolated():
    journal = ResearchEvidenceJournal()
    first = observation()
    first_source = source("first")
    assert journal.record([first], {"s1": first_source}) == 1
    frozen = journal.snapshot()
    assert journal.record([observation(name="Beta")], {"s1": source("second")}) == 2
    current = journal.snapshot()
    assert [item["result"][0]["source_ids"] for item in current.observations] == [["r1"], ["r2"]]
    assert str(current.sources["r1"].url).endswith("/first")
    assert str(current.sources["r2"].url).endswith("/second")
    assert frozen.revision == 1 and len(frozen.observations) == 1
    first["result"][0]["name"] = "mutated input"
    first_source.label = "mutated source"
    current.observations[0]["result"].clear()
    current.sources["r1"].label = "mutated snapshot"
    assert journal.snapshot().observations[0]["result"][0]["name"] == "Alpha"
    assert journal.snapshot().sources["r1"].label == "first"


def test_cached_or_refetched_identical_facts_and_plain_chat_do_not_advance_revision():
    journal = ResearchEvidenceJournal()
    journal.record([observation("s3")], {"s3": source("first")})
    refetched = source("first").model_copy(update={"retrieved_at": NOW + timedelta(minutes=1)})
    assert journal.record([observation("s99")], {"s99": refetched}) == 1
    assert journal.record([], {}) == 1
    assert journal.status()["observation_count"] == 1
    changed = observation("s99")
    changed["result"][0]["description"] = "Newly retrieved portfolio detail"
    assert journal.record([changed], {"s99": refetched}) == 2
    assert len(journal.snapshot().sources) == 1


def test_failed_unsourced_unregistered_and_model_generated_findings_are_not_saved():
    journal = ResearchEvidenceJournal()
    failed = {**observation(), "error": "provider unavailable"}
    unsupported = observation("s999")
    unsourced = observation()
    unsourced["result"][0]["source_ids"] = []
    model_findings = {"findings": [{"text": "Generated claim", "source_ids": ["s1"]}]}
    assert journal.record([failed, unsupported, unsourced, model_findings], {"s1": source("valid")}) == 0
    assert journal.snapshot().observations == [] and journal.snapshot().sources == {}


def test_bounded_storage_keeps_new_observations_and_prunes_evicted_sources(monkeypatch):
    monkeypatch.setattr(evidence_module, "MAX_OBSERVATIONS", 2)
    journal = ResearchEvidenceJournal()
    for name in ("first", "second", "third"):
        journal.record([observation(name=name)], {"s1": source(name)})
    snapshot = journal.snapshot()
    assert [item["result"][0]["name"] for item in snapshot.observations] == ["second", "third"]
    assert set(source_references(snapshot.observations)) == set(snapshot.sources)
    assert snapshot.truncated and journal.status()["truncated"]


def test_journals_are_scoped_to_one_run_and_snapshot_source_maps_are_authoritative():
    first, second = ResearchEvidenceJournal(), ResearchEvidenceJournal()
    value = {**observation(), "sources": {"s1": {"url": "https://forged.example/"}},
             "research": {"findings": ["Untrusted generated summary"]}}
    before = deepcopy(value)
    first.record([value], {"s1": source("real")})
    snapshot = first.snapshot()
    assert "sources" not in snapshot.observations[0] and "research" not in snapshot.observations[0]
    assert str(snapshot.sources["r1"].url) == "https://example.test/real"
    assert second.revision == 0 and second.snapshot().observations == []
    assert value == before


def test_malformed_nested_source_fields_do_not_break_or_partially_mutate_the_journal():
    journal = ResearchEvidenceJournal()
    invalid = observation()
    invalid["result"][0]["metadata"] = {"source_ids": ["s1", 17]}
    assert journal.record([invalid], {"s1": source("real")}) == 0
    assert journal.snapshot().sources == {}
    assert journal.record([observation()], {"s1": source("real")}) == 1
    assert list(journal.snapshot().sources) == ["r1"]
