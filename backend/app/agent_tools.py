"""Typed Cala tools shared by the conversational and research agents."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .cala import CalaError
from .chat_model import _clip, _Evidence
from .models import ChatToolStep, Entity, FundingRound, InvestorProfile, KnowledgeAnswer, Role
from .runtime import BudgetExceeded

MAX_TOOL_RESULT_CHARACTERS = 6000
MAX_TOOL_STEPS = 36
StepCallback = Callable[[ChatToolStep], Awaitable[None] | None]

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=100)
    kind: Literal["company", "investor", "person"]


class EntityArgs(_Args):
    entity_id: str = Field(min_length=1, max_length=300)
    is_company: bool = False


class InvestorArgs(_Args):
    investor_id: str = Field(min_length=1, max_length=300)
    lookback_months: int = Field(default=60, ge=1, le=120)


class CompanyRoundsArgs(_Args):
    company_id: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=3, ge=1, le=4)


class OrgArgs(_Args):
    entity_id: str = Field(min_length=1, max_length=300)


class PersonArgs(_Args):
    person_id: str = Field(min_length=1, max_length=300)


class RoundArgs(_Args):
    round_id: str = Field(min_length=1, max_length=300)


class KnowledgeArgs(_Args):
    query: str = Field(min_length=1, max_length=500)


# name -> (provider operation, argument model, result type, description)
TOOL_SPECS: dict[str, tuple[str, type[_Args], Any, str]] = {
    "search_entities": ("search_entities", SearchArgs, list[Entity],
                        "Find Cala entities (company, investor or person) by name. Returns up to 8 matches with IDs."),
    "get_entity_details": ("entity_metadata", EntityArgs, Entity,
                           "Read one entity's properties: description, country, type, industry, website."),
    "get_investor_profile": ("investor_profile", InvestorArgs, InvestorProfile,
                             "Read an investor's funding-round participations in the lookback window, affiliated "
                             "people and announced funds."),
    "get_company_funding_rounds": ("company_rounds", CompanyRoundsArgs, list[FundingRound],
                                   "Read a company's own most recent identified funding rounds with participants."),
    "get_funding_round": ("funding_round", RoundArgs, FundingRound | None,
                          "Read one funding round by ID (e.g. a participation id): issuer company, date, stage, "
                          "EUR amount and all participating investors (the co-investors)."),
    "get_organisation_people": ("entity_people", OrgArgs, list[Role],
                                "List people with sourced roles (employees, members, board, founders) at an organisation."),
    "get_person_roles": ("person_roles", PersonArgs, list[Role],
                         "List a person's sourced roles at organisations, with validity dates."),
    "knowledge_search": ("knowledge_search", KnowledgeArgs, KnowledgeAnswer,
                         "Slow (~30-40 s) sourced natural-language search of Cala's knowledge. Last resort."),
}

FINAL_ANSWER_TOOL = {"type": "function", "function": {
    "name": "final_answer",
    "description": "Return the answer to the founder. Call exactly once, when done.",
    "parameters": {"type": "object", "additionalProperties": False,
                   "required": ["message", "source_ids", "candidate_ids"],
                   "properties": {
                       "message": {"type": "string", "description": "Answer text, max 6000 characters."},
                       "source_ids": {"type": "array", "maxItems": 16, "items": {"type": "string"},
                                      "description": "Exact source IDs (s1, s2, ...) supporting the answer."},
                       "candidate_ids": {"type": "array", "maxItems": 5, "items": {"type": "string"},
                                         "description": "Shortlist investor IDs the answer discusses."},
                   }},
}}


def _schema(model: type[_Args]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    schema["additionalProperties"] = False
    return schema


def tool_definitions(provider, knowledge_search: bool = True) -> list[dict]:
    """Expose only tools the configured Cala adapter implements, plus final_answer."""
    tools = [{"type": "function", "function": {"name": name, "description": description,
                                               "parameters": _schema(args)}}
             for name, (operation, args, _, description) in TOOL_SPECS.items()
             if callable(getattr(provider, operation, None))
             and (knowledge_search or name != "knowledge_search")]
    return [*tools, FINAL_ANSWER_TOOL]


def _label(name: str, args: _Args, names: dict[str, str]) -> str:
    def who(identifier):
        return names.get(identifier) or identifier
    if isinstance(args, SearchArgs):
        return f"Searching Cala for {args.kind}s named “{args.query}”"
    if isinstance(args, KnowledgeArgs):
        return f"Searching Cala knowledge: “{_clip(args.query, 80)}”"
    if isinstance(args, InvestorArgs):
        return f"Reading investor profile of {who(args.investor_id)}"
    if isinstance(args, RoundArgs):
        return f"Reading funding round {who(args.round_id)}"
    if isinstance(args, CompanyRoundsArgs):
        return f"Reading funding rounds of {who(args.company_id)}"
    if isinstance(args, PersonArgs):
        return f"Reading roles of {who(args.person_id)}"
    if isinstance(args, OrgArgs):
        return f"Listing people at {who(args.entity_id)}"
    if isinstance(args, EntityArgs):
        return f"Reading details of {who(args.entity_id)}"
    return name


class _Serializer:
    """Compact, sourced JSON for the model; sources join the shared catalog."""

    def __init__(self, evidence: _Evidence, names: dict[str, str]):
        self.evidence, self.names = evidence, names
        # Entity websites returned by Cala lookups may appear in answers (e.g. contact questions).
        self.websites: set[str] = set()

    def _remember(self, entity: Entity):
        self.names.setdefault(entity.id, entity.name)

    def entity(self, value: Entity) -> dict:
        self._remember(value)
        data = self.evidence.entity(value)
        if value.website:
            data["website"] = str(value.website)
            self.websites.add(str(value.website))
        metadata = {item.name: _clip(item.value, 300) if isinstance(item.value, str) else item.value
                    for item in value.metadata[:12]}
        if metadata:
            data["metadata"] = metadata
        return {key: item for key, item in data.items() if item not in (None, [], {})}

    def brief(self, value: Entity) -> dict:
        self._remember(value)
        return {"id": value.id, "name": value.name}

    def role(self, value: Role) -> dict:
        return {"person": self.brief(value.person), "org": self.brief(value.org), "kind": value.kind,
                "valid_since": str(value.valid_since) if value.valid_since else None,
                "valid_until": str(value.valid_until) if value.valid_until else None,
                "current": value.current, "source_ids": self.evidence.refs(value.sources, limit=2)}

    def rounds(self, values: list[FundingRound]) -> list[dict]:
        for funding in values:
            self._remember(funding.company)
            for participant in funding.participants:
                self._remember(participant.investor)
        return [self.evidence.events[self.evidence.event(funding)] for funding in values]

    def catalog(self, payload: Any) -> dict:
        """The sources a result cites, so their IDs are concrete and citable."""
        found: list[str] = []

        def visit(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("source_ids", "country_source_ids") and isinstance(item, list):
                        found.extend(identifier for identifier in item if isinstance(identifier, str))
                    else:
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        visit(payload)
        return {identifier: {"label": _clip(self.evidence.sources[identifier].label, 120),
                             "url": str(self.evidence.sources[identifier].url),
                             "published_at": str(self.evidence.sources[identifier].published_at or "") or None}
                for identifier in dict.fromkeys(found) if identifier in self.evidence.sources}

    def result(self, value: Any) -> tuple[Any, int | None]:
        if value is None:
            return {"error": "Not a readable funding round (no single identified issuer)."}, None
        if isinstance(value, FundingRound):
            return self.rounds([value])[0], len(value.participants)
        if isinstance(value, KnowledgeAnswer):
            return {"text": value.text, "source_ids": self.evidence.refs(value.sources, limit=8)}, len(value.sources)
        if isinstance(value, InvestorProfile):
            return {
                "investor": self.entity(value.investor),
                "funding_rounds_known": self.rounds(value.rounds[:6]),
                "participations": [self.evidence.record(item) for item in value.observations[:15]],
                "participations_omitted": max(0, len(value.observations) - 15),
                "people": [self.role(item) for item in value.people[:10]],
                "funds": [{"name": item.name, "event_date": str(item.event_date),
                           "source_ids": self.evidence.refs(item.sources, limit=1)} for item in value.fund_events[:6]],
                "activity_window_complete": value.activity_window_complete,
            }, len(value.observations)
        if isinstance(value, Entity):
            return self.entity(value), None
        if isinstance(value, list):
            if all(isinstance(item, Entity) for item in value):
                return [self.entity(item) for item in value[:8]], len(value)
            if all(isinstance(item, FundingRound) for item in value):
                return self.rounds(value[:4]), len(value)
            if all(isinstance(item, Role) for item in value):
                return [self.role(item) for item in value[:20]], len(value)
        raise TypeError("Unexpected tool result")


def _dump(value: Any) -> str:
    """Keep bounded observations valid JSON, including when a result is large."""
    def encode(item):
        return json.dumps(item, ensure_ascii=False, default=str)

    text = encode(value)
    if len(text) <= MAX_TOOL_RESULT_CHARACTERS:
        return text

    def compact(item, items, characters, depth=0, field=""):
        if depth > 8:
            return "[omitted]"
        if isinstance(item, str):
            if field in {"id", "url", "website"} or field.endswith(("_id", "_ids", "_url", "_urls")):
                return item
            return item[:characters] + (" [truncated]" if len(item) > characters else "")
        if isinstance(item, list):
            return [compact(child, items, characters, depth + 1, field) for child in item[:items]]
        if isinstance(item, dict):
            count = items if field in {"sources", "source_catalog", "registered_sources"} else max(items, 12)
            return {key: compact(child, items, characters, depth + 1, str(key))
                    for key, child in list(item.items())[:count]}
        return item

    for items, characters in ((8, 500), (4, 250), (2, 120), (1, 60)):
        bounded = compact(value, items, characters)
        packet = bounded if isinstance(bounded, dict) else {"result": bounded}
        text = encode({**packet, "truncated": True,
                       "notice": "Bounded result; omitted data is not negative evidence."})
        if len(text) <= MAX_TOOL_RESULT_CHARACTERS:
            return text
    return encode({"truncated": True, "notice": "Result too large; request a narrower Cala lookup."})


def _assistant_turn(message: dict) -> dict:
    # Echo only standard fields; provider extras (e.g. reasoning_content) can be rejected on replay.
    turn = {"role": "assistant", "content": message.get("content")}
    if message.get("tool_calls"):
        turn["tool_calls"] = [{"id": call.get("id"), "type": "function",
                               "function": {"name": call.get("function", {}).get("name"),
                                            "arguments": call.get("function", {}).get("arguments") or "{}"}}
                              for call in message["tool_calls"]]
    return turn


def _valid_tool_calls(calls: Any) -> bool:
    if not isinstance(calls, list):
        return False
    identifiers = set()
    for call in calls:
        if (not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]
                or call["id"] in identifiers or not isinstance(call.get("function"), dict)):
            return False
        function = call["function"]
        if not isinstance(function.get("name"), str) or not isinstance(function.get("arguments", "{}"), str):
            return False
        identifiers.add(call["id"])
    return True


class AgentToolSession:
    """Request-scoped tool execution and evidence shared through explicit handoffs."""

    def __init__(self, retrieval, evidence: _Evidence, names: dict[str, str],
                 on_step: StepCallback | None = None, *, agent: str = "research", knowledge_search: bool = False):
        self.retrieval, self.evidence, self.names = retrieval, evidence, names
        self.serializer = _Serializer(evidence, names)
        self.on_step, self.agent = on_step, agent
        self.knowledge_search = knowledge_search
        self.steps: list[ChatToolStep] = []
        self.observations: list[dict] = []

    def tools(self, knowledge_search: bool | None = None) -> list[dict]:
        knowledge_search = self.knowledge_search if knowledge_search is None else knowledge_search
        return [tool for tool in tool_definitions(self.retrieval.provider, knowledge_search)
                if tool["function"]["name"] != "final_answer"]

    def catalog(self) -> dict:
        return {identifier: {"label": _clip(source.label, 120), "url": str(source.url),
                             "published_at": str(source.published_at or "") or None}
                for identifier, source in self.evidence.sources.items()}

    async def emit(self, step: ChatToolStep):
        if self.on_step is not None:
            outcome = self.on_step(step)
            if asyncio.iscoroutine(outcome):
                await outcome

    async def execute(self, call: dict) -> str:
        if not _valid_tool_calls([call]):
            return _dump({"error": "Invalid research tool call; use the published tool schema."})
        name = call.get("function", {}).get("name")
        if name not in TOOL_SPECS or name not in {tool["function"]["name"] for tool in self.tools()}:
            return _dump({"error": "Unknown or unavailable research tool."})
        if len(self.steps) >= MAX_TOOL_STEPS:
            return _dump({"error": "Agent activity limit reached; finish with available evidence."})
        operation, model, result_type, _ = TOOL_SPECS[name]
        try:
            arguments = call.get("function", {}).get("arguments") or "{}"
            if not isinstance(arguments, str) or len(arguments) > 8000:
                raise ValueError("Invalid tool arguments")
            args = model.model_validate_json(arguments)
        except (ValidationError, ValueError, TypeError):
            return _dump({"error": "Invalid research tool arguments; use the published schema."})
        step = ChatToolStep(id=f"t{len(self.steps) + 1}", tool=name, label=_label(name, args, self.names),
                            arguments=args.model_dump(), status="started", agent=self.agent)
        self.steps.append(step)
        await self.emit(step)
        try:
            call_args = args.model_dump()
            if isinstance(args, EntityArgs):
                call_args = {"entity_id": args.entity_id, "company": args.is_company}
            elif isinstance(args, RoundArgs):
                call_args = {"event_id": args.round_id}
            value = await self.retrieval.call(operation, call_args, result_type, step=self.agent)
            payload, count = self.serializer.result(value)
            observation = {"tool": name, "arguments": args.model_dump(), "result": payload,
                           "sources": self.serializer.catalog(payload)}
            # Store exactly the bounded, parseable observation shown to the model.
            content = _dump(observation)
            self.observations.append(json.loads(content))
            done = step.model_copy(update={"status": "done", "count": count})
        except (CalaError, BudgetExceeded, ValidationError, TypeError) as error:
            message = (error.message if isinstance(error, CalaError) else
                       "Research retrieval budget used up; missing facts remain unknown."
                       if isinstance(error, BudgetExceeded) else "Cala returned unreadable data.")
            content = _dump({"error": message})
            done = step.model_copy(update={"status": "failed", "error": message})
        self.steps[self.steps.index(step)] = done
        await self.emit(done)
        return content
