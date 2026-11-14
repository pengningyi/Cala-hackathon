"""The graph receives existing sourced people facts without changing scoring."""
import asyncio
from datetime import date

from app import pipeline
from app.cala import CalaError
from app.models import Entity, EntityMetadataProperty, GraphSnapshot, InvestorProfile, Role, RunResult, Source
from app.pipeline import Workflow, _serialized_people
from app.runtime import BudgetExceeded
from test_chat import saved_result
from test_workflow import SyntheticProvider, complete_run, entity, evidence, founder, funding, settings


def person(identifier="founder-person", **updates):
    return Entity(id=identifier, name="Same Person Name", entity_type="Person", **updates)


def role(person_entity, org, *, kind="works_at", current=False, refs=None, since=date(2020, 1, 1), until=None):
    return Role(person=person_entity, org=org, kind=kind, current=current,
                valid_since=since, valid_until=until, sources=[evidence()] if refs is None else refs)


def test_old_results_default_to_empty_people_and_round_trip_sourced_roles(saved_result):
    old = saved_result.model_dump(mode="json")
    old.pop("people", None)
    assert RunResult.model_validate(old).people == []
    schema = RunResult.model_json_schema()
    assert "people" not in schema["required"] and schema["properties"]["people"]["items"]["$ref"].endswith("/Role")
    original = role(person(), entity("organisation"), kind="founded", current=False, until=date(2022, 1, 1))
    saved_result.people = [original]
    restored = RunResult.model_validate_json(saved_result.model_dump_json())
    assert restored.people == [original]
    assert restored.people[0].sources == original.sources
    assert restored.people[0].valid_until == date(2022, 1, 1) and restored.people[0].current is False


def test_duplicate_roles_merge_own_sources_and_richer_entities_without_merging_names():
    first_source = Source.model_validate({**evidence().model_dump(), "url": "https://synthetic.example.test/first"})
    second_source = Source.model_validate({**evidence().model_dump(), "url": "https://synthetic.example.test/second"})
    org = entity("organisation")
    first_person = person(metadata=[EntityMetadataProperty(name="description", value="First fact", sources=[first_source])])
    second_person = person(website="https://person.example.test/", metadata=[
        EntityMetadataProperty(name="description", value="First fact", sources=[second_source]),
        EntityMetadataProperty(name="custom", value={"active": False, "since": "2020"}, sources=[second_source]),
    ])
    one = role(first_person, org, refs=[first_source])
    two = role(second_person, org, refs=[second_source])
    another_identity = role(person("different-id"), org, refs=[first_source])
    explicitly_current = one.model_copy(update={"current": True})
    another_date = one.model_copy(update={"valid_since": date(2021, 1, 1)})
    unsourced = role(person("not-role-sourced", sources=[first_source]), org, refs=[])
    inputs = [one, two, another_identity, explicitly_current, another_date, unsourced]
    before = [item.model_dump_json() for item in inputs]
    merged = _serialized_people(inputs)
    assert [item.model_dump_json() for item in merged] == [item.model_dump_json() for item in _serialized_people(list(reversed(inputs)))]
    assert len(merged) == 4
    combined = next(item for item in merged if item.person.id == one.person.id and not item.current and item.valid_since == one.valid_since)
    assert {str(item.url) for item in combined.sources} == {str(first_source.url), str(second_source.url)}
    assert str(combined.person.website) == "https://person.example.test/"
    description = next(item for item in combined.person.metadata if item.name == "description")
    assert {str(item.url) for item in description.sources} == {str(first_source.url), str(second_source.url)}
    assert next(item for item in combined.person.metadata if item.name == "custom").value == {"active": False, "since": "2020"}
    assert any(item.person.id == "different-id" for item in merged)
    assert not any(item.person.id == "not-role-sourced" for item in merged)
    assert [item.model_dump_json() for item in inputs] == before


def people_fixture():
    startup, lead, candidate = entity("startup", kind="company"), entity("lead"), entity("candidate")
    previous = entity("previous", kind="company")
    founder_role = role(person("founder"), startup, kind="founded")
    previous_role = role(founder_role.person, previous, kind="founded", until=date(2021, 1, 1))
    lead_role = role(person("lead-person"), lead)
    candidate_role = role(person("candidate-person"), candidate, current=True)
    graph = GraphSnapshot(rounds=[funding("shared", [lead, candidate])], complete=True, people=[lead_role])

    class PeopleProvider(SyntheticProvider):
        async def search_entities(self, query, kind):
            if kind == "company":
                self.calls.append(("search_entities", query, kind))
                return [startup.model_copy(deep=True)]
            return await super().search_entities(query, kind)

        async def entity_people(self, entity_id):
            self.calls.append(("entity_people", entity_id))
            return [founder_role.model_copy(deep=True)] if entity_id == startup.id else []

        async def person_roles(self, person_id):
            self.calls.append(("person_roles", person_id))
            return [previous_role.model_copy(deep=True)]

    def provider():
        return PeopleProvider(graph=graph, profiles={candidate.id: InvestorProfile(investor=candidate, people=[candidate_role])})

    profile = founder()
    profile.startup.cala_entity_id = startup.id
    return provider, profile, [founder_role, previous_role, lead_role, candidate_role]


def test_workflow_serializes_founder_lead_candidate_and_intermediate_roles_without_extra_calls_or_score_changes(tmp_path, monkeypatch):
    async def scenario():
        make_provider, profile, expected = people_fixture()
        provider = make_provider()
        _, record = await complete_run(Workflow(settings(tmp_path), provider), profile, lookup_startup=True)
        assert record.result.state == "completed"
        assert record.result.people == _serialized_people(expected)
        assert {item.org.id for item in record.result.people} == {"startup", "previous", "lead", "candidate"}
        assert all(item.sources for item in record.result.people)
        assert not next(item for item in record.result.people if item.person.id == "lead-person").current

        # The pre-field serialization path receives identical data and policies.
        monkeypatch.setattr(pipeline, "_serialized_people", lambda roles: [])
        baseline_provider = make_provider()
        _, baseline = await complete_run(Workflow(settings(tmp_path), baseline_provider), profile, lookup_startup=True)
        assert baseline.result.people == []
        assert provider.calls == baseline_provider.calls
        assert record.result.candidates == baseline.result.candidates
        assert record.result.allocation == baseline.result.allocation
        assert record.result.combined_draft == baseline.result.combined_draft
    asyncio.run(scenario())


def test_partial_path_keeps_successfully_retrieved_roles(tmp_path):
    class InterruptedWorkflow(Workflow):
        async def _trust_people(self, *args):
            raise BudgetExceeded("Synthetic interruption after the first wave")

    async def scenario():
        make_provider, profile, expected = people_fixture()
        _, record = await complete_run(InterruptedWorkflow(settings(tmp_path), make_provider()), profile, lookup_startup=True)
        assert record.result.state == "partial"
        assert record.result.people == _serialized_people([expected[0], expected[2], expected[3]])
        assert not any(item.org.id == "previous" for item in record.result.people)
    asyncio.run(scenario())


def test_failed_run_never_fabricates_person_from_founder_form_name(tmp_path):
    class FailingGraph(SyntheticProvider):
        async def lead_graph(self, lead_id, lookback_months):
            raise CalaError("synthetic_graph_failure", "Synthetic graph failure")

    async def scenario():
        profile = founder(founder_name="Founder entered by user")
        _, record = await complete_run(Workflow(settings(tmp_path), FailingGraph()), profile)
        assert record.result.state == "failed" and record.result.people == []
        assert record.result.profile.founder_name == "Founder entered by user"
    asyncio.run(scenario())
