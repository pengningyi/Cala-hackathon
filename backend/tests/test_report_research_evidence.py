"""Reports consume only frozen server-recorded Cala observations, never chat claims."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

import app.report_agent as report_module
from app.chat_model import ChatModelError
from app.models import Source
from app.report_agent import RESEARCH_PREFIX, ReportAgent, ReportRequest
from app.research_evidence import ResearchEvidenceJournal, ResearchEvidenceSnapshot
from test_chat import saved_result  # noqa: F401 (fixture)
from test_report_agent import Generator, config


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LIVE_URL = "https://news.example.test/research-fund"


def source(url=LIVE_URL, label="Registered Cala source"):
    return Source(url=url, retrieved_at=NOW, label=label)


def observation(ref="r1", **result_overrides):
    return {"tool": "get_entity_details", "arguments": {"entity_id": "research-fund", "is_company": False},
            "result": {"id": "research-fund", "name": "Research Fund", "source_ids": [ref], **result_overrides}}


def snapshot(observations=None, sources=None, revision=7, **kwargs):
    return ResearchEvidenceSnapshot(revision=revision, observations=observations if observations is not None else [observation()],
                                    sources=sources if sources is not None else {"r1": source()}, updated_at=NOW, **kwargs)


def envelope(messages):
    item = next(item for item in messages if item["content"].startswith(RESEARCH_PREFIX))
    return json.loads(item["content"][len(RESEARCH_PREFIX):])


class EvidenceGenerator(Generator):
    def __init__(self, render=None):
        super().__init__()
        self.render = render or (lambda packet, ref: {
            "message": f"## Saved Cala research\nResearch Fund appears in the registered evidence. [{ref}]",
            "source_ids": [ref], "candidate_ids": [],
        })

    async def generate(self, messages):
        self.messages.append(deepcopy(messages))
        packet = envelope(messages)
        ref = packet["observations"][0]["result"]["source_ids"][0]
        return json.dumps(self.render(packet, ref))


def test_report_remaps_research_ids_and_country_refs_without_source_collisions(saved_result):
    research = snapshot([observation(website="https://research-fund.example.test/", country="Finland",
                                     country_basis="office", country_source_ids=["r2"])], {
        "r1": source(), "r2": source("https://registry.example.test/office"),
        "r99": source("https://unused.example.test/unregistered-in-context"),
    })
    before = deepcopy(research)
    saved_before = saved_result.model_dump_json()
    transport = EvidenceGenerator(lambda packet, ref: {
        "message": f"Research Fund's recorded website is https://research-fund.example.test/. [{ref}]",
        "source_ids": [ref], "candidate_ids": [],
    })
    reply = asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))

    packet = envelope(transport.messages[0])
    entity = packet["observations"][0]["result"]
    saved_catalog = json.loads(transport.messages[0][1]["content"].split("\n", 1)[1])["source_catalog"]
    assert entity["source_ids"][0] not in saved_catalog
    assert entity["country_source_ids"][0] != entity["source_ids"][0]
    assert all(ref.startswith("s") for ref in packet["sources"])
    assert packet["sources"][entity["source_ids"][0]]["url"] == LIVE_URL
    assert packet["sources"][entity["country_source_ids"][0]]["url"] == "https://registry.example.test/office"
    assert "unused.example.test" not in json.dumps(packet)
    assert reply.evidence_revision == 7 and reply.citation_ids == entity["source_ids"]
    assert [str(item.url) for item in reply.citations] == [LIVE_URL]
    assert "country_basis" in transport.messages[0][0]["content"]
    assert research == before and saved_result.model_dump_json() == saved_before


def test_research_and_saved_source_deduplicate_in_one_catalog(saved_result):
    existing = saved_result.candidates[0].investor.sources[0]
    research = snapshot(sources={"r1": existing.model_copy(deep=True)})
    transport = EvidenceGenerator()
    reply = asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))
    assert envelope(transport.messages[0])["observations"][0]["result"]["source_ids"] == ["s1"]
    assert reply.citation_ids == ["s1"] and reply.citations == [existing]


@pytest.mark.parametrize("invalid", [
    lambda packet, ref: {"message": "Internal journal ref", "source_ids": ["r1"], "candidate_ids": []},
    lambda packet, ref: {"message": "New shortlist investor", "source_ids": [ref], "candidate_ids": ["research-fund"]},
    lambda packet, ref: {"message": "Visit https://invented.example.test/", "source_ids": [ref], "candidate_ids": []},
])
def test_report_rejects_internal_refs_extra_candidates_and_arbitrary_urls(saved_result, invalid):
    transport = EvidenceGenerator(invalid)
    with pytest.raises(ChatModelError) as error:
        asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=snapshot()))
    assert error.value.code == "report_invalid_output" and len(transport.messages) == 2
    assert envelope(transport.messages[0]) == envelope(transport.messages[1])


def test_urls_in_tool_text_and_arguments_do_not_become_allowed_entity_websites(saved_result):
    research = snapshot([{"tool": "knowledge_search", "arguments": {"query": "https://argument.example.test/"},
                          "result": {"text": "Claimed site https://prose.example.test/", "source_ids": ["r1"]}}])
    for url in ("https://argument.example.test/", "https://prose.example.test/"):
        transport = EvidenceGenerator(lambda packet, ref: {"message": "Visit " + url,
                                                           "source_ids": [ref], "candidate_ids": []})
        with pytest.raises(ChatModelError) as error:
            asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))
        assert error.value.code == "report_invalid_output"


def test_generated_summaries_and_history_cannot_add_report_evidence(saved_result):
    valid = observation()
    valid["research"] = {"findings": [{"text": "GENERATED_SUMMARY_CLAIM", "source_ids": ["r1"]}]}
    research = snapshot([{"tool": "generated_summary", "findings": ["ONLY_GENERATED_CLAIM"]}, valid])
    transport = EvidenceGenerator()
    history = [{"role": "assistant", "content": "HISTORY_ONLY_CLAIM [history-ref] https://history.example.test/"}]
    asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(history=history), research=research))
    packet = envelope(transport.messages[0])
    serialized = json.dumps(packet)
    assert "GENERATED_SUMMARY_CLAIM" not in serialized and "ONLY_GENERATED_CLAIM" not in serialized
    assert "HISTORY_ONLY_CLAIM" not in serialized and "history-ref" not in serialized
    assert packet["observations_omitted"] == 1 and packet["truncated"] is True
    assert "Only actual observation results" in transport.messages[0][0]["content"]


@pytest.mark.parametrize("bad_refs", [["r404"], ["r1", 123]])
def test_unresolvable_observations_do_not_register_partial_sources(saved_result, bad_refs):
    research = snapshot([observation(source_ids=bad_refs)])
    transport = Generator()
    reply = asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))
    packet = envelope(transport.messages[0])
    assert packet["observations"] == [] and packet["sources"] == {}
    assert packet["observations_omitted"] == 1 and packet["truncated"] is True
    assert reply.evidence_revision == research.revision


def test_report_trims_older_whole_observations_to_fit_existing_context(saved_result, monkeypatch):
    baseline = Generator()
    asyncio.run(ReportAgent(config(), baseline).reply(saved_result, ReportRequest()))
    limit = sum(len(item["content"]) for item in baseline.messages[0]) + 3000
    monkeypatch.setattr(report_module, "MAX_PROMPT_CHARACTERS", limit)
    research = snapshot([observation("r-old", description="OLD_EVIDENCE " * 1000),
                         observation("r-new", name="Recent Fund")], {
        "r-old": source("https://old.example.test/"), "r-new": source("https://new.example.test/"),
    })
    transport = EvidenceGenerator()
    reply = asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))
    packet = envelope(transport.messages[0])
    assert len(packet["observations"]) == 1 and packet["observations"][0]["result"]["name"] == "Recent Fund"
    assert packet["observations_omitted"] == 1 and packet["truncated"] is True
    assert "old.example.test" not in json.dumps(packet)
    assert sum(len(item["content"]) for item in transport.messages[0]) <= limit
    assert [str(item.url) for item in reply.citations] == ["https://new.example.test/"]


def test_saved_context_with_no_research_headroom_still_generates_report(saved_result, monkeypatch):
    baseline = Generator()
    asyncio.run(ReportAgent(config(), baseline).reply(saved_result, ReportRequest()))
    limit = sum(len(item["content"]) for item in baseline.messages[0]) + 10
    monkeypatch.setattr(report_module, "MAX_PROMPT_CHARACTERS", limit)
    transport = Generator()
    reply = asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=snapshot()))
    assert not any(item["content"].startswith(RESEARCH_PREFIX) for item in transport.messages[0])
    assert "research omitted for context size" in transport.messages[0][-1]["content"]
    assert sum(len(item["content"]) for item in transport.messages[0]) <= limit
    assert reply.evidence_revision == 7 and reply.citation_ids == ["s1"]


def test_research_projection_reserves_context_for_a_large_validation_repair(saved_result, monkeypatch):
    latest = observation("r-new", description="x" * 4000)
    research_sources = {"r-old": source("https://old.example.test/"),
                        "r-new": source("https://new.example.test/")}
    baseline = EvidenceGenerator()
    asyncio.run(ReportAgent(config(), baseline).reply(
        saved_result, ReportRequest(), research=snapshot([latest], research_sources)))
    one_observation_size = sum(len(item["content"]) for item in baseline.messages[0])
    limit = one_observation_size + 1530
    monkeypatch.setattr(report_module, "MAX_PROMPT_CHARACTERS", limit)
    research = snapshot([observation("r-old", description="older" * 100), latest], research_sources)

    class RepairGenerator(EvidenceGenerator):
        async def generate(self, messages):
            if not self.messages:
                self.messages.append(deepcopy(messages))
                return json.dumps({"message": "Invalid source", "source_ids": ["unregistered-" + "x" * 1400],
                                   "candidate_ids": []})
            return await super().generate(messages)

    transport = RepairGenerator()
    reply = asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))
    assert reply.model_used is True and len(transport.messages) == 2
    assert len(envelope(transport.messages[0])["observations"]) == 1
    assert envelope(transport.messages[0])["truncated"] is True
    assert sum(len(item["content"]) for item in transport.messages[0]) <= limit - 1500
    assert sum(len(item["content"]) for item in transport.messages[1]) <= limit


def test_source_capacity_rejects_whole_observation_without_leaking_registered_refs(saved_result):
    references = [f"r{index}" for index in range(1, 34)]
    research = snapshot([observation(source_ids=references)], {
        ref: source(f"https://source.example.test/{ref}") for ref in references
    })
    transport = Generator()
    asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))
    packet = envelope(transport.messages[0])
    assert packet["observations"] == [] and packet["sources"] == {} and packet["truncated"] is True


def test_report_caps_observations_and_prefers_the_latest_registered_evidence(saved_result):
    research = snapshot([observation(name=f"Fund {index}") for index in range(15)])
    transport = EvidenceGenerator()
    asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=research))
    packet = envelope(transport.messages[0])
    assert len(packet["observations"]) == 12 and packet["observations_omitted"] == 3
    assert [item["result"]["name"] for item in packet["observations"]] == [f"Fund {index}" for index in range(3, 15)]


def test_report_revision_and_citations_stay_frozen_while_journal_advances_and_answer_repairs(saved_result):
    journal = ResearchEvidenceJournal()
    journal.record([observation("request-source")], {"request-source": source(label="Original label")})
    frozen = journal.snapshot()

    class AdvancingGenerator(EvidenceGenerator):
        async def generate(self, messages):
            if not self.messages:
                self.messages.append(deepcopy(messages))
                journal.record([observation("next-source", name="Later Fund")], {
                    "next-source": source("https://later.example.test/"),
                })
                # Nested snapshot containers are mutable despite the frozen dataclass.
                # The writer's own copy must remain stable even under this mutation.
                frozen.sources["r1"].label = "Mutated after report start"
                frozen.observations.clear()
                return json.dumps({"message": "Bad source", "source_ids": ["not-registered"], "candidate_ids": []})
            return await super().generate(messages)

    transport = AdvancingGenerator()
    reply = asyncio.run(ReportAgent(config(), transport).reply(saved_result, ReportRequest(), research=frozen))
    assert journal.snapshot().revision == 2 and reply.evidence_revision == 1
    assert len(transport.messages) == 2
    first, repaired = (envelope(messages) for messages in transport.messages)
    assert first == repaired and first["revision"] == 1
    assert "later.example.test" not in json.dumps(repaired)
    assert reply.citations[0].label == "Original label"
