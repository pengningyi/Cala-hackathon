"""Native Cala MCP adapter using discovered tools and exact entity/event joins.

Every actual tools/call passes through a replaceable caller so the orchestrator
can enforce a single request budget over introspection, projections and joins.
"""
from __future__ import annotations

import copy
import asyncio
import json
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator, SchemaError, ValidationError as SchemaValidationError
from pydantic import BaseModel, ConfigDict

from .cala import CalaError, CalaMCPProvider
from .entity_metadata import date_value as _date, parse_entity as _entity, property_value as _value, sources as _sources
from .models import (
    DetectedLead, Entity, EntityMetadataProperty, FundEvent, FundingRound, GraphSnapshot, InvestmentObservation,
    InvestorProfile, KnowledgeAnswer, Participation, Role,
)
from .runtime import BudgetExceeded


class ToolPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: Any
    retrieved_at: datetime


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[ToolPayload]]


def _months_before(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _edges(data: dict, direction: str, names: tuple[str, ...]) -> list[dict]:
    result = []
    for name in names:
        value = data.get("relationships", {}).get(direction, {}).get(name, [])
        if isinstance(value, list):
            result.extend(item for item in value if isinstance(item, dict))
    return result


# Preferred relationship names for people and funds. Only names that
# entity_introspection advertises for the entity are ever requested.
ROLE_KINDS = {
    "WORKS_AT": "works_at", "IS_MEMBER_OF": "member_of", "IS_BOARD_MEMBER_OF": "board",
    "FOUNDED": "founded", "STUDIED_AT": "studied_at",
}
PEOPLE_INCOMING = ("WORKS_AT", "IS_MEMBER_OF", "IS_BOARD_MEMBER_OF", "FOUNDED")
PERSON_OUTGOING = ("WORKS_AT", "IS_MEMBER_OF", "IS_BOARD_MEMBER_OF", "FOUNDED", "STUDIED_AT")
FUND_OUTGOING = ("MANAGES_FUND", "MANAGES", "SPONSORS_FUND", "LAUNCHED_FUND", "RAISED_FUND")


def _roles(data: dict, retrieved_at: datetime, direction: str, anchor: Entity) -> list[Role]:
    """Typed, sourced person-organisation roles from one projection, never inferred."""
    roles = []
    names = PEOPLE_INCOMING if direction == "incoming" else PERSON_OUTGOING
    for name in names:
        for edge in _edges(data, direction, (name,)):
            refs = _sources(edge, retrieved_at)
            if not edge.get("id") or not refs:
                continue
            if direction == "incoming" and edge.get("entity_type") not in ("Person", None):
                continue
            since_raw, until_raw = _value(edge, "valid_since"), _value(edge, "valid_until")
            since, until = _date(since_raw), _date(until_raw)
            if (since_raw is not None and since is None) or (until_raw is not None and until is None):
                continue  # unparseable dates stay unknown rather than open-ended
            current = _value(edge, "is_current")
            try:
                other = _entity(edge, retrieved_at)
            except CalaError:
                continue
            person, org = (other, anchor) if direction == "incoming" else (anchor, other)
            roles.append(Role(person=person, org=org, kind=ROLE_KINDS[name], valid_since=since, valid_until=until,
                              current=current is True, sources=refs))
    return roles


def _fund_events(data: dict, retrieved_at: datetime) -> list[FundEvent]:
    events = []
    for edge in _edges(data, "outgoing", FUND_OUTGOING):
        refs = _sources(edge, retrieved_at)
        when = next((d for d in (_date(_value(edge, key)) for key in (
            "event_date", "announcement_date", "launch_date", "valid_since", "inception_date")) if d), None)
        if when is None:
            vintage = _value(edge, "vintage_year")
            if isinstance(vintage, int) and 1900 < vintage < 2200:
                when = date(vintage, 1, 1)
        if when and refs and isinstance(edge.get("name"), str):
            events.append(FundEvent(name=edge["name"], event_date=when, sources=refs))
    return events


def _round(data: dict, retrieved_at: datetime) -> FundingRound | None:
    edges = _edges(data, "incoming", ("PARTICIPATES_IN_FUNDING_ROUND", "PARTICIPATES_IN_CORPORATE_EVENT"))
    issuers = [edge for edge in edges if str(_value(edge, "role", "")).upper() == "ISSUER"]
    if len({issuer.get("id") for issuer in issuers}) != 1:
        return None  # Never invent the funding recipient or merge separate events.
    participants = []
    for edge in edges:
        role = str(_value(edge, "role", "")).upper()
        if role not in ("INVESTOR", "LEAD_INVESTOR", "CO_LEAD_INVESTOR", "NON_LEAD_INVESTOR"):
            continue
        if not _sources(edge, retrieved_at):
            continue
        is_lead = _value(edge, "is_lead")
        if not isinstance(is_lead, bool):
            is_lead = True if role in ("LEAD_INVESTOR", "CO_LEAD_INVESTOR") else (False if role == "NON_LEAD_INVESTOR" else None)
        investor = _entity(edge, retrieved_at)
        # Preserve repeated sourced observations, including conflicting roles.
        # The deterministic snapshot merger resolves conflicts as unknown.
        participants.append(Participation(investor=investor, is_lead=is_lead))
    total = None
    if _value(data, "transaction_value_units") == "EUR":
        value = _value(data, "transaction_value")
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            try:
                amount = Decimal(str(value))
                total = amount if amount.is_finite() and amount >= 0 else None
            except InvalidOperation:
                pass
    precision = _value(data, "event_date__precision")
    extra = {}
    if "event_date_precision" in FundingRound.model_fields:
        extra["event_date_precision"] = str(precision).lower() if precision else None
    # When precision is explicitly coarser than day and the output contract cannot
    # retain it, leave the exact event date unknown instead of asserting Jan 1.
    event_date = _date(_value(data, "event_date"))
    if precision and str(precision).lower() not in ("day", "date") and not extra:
        event_date = None
    return FundingRound(
        id=str(data["id"]), company=_entity(issuers[0], retrieved_at, company=True),
        event_date=event_date, stage=_value(data, "round_label") or _value(data, "round_type"),
        total_amount_eur=total, participants=participants, sources=_sources(data, retrieved_at), **extra,
    )


class CalaNativeProvider:
    def __init__(self, endpoint: str | None = "https://api.cala.ai/mcp/", token: str | None = None,
                 transport: str = "streamable_http", timeout_seconds: float = 15,
                 api_key: str | None = None, max_graph_rounds: int = 6):
        self._transport = CalaMCPProvider(endpoint=endpoint, token=token, transport=transport,
                                          timeout_seconds=timeout_seconds, api_key=api_key)
        self._tool_caller: ToolCaller | None = None
        self.max_graph_rounds = max_graph_rounds
        self._known_rounds: dict[str, FundingRound] = {}

    @property
    def configured(self) -> bool:
        return bool(self._transport.endpoint and (self._transport._api_key or self._transport._token))

    def with_tool_caller(self, caller: ToolCaller) -> CalaNativeProvider:
        bound = copy.copy(self)
        bound._tool_caller = caller
        bound._known_rounds = {}
        return bound

    async def discover_tools(self):
        return await self._transport.discover_tools()

    async def call_data_tool(self, tool: str, arguments: dict[str, Any]) -> ToolPayload:
        metadata = {item["name"]: item for item in await self.discover_tools()}
        if tool not in metadata:
            raise CalaError("cala_tool_missing", "Required Cala tool is unavailable")
        try:
            Draft202012Validator.check_schema(metadata[tool]["inputSchema"])
            Draft202012Validator(metadata[tool]["inputSchema"]).validate(arguments)
        except (SchemaError, SchemaValidationError):
            raise CalaError("cala_arguments_invalid", "Native Cala arguments do not match its advertised schema") from None
        async with self._transport._session() as session:
            response = await session.call_tool(tool, arguments=arguments)
        if response.isError:
            raise CalaError("cala_tool_error", f"Cala {tool} returned an error")
        try:
            payload = response.structuredContent
            if payload is None:
                payload = json.loads("\n".join(item.text for item in response.content if item.type == "text"))
        except (ValueError, TypeError):
            raise CalaError("cala_response_invalid", f"Cala {tool} did not return structured JSON") from None
        if isinstance(payload, dict) and (payload.get("response_type") == "ERROR" or payload.get("error")):
            raise CalaError("cala_tool_error", f"Cala {tool} could not return the requested data")
        return ToolPayload(data=payload, retrieved_at=datetime.now(timezone.utc))

    async def _call(self, tool: str, arguments: dict[str, Any]) -> ToolPayload:
        caller = self._tool_caller or self.call_data_tool
        return await caller(tool, arguments)

    async def search_entities(self, query: str, kind: str) -> list[Entity]:
        args = {"name": query, "limit": 8}
        if kind == "company":
            args["entity_types"] = ["Company"]
        elif kind == "person":
            args["entity_types"] = ["Person"]
        response = await self._call("entity_search", args)
        raw = response.data.get("entities") if isinstance(response.data, dict) else response.data
        if not isinstance(raw, list):
            raise CalaError("cala_response_invalid", "Cala entity_search did not return entities")
        return [_entity(item, response.retrieved_at, company=kind == "company") for item in raw]

    async def entity_metadata(self, entity_id: str, company: bool = False) -> Entity:
        """Retrieve available properties without expanding unrelated graph paths."""
        response, _ = await self._entity_projection(entity_id, investment_edges=False)
        entity = _entity(response.data, response.retrieved_at, company=company, metadata_retrieved=True)
        # Industry is a sourced relationship in Cala, rather than a company
        # property. Keep that explicit classification available for onboarding.
        edges = response.data.get("relationships", {}).get("outgoing", {}).get("OPERATES_IN_INDUSTRY", [])
        if not isinstance(edges, list):
            return entity
        industries, ambiguous = {}, set()
        for edge in edges:
            if not isinstance(edge, dict) or edge.get("entity_type") != "Industry":
                continue
            if not isinstance(edge.get("properties", {}), dict):
                continue
            identifier, name = edge.get("id"), edge.get("name")
            if not isinstance(identifier, str) or not identifier.strip() or not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            since_raw, until_raw = _value(edge, "valid_since"), _value(edge, "valid_until")
            since, until = _date(since_raw), _date(until_raw)
            if ((since_raw is not None and since is None) or (until_raw is not None and until is None)
                    or (since and since > response.retrieved_at.date())
                    or (until and until < response.retrieved_at.date())):
                continue
            refs = _sources(edge, response.retrieved_at)
            if not refs:
                continue
            if identifier in industries and industries[identifier][0] != name:
                ambiguous.add(identifier)
                continue
            prior = industries.get(identifier, (name, []))[1]
            industries[identifier] = (name, [*prior, *refs])
        labels, references = [], {}
        for identifier, (name, refs) in industries.items():
            if identifier in ambiguous:
                continue
            if name not in labels:
                labels.append(name)
            for source in refs:
                references.setdefault((str(source.url), source.published_at, source.retrieved_at), source)
        if labels:
            entity.metadata.append(EntityMetadataProperty(
                name="operates_in_industry", value=labels, sources=list(references.values())))
        return entity

    async def detect_lead(self, company_id: str) -> DetectedLead | None:
        """Suggest a lead only from an identified company event and explicit lead role.

        This is a bounded scan, not a declaration of the company's latest funding
        history. The founder must still confirm the offered entity before a run.
        """
        response, _ = await self._entity_projection(company_id, investment_edges=True)
        today = datetime.now(timezone.utc).date()
        edges = _edges(response.data, "outgoing", (
            "PARTICIPATES_IN_FUNDING_ROUND", "PARTICIPATES_IN_CORPORATE_EVENT",
        ))
        candidates = {}
        for edge in edges:
            if edge.get("entity_type") not in ("FundingRound", "PrivateCompanyFundingRound") or not edge.get("id"):
                continue
            role = str(_value(edge, "role", "")).upper()
            if role and role != "ISSUER":
                continue
            event_date = _date(_value(edge, "event_date"))
            if event_date and event_date > today:
                continue
            candidates[edge["id"]] = edge
        ordered = sorted(candidates.values(), key=lambda item: (
            -(_date(_value(item, "event_date")) or date.min).toordinal(), item["id"],
        ))[:self.max_graph_rounds]

        async def read(edge):
            try:
                return await self._read_round(edge["id"])
            except (CalaError, BudgetExceeded):
                return None

        rounds = [funding for funding in await asyncio.gather(*(read(edge) for edge in ordered))
                  if funding is not None and funding.company.id == company_id and funding.company.sources and funding.sources
                  and (funding.event_date is None or funding.event_date <= today)]
        rounds.sort(key=lambda funding: (-(funding.event_date or date.min).toordinal(), funding.id))
        for funding in rounds:
            roles: dict[str, set[bool]] = {}
            investors = {}
            for participant in funding.participants:
                investors[participant.investor.id] = participant.investor
                if participant.is_lead is not None:
                    roles.setdefault(participant.investor.id, set()).add(participant.is_lead)
            explicit_leads = [investors[identifier] for identifier, observations in roles.items()
                              if observations == {True} and investors[identifier].sources]
            # One anchor per P0 run. Multiple explicit co-leads require a choice.
            if len(explicit_leads) != 1:
                continue
            lead = explicit_leads[0]
            evidence = {(str(source.url), source.published_at, source.retrieved_at): source
                        for source in [*funding.sources, *lead.sources]}
            return DetectedLead(lead=lead, company_id=company_id, event_id=funding.id,
                                stage=funding.stage, event_date=funding.event_date,
                                event_date_precision=funding.event_date_precision,
                                sources=list(evidence.values()))
        return None

    async def lead_graph(self, lead_id: str, lookback_months: int) -> GraphSnapshot:
        response, _ = await self._investor(lead_id)
        today = datetime.now(timezone.utc).date()
        cutoff = _months_before(today, lookback_months)
        edges = _edges(response.data, "outgoing", ("PARTICIPATES_IN_FUNDING_ROUND", "PARTICIPATES_IN_CORPORATE_EVENT"))
        selected = {}
        for edge in edges:
            if edge.get("entity_type") not in ("FundingRound", "PrivateCompanyFundingRound"):
                continue
            edge_date = _date(_value(edge, "event_date"))
            if edge_date and not cutoff <= edge_date <= today:
                continue
            selected[edge["id"]] = edge
        ordered = sorted(selected.values(), key=lambda item: (
            -(_date(_value(item, "event_date")) or date.min).toordinal(), item["id"],
        ))[:self.max_graph_rounds]

        async def read(edge):
            try:
                return await self._read_round(edge["id"])
            except (CalaError, BudgetExceeded):
                return None

        rounds = [funding for funding in await asyncio.gather(*(read(edge) for edge in ordered)) if funding is not None]
        rounds = [funding for funding in rounds if funding.event_date and cutoff <= funding.event_date <= today
                  and any(item.investor.id == lead_id for item in funding.participants)]
        # One source-backed company description gives the semantic step bounded
        # product context while reserving ten calls for five investor profiles.
        if rounds:
            issuer_id = rounds[0].company.id
            try:
                company_response = await self._call("entity_retrieval", {"entity_id": issuer_id})
                company = _entity(company_response.data, company_response.retrieved_at, company=True)
                if company.id == issuer_id:
                    rounds = [funding.model_copy(update={"company": company}) if funding.company.id == issuer_id else funding for funding in rounds]
            except (CalaError, BudgetExceeded):
                pass  # Optional profile context never erases already verified joins.
        self._known_rounds.update({funding.id: funding for funding in rounds})
        # Relation projections are deliberately bounded, and Cala does not return
        # an exhaustive-coverage declaration. A small page is not proof of coverage.
        lead = _entity(response.data, response.retrieved_at, metadata_retrieved=True)
        return GraphSnapshot(rounds=rounds, complete=False, lead=lead,
                             people=_roles(response.data, response.retrieved_at, "incoming", lead))

    async def investor_profile(self, investor_id: str, lookback_months: int) -> InvestorProfile:
        response, schema = await self._investor(investor_id)
        investor = _entity(response.data, response.retrieved_at, metadata_retrieved=True)
        if investor.id != investor_id:
            raise CalaError("cala_entity_mismatch", "Cala returned a different investor")
        today = datetime.now(timezone.utc).date()
        cutoff = _months_before(today, lookback_months)
        rounds = [funding for funding in self._known_rounds.values()
                  if funding.event_date and cutoff <= funding.event_date <= today
                  and any(item.investor.id == investor_id for item in funding.participants)]
        observations = []
        for edge in _edges(response.data, "outgoing", ("PARTICIPATES_IN_FUNDING_ROUND", "PARTICIPATES_IN_CORPORATE_EVENT")):
            role = str(_value(edge, "role", "")).upper()
            sources = _sources(edge, response.retrieved_at)
            if edge.get("entity_type") not in ("FundingRound", "PrivateCompanyFundingRound") or not edge.get("id") or not sources:
                continue
            if role not in ("INVESTOR", "LEAD_INVESTOR", "CO_LEAD_INVESTOR", "NON_LEAD_INVESTOR"):
                continue
            event_date = _date(_value(edge, "event_date"))
            if event_date and not cutoff <= event_date <= today:
                continue
            is_lead = _value(edge, "is_lead")
            if not isinstance(is_lead, bool):
                is_lead = True if role in ("LEAD_INVESTOR", "CO_LEAD_INVESTOR") else (False if role == "NON_LEAD_INVESTOR" else None)
            precision = _value(edge, "event_date__precision")
            observations.append(InvestmentObservation(
                id=edge["id"], event_date=event_date,
                event_date_precision=str(precision).lower() if precision else None,
                stage=_value(edge, "round_label") or _value(edge, "round_type") or _value(edge, "stage")
                      or _stage_label(edge.get("name")),
                is_lead=is_lead, sources=sources,
            ))
        return InvestorProfile(investor=investor, rounds=rounds, observations=observations, activity_window_complete=False,
                               sources=_sources(response.data, response.retrieved_at),
                               people=_roles(response.data, response.retrieved_at, "incoming", investor),
                               fund_events=_fund_events(response.data, response.retrieved_at),
                               fund_data_available=any(name in schema.get("relationships", {}).get("outgoing", [])
                                                       for name in FUND_OUTGOING))

    async def company_rounds(self, company_id: str, limit: int) -> list[FundingRound]:
        """A company's own identified funding events (it is the issuer), newest first.

        Used to find your company's existing investors and the backers of the
        founders' previous companies. Bounded; not a declaration of full history.
        """
        if limit <= 0:
            return []
        response, _ = await self._entity_projection(company_id, investment_edges=True, location=False)
        today = datetime.now(timezone.utc).date()
        candidates = {}
        for edge in _edges(response.data, "outgoing", ("PARTICIPATES_IN_FUNDING_ROUND", "PARTICIPATES_IN_CORPORATE_EVENT")):
            if edge.get("entity_type") not in ("FundingRound", "PrivateCompanyFundingRound") or not edge.get("id"):
                continue
            role = str(_value(edge, "role", "")).upper()
            if role and role != "ISSUER":
                continue
            event_date = _date(_value(edge, "event_date"))
            if event_date and event_date > today:
                continue
            candidates[edge["id"]] = edge
        ordered = sorted(candidates.values(), key=lambda item: (
            -(_date(_value(item, "event_date")) or date.min).toordinal(), item["id"]))[:limit]

        async def read(edge):
            try:
                return await self._read_round(edge["id"])
            except (CalaError, BudgetExceeded):
                return None

        rounds = [funding for funding in await asyncio.gather(*(read(edge) for edge in ordered))
                  if funding is not None and funding.company.id == company_id and funding.sources
                  and (funding.event_date is None or funding.event_date <= today)]
        return sorted(rounds, key=lambda funding: (-(funding.event_date or date.min).toordinal(), funding.id))

    async def entity_people(self, entity_id: str) -> list[Role]:
        """People with sourced roles at an organisation (employees, members, board, founders)."""
        response, _ = await self._entity_projection(entity_id, investment_edges=False, people=True, location=False)
        org = _entity(response.data, response.retrieved_at)
        return _roles(response.data, response.retrieved_at, "incoming", org)

    async def person_roles(self, person_id: str) -> list[Role]:
        """A person's sourced roles at organisations, with validity dates."""
        schema = (await self._call("entity_introspection", {"entity_id": person_id})).data
        if not isinstance(schema, dict):
            raise CalaError("cala_schema_invalid", "Cala entity schema was not an object")
        available = schema.get("relationships", {}).get("outgoing", [])
        relationships = {name: {"limit": 20} for name in PERSON_OUTGOING if name in available}
        if not relationships:
            return []
        response = await self._call("entity_retrieval", {"entity_id": person_id, "relationships": {"outgoing": relationships}})
        if not isinstance(response.data, dict) or response.data.get("id") != person_id:
            raise CalaError("cala_entity_mismatch", "Cala returned a different person")
        person = _entity(response.data, response.retrieved_at)
        return _roles(response.data, response.retrieved_at, "outgoing", person)

    async def funding_round(self, event_id: str) -> FundingRound | None:
        """One identified funding event: issuer company, date, stage, amount and participants."""
        return await self._read_round(event_id)

    async def knowledge_search(self, query: str) -> KnowledgeAnswer:
        """Sourced natural-language search. Slow (tens of seconds) and not a typed join."""
        tools = {item["name"]: item for item in await self.discover_tools()}
        schema = tools.get("knowledge_search", {}).get("inputSchema") or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        # Use the advertised argument name rather than assuming one.
        names = [name for name in ("input", "query", "question", "q") if name in properties]
        if not names:
            names = [name for name in schema.get("required", []) if properties.get(name, {}).get("type") == "string"]
        response = await self._call("knowledge_search", {(names[0] if names else "input"): query})
        data = response.data
        text = ""
        if isinstance(data, dict):
            for key in ("content", "answer", "result", "text", "summary"):
                if isinstance(data.get(key), str) and data[key].strip():
                    text = data[key].strip()
                    break
        elif isinstance(data, str):
            text = data.strip()
        return KnowledgeAnswer(text=text[:4000], sources=_sources(data, response.retrieved_at)[:12])

    async def _investor(self, investor_id: str) -> tuple[ToolPayload, dict]:
        # People and fund edges ride on the same projection call: no extra budget.
        return await self._entity_projection(investor_id, investment_edges=True, people=True, funds=True)

    async def _entity_projection(self, entity_id: str, *, investment_edges: bool, people: bool = False,
                                 funds: bool = False, location: bool = True) -> tuple[ToolPayload, dict]:
        schema = (await self._call("entity_introspection", {"entity_id": entity_id})).data
        if not isinstance(schema, dict):
            raise CalaError("cala_schema_invalid", "Cala entity schema was not an object")
        # Introspection, rather than a hand-picked field list, controls the read.
        properties = list(dict.fromkeys(name for name in schema.get("properties", []) if isinstance(name, str)))
        available = schema.get("relationships", {}).get("outgoing", [])
        available_in = schema.get("relationships", {}).get("incoming", [])
        names = ["HAS_HEADQUARTERS_IN", "IS_REGISTERED_IN", "OPERATES_IN_INDUSTRY"] if location else []
        if investment_edges:
            names.extend(["PARTICIPATES_IN_FUNDING_ROUND", "PARTICIPATES_IN_CORPORATE_EVENT"])
        if funds:
            names.extend(FUND_OUTGOING)
        relationships = {name: {"limit": 20} for name in names if name in available}
        incoming = {name: {"limit": 20} for name in PEOPLE_INCOMING if name in available_in} if people else {}
        args = {"entity_id": entity_id, "properties": properties}
        if relationships or incoming:
            args["relationships"] = {}
            if relationships:
                args["relationships"]["outgoing"] = relationships
            if incoming:
                args["relationships"]["incoming"] = incoming
        response = await self._call("entity_retrieval", args)
        if not isinstance(response.data, dict) or response.data.get("id") != entity_id:
            raise CalaError("cala_entity_mismatch", "Cala returned a different entity")
        return response, schema

    async def _read_round(self, event_id: str) -> FundingRound | None:
        if event_id in self._known_rounds:
            return self._known_rounds[event_id]
        schema = (await self._call("entity_introspection", {"entity_id": event_id})).data
        properties = [name for name in (
            "id", "name", "description", "event_date", "event_date__precision", "round_label", "round_type",
            "transaction_value", "transaction_value_units",
        ) if name in schema.get("properties", [])]
        available = schema.get("relationships", {}).get("incoming", [])
        incoming = {name: {"limit": 50} for name in (
            "PARTICIPATES_IN_FUNDING_ROUND", "PARTICIPATES_IN_CORPORATE_EVENT",
        ) if name in available}
        if not incoming:
            return None
        response = await self._call("entity_retrieval", {
            "entity_id": event_id, "properties": properties, "relationships": {"incoming": incoming},
        })
        if not isinstance(response.data, dict) or response.data.get("id") != event_id:
            raise CalaError("cala_entity_mismatch", "Cala returned a different funding event")
        funding = _round(response.data, response.retrieved_at)
        if funding:
            self._known_rounds[event_id] = funding
        return funding


def _stage_label(value: Any) -> str | None:
    """A typed funding event's exact stage name is an observed stage label."""
    if not isinstance(value, str):
        return None
    label = " ".join(value.lower().replace("_", " ").replace("-", " ").split())
    if label in {"pre seed", "seed", *(f"series {letter}" for letter in "abcdefgh")}:
        return value
    return None
