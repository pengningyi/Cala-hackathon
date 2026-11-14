from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

from . import ranker, rules as rule_book, trust
from .config import Settings
from .models import (
    CompanyConfirmation, CompanyMatches, CompanyMatchRequest, ConfirmCompanyMatches,
    DetectedLead, Entity, FounderInput, FundingRound, GraphSnapshot, InvestorProfile, Resolution,
    ResolveRequest, Role, RunResult, RunStatus, StartRunRequest,
)
from .onboarding import startup_prefill
from .runtime import BudgetExceeded, ResponseCache, Retrieval, Trace, utcnow
from .scoring import (
    build_allocation, combined_draft, deduplicate_rounds, exclusion_for, finalize_card, months_before, normalize_stage,
    normalized_name, score_candidate,
)

PEOPLE_PAGE_LIMIT = 20  # a full page means the organisation's size is unknown, not 20


class WorkflowError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


@dataclass
class RunRecord:
    id: str
    profile: FounderInput
    lead: Entity | None
    confirmed_at: datetime
    started: float
    trace: Trace = field(default_factory=Trace)
    result: RunResult | None = None
    task: asyncio.Task | None = None

    def status(self):
        return RunStatus(id=self.id, state=self.result.state if self.result else "running",
                         confirmed_at=self.confirmed_at, event_count=len(self.trace.events),
                         result_url=f"/api/runs/{self.id}/result", events_url=f"/api/runs/{self.id}/events")


@dataclass
class OnboardingRecord:
    matches: CompanyMatches
    confirmation: CompanyConfirmation | None = None


class Workflow:
    def __init__(self, settings: Settings, provider, semantic=None, identity=None):
        self.settings, self.provider, self.semantic = settings, provider, semantic
        # Optional REST resolver (app/cala_rest.py) for the onboarding company/lead lookup.
        self.identity = identity
        # Include endpoint, credentials and mapping in cache identity, never in cache filenames/content.
        import hashlib
        mapping = ""
        if settings.cala_tool_mapping:
            try:
                mapping = settings.cala_tool_mapping.read_text(encoding="utf-8")
            except OSError:
                mapping = str(settings.cala_tool_mapping)
        namespace = hashlib.sha256(repr((settings.cala_mcp_url, settings.cala_api_key, settings.cala_mcp_token, mapping)).encode()).hexdigest()
        self.cache = ResponseCache(settings.cache_dir, settings.cache_ttl_seconds, namespace)
        self.semaphore = asyncio.Semaphore(settings.cala_concurrency)
        self.resolutions: dict[str, Resolution] = {}
        self.onboarding_matches: dict[str, OnboardingRecord] = {}
        self.onboarding_confirmations: dict[str, OnboardingRecord] = {}
        self._pending_identity_lookups = 0
        self.runs: dict[str, RunRecord] = {}

    def cleanup(self):
        now = utcnow()
        self.resolutions = {k: v for k, v in self.resolutions.items() if v.expires_at > now}
        self.onboarding_matches = {k: v for k, v in self.onboarding_matches.items() if v.matches.expires_at > now}
        self.onboarding_confirmations = {k: v for k, v in self.onboarding_confirmations.items() if v.matches.expires_at > now}
        self.runs = {k: v for k, v in self.runs.items()
                     if v.result is None or (now - v.result.finished_at).total_seconds() < self.settings.retention_seconds}

    async def resolve(self, request: ResolveRequest):
        self.cleanup()
        if len(self.resolutions) + len(self.onboarding_matches) + self._pending_identity_lookups >= self.settings.max_stored_runs:
            raise WorkflowError("capacity", "Resolution capacity reached; retry after older resolutions expire", 429)
        started, now = time.monotonic(), utcnow()
        leads, startups, notices = [], [], []
        profile = request.profile.model_copy(deep=True)
        detected_lead = None
        if request.profile.round.remaining_eur > 0:
            retrieval = Retrieval(self.provider, self.cache, self.settings, Trace(), semaphore=self.semaphore)
            # Resolution latency is separate from confirmed-run processing.
            retrieval.stop_at = started + self.settings.cala_request_timeout_seconds
            if profile.lead_name:
                leads = await retrieval.call("search_entities", {"query": profile.lead_name, "kind": "investor"}, list[Entity], "resolver")
            if request.lookup_startup or not profile.lead_name:
                try:
                    startups = await retrieval.call("search_entities", {"query": profile.startup.company_name, "kind": "company"}, list[Entity], "resolver")
                except Exception:
                    notices.append("Optional startup lookup unavailable; confirm the manual startup profile.")
            if not profile.lead_name:
                startup = _startup_for_detection(profile, startups)
                if startup is None:
                    notices.append("A unique company match is needed to detect a lead. Enter a lead investor or refine the company name and website.")
                elif not callable(getattr(self.provider, "detect_lead", None)):
                    notices.append("Lead auto-detection is unavailable for this Cala adapter. Enter the lead investor's name.")
                else:
                    try:
                        async with asyncio.timeout(max(0, retrieval.remaining() - 0.05)):
                            detected_lead = await retrieval.call("detect_lead", {"company_id": startup.id},
                                                                DetectedLead | None, "resolver_lead")
                        if detected_lead and detected_lead.company_id == startup.id:
                            leads = [detected_lead.lead]
                            profile.lead_name = detected_lead.lead.name
                            notices.append("Lead suggested from an identified Cala funding event. Confirm the lead and company before starting the search.")
                        else:
                            detected_lead = None
                            notices.append("No unique, explicitly identified lead was found in the retrieved company funding events. Enter your lead investor.")
                    except Exception:
                        detected_lead = None
                        notices.append("Lead detection could not finish within the lookup limits. Your company matches remain available; enter your lead investor.")
            if callable(getattr(self.provider, "entity_metadata", None)) and (leads or startups):
                leads, startups = await self._resolve_metadata(retrieval, leads, startups, notices)
            if not leads:
                notices.append("No Cala lead match found. Refine the lead name before starting a run.")
        else:
            notices.append("No open allocation. No Cala search was performed.")
        resolution = Resolution(id=uuid.uuid4().hex, profile=profile, detected_lead=detected_lead,
                                lead_matches=leads, startup_matches=startups,
                                created_at=now, expires_at=now + timedelta(seconds=self.settings.retention_seconds),
                                resolver_ms=round((time.monotonic() - started) * 1000, 2),
                                no_open_allocation=request.profile.round.remaining_eur == 0, notices=notices)
        self.resolutions[resolution.id] = resolution
        return resolution

    async def match_companies(self, request: CompanyMatchRequest):
        """Resolve only two names; current round facts do not exist yet."""
        self.cleanup()
        if len(self.resolutions) + len(self.onboarding_matches) + self._pending_identity_lookups >= self.settings.max_stored_runs:
            raise WorkflowError("capacity", "Company lookup capacity reached; retry after older matches expire", 429)
        self._pending_identity_lookups += 1
        try:
            started, now = time.monotonic(), utcnow()
            notices = []
            # Keep the REST identity query and its top-three matching behavior.
            # Full property metadata is a separate, bounded MCP read below.
            source = self.identity or self.provider
            operation = "knowledge_query_entities" if self.identity else "search_entities"
            retrieval = Retrieval(source, self.cache, self.settings, Trace(), semaphore=self.semaphore)
            retrieval.stop_at = started + (self.settings.cala_query_timeout_seconds if self.identity
                                           else self.settings.cala_request_timeout_seconds)
            outcomes = await asyncio.gather(
                retrieval.call(operation, {"query": request.company_name, "kind": "company"}, list[Entity], "resolver_company"),
                retrieval.call(operation, {"query": request.lead_name, "kind": "investor"}, list[Entity], "resolver_lead"),
                return_exceptions=True,
            )
            for outcome in outcomes:
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
            failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
            if len(failures) == len(outcomes):
                raise failures[0]
            startups, leads = [], []
            for index, label in ((0, "company"), (1, "lead investor")):
                outcome = outcomes[index]
                if isinstance(outcome, Exception):
                    notices.append(f"The {label} lookup could not finish. Retry or refine its name.")
                    continue
                # Bound optional enrichment even for a custom provider returning an oversized page.
                matches, seen = [], set()
                for entity in outcome:
                    if entity.id not in seen:
                        seen.add(entity.id)
                        matches.append(entity)
                if len(matches) > 10:
                    notices.append(f"Only the first ten {label} matches are shown; refine the name if needed.")
                if index == 0:
                    startups = matches[:10]
                else:
                    leads = matches[:10]
            if callable(getattr(self.provider, "entity_metadata", None)) and (leads or startups):
                metadata_retrieval = retrieval
                if self.identity:
                    # The identity resolver cannot call MCP tools. Give its
                    # successful matches a fresh metadata window after REST,
                    # with configured request limits and global concurrency.
                    metadata_retrieval = Retrieval(self.provider, self.cache, self.settings, Trace(),
                                                   semaphore=self.semaphore)
                    metadata_retrieval.stop_at = time.monotonic() + self.settings.cala_request_timeout_seconds
                leads, startups = await self._resolve_metadata(metadata_retrieval, leads, startups, notices)
            elif self.identity and (leads or startups):
                leads = [entity.model_copy(update={"metadata_status": "unavailable"}) for entity in leads]
                startups = [entity.model_copy(update={"metadata_status": "unavailable"}) for entity in startups]
                notices.append("Full Cala match metadata was unavailable for this adapter. "
                               "Basic matches remain available; missing fields stay unknown.")
            if not startups:
                notices.append("No company match is available. Refine the founder company name before continuing.")
            if not leads:
                notices.append("No lead investor match is available. Refine the lead company name before continuing.")
            matches = CompanyMatches(id=uuid.uuid4().hex, startup_matches=startups, lead_matches=leads,
                                     resolver_ms=round((time.monotonic() - started) * 1000, 2),
                                     created_at=now, expires_at=now + timedelta(seconds=self.settings.retention_seconds),
                                     notices=notices)
            self.onboarding_matches[matches.id] = OnboardingRecord(matches=matches.model_copy(deep=True))
            return matches
        finally:
            self._pending_identity_lookups -= 1

    def confirm_companies(self, match_id: str, request: ConfirmCompanyMatches):
        """Confirm offered IDs and return editable sourced fields without any retrieval."""
        self.cleanup()
        record = self.onboarding_matches.get(match_id)
        if record is None:
            raise WorkflowError("matches_not_found", "Company matches are missing or expired; search again", 404)
        startup = next((item for item in record.matches.startup_matches if item.id == request.startup_id), None)
        lead = next((item for item in record.matches.lead_matches if item.id == request.lead_id), None)
        if startup is None:
            raise WorkflowError("invalid_startup_selection", "Select a company offered by this lookup")
        if lead is None:
            raise WorkflowError("invalid_lead_selection", "Select a lead investor offered by this lookup")
        if (record.confirmation and record.confirmation.startup.id == startup.id
                and record.confirmation.lead.id == lead.id):
            return record.confirmation.model_copy(deep=True)
        if record.confirmation:
            self.onboarding_confirmations.pop(record.confirmation.resolution_id, None)
        prefill, sources = startup_prefill(startup)
        confirmation = CompanyConfirmation(
            resolution_id=uuid.uuid4().hex, startup=startup.model_copy(deep=True), lead=lead.model_copy(deep=True),
            prefill=prefill, prefill_sources=sources, expires_at=record.matches.expires_at,
            notices=["Review the confirmed company details. Missing fields and current-round information need your input."],
        )
        record.confirmation = confirmation
        self.onboarding_confirmations[confirmation.resolution_id] = record
        return confirmation.model_copy(deep=True)

    async def _resolve_metadata(self, retrieval: Retrieval, leads: list[Entity],
                                startups: list[Entity], notices: list[str]):
        """Enrich offered identities without losing a successful basic lookup.

        Metadata shares the resolver's existing raw-call and time limits. Each
        completed response is retained immediately, so cancellation of a slow
        sibling never erases already-retrieved details or the original matches.
        """
        leads = [item.model_copy(deep=True) for item in leads]
        startups = [item.model_copy(deep=True) for item in startups]
        jobs = []
        for matches, company in ((leads, False), (startups, True)):
            for index, entity in enumerate(matches):
                matches[index] = entity.model_copy(update={"metadata_status": "unavailable"})
                jobs.append((matches, index, entity, company))

        cursor = 0

        async def worker():
            nonlocal cursor
            while cursor < len(jobs) and retrieval.remaining() > 0:
                # No await between selecting a job and advancing the cursor.
                matches, index, original, company = jobs[cursor]
                cursor += 1
                try:
                    enriched = await retrieval.call("entity_metadata", {
                        "entity_id": original.id, "company": company,
                    }, Entity, "resolver_metadata")
                    if enriched.id != original.id:
                        continue  # A different identity can never become an offered match.
                    matches[index] = enriched.model_copy(update={
                        "description": enriched.description or original.description,
                        "website": enriched.website or original.website,
                        "metadata_status": "retrieved",
                    })
                except Exception:
                    # Metadata is optional. Preserve the original identity and
                    # allow the founder to confirm it even when details fail.
                    continue

        tasks = [asyncio.create_task(worker()) for _ in range(min(
            self.settings.cala_concurrency, 4, len(jobs)))]
        try:
            # Leave a small scheduling margin to return the preserved resolution
            # instead of allowing metadata to consume the whole request window.
            async with asyncio.timeout(max(0, retrieval.remaining() - 0.05)):
                await asyncio.gather(*tasks)
        except TimeoutError:
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if any(item.metadata_status != "retrieved" for item in [*leads, *startups]):
            notices.append("Some Cala match metadata was unavailable within the lookup limits. "
                           "Basic matches remain available; missing fields stay unknown.")
        return leads, startups

    def start(self, request: StartRunRequest):
        self.cleanup()
        identity = self.onboarding_confirmations.get(request.resolution_id)
        resolution = self.resolutions.get(request.resolution_id)
        if identity is not None:
            confirmed = identity.confirmation
            if (request.profile.startup.cala_entity_id != confirmed.startup.id
                    or request.profile.startup.company_name != confirmed.startup.name):
                raise WorkflowError("confirmed_startup_changed", "The company changed. Confirm the correct company match again")
            if request.lead_id != confirmed.lead.id or request.profile.lead_name != confirmed.lead.name:
                raise WorkflowError("confirmed_lead_changed", "The lead investor changed. Confirm the correct lead match again")
            lead = confirmed.lead
        elif resolution is None:
            raise WorkflowError("resolution_not_found", "Resolution missing or expired; resolve again", 404)
        elif request.profile.lead_name != resolution.profile.lead_name:
            raise WorkflowError("lead_changed", "Resolve the updated lead name before confirmation")
        if identity is None and request.profile.startup.cala_entity_id and request.profile.startup.cala_entity_id not in {e.id for e in resolution.startup_matches}:
            raise WorkflowError("invalid_startup_selection", "Select a startup ID offered by this resolution or use a manual profile")
        if (identity is None and resolution.detected_lead and request.profile.startup.cala_entity_id
                and request.profile.startup.cala_entity_id != resolution.detected_lead.company_id):
            raise WorkflowError("detected_lead_company_changed", "This suggested lead belongs to a different company match. Resolve the corrected company or enter its lead manually.")
        if identity is None:
            lead = next((x for x in resolution.lead_matches if x.id == request.lead_id), None)
        if request.profile.round.remaining_eur > 0 and lead is None:
            raise WorkflowError("invalid_lead_selection", "Select a lead ID offered by this resolution")
        if sum(x.result is None for x in self.runs.values()) >= self.settings.max_active_runs:
            raise WorkflowError("capacity", "Active run capacity reached; retry when a run completes", 429)
        if len(self.runs) >= self.settings.max_stored_runs:
            raise WorkflowError("capacity", "Run storage capacity reached; retry after older results expire", 429)
        record = RunRecord(id=uuid.uuid4().hex, profile=request.profile.model_copy(deep=True),
                           lead=lead.model_copy(deep=True) if lead else None,
                           confirmed_at=utcnow(), started=time.monotonic())
        self.runs[record.id] = record
        record.trace.emit("confirmation", "Founder confirmed the lead and startup profile; processing timer started")
        record.task = asyncio.create_task(self._execute(record))
        return record.status()

    async def _optional(self, retrieval, operation, args, result_type, *, enabled=True, step="trust"):
        """A bounded lookup whose failure leaves its signal unknown and never blocks the run."""
        from .cala import CalaError
        if not enabled or not hasattr(self.provider, operation):
            return None
        try:
            return await retrieval.call(operation, args, result_type, step)
        except (CalaError, BudgetExceeded):
            return None

    @staticmethod
    def _add_origin(rounds, origin, lead, entities, origin_rounds, origins):
        for funding in rounds:
            for participation in funding.participants:
                investor = participation.investor
                if investor.id == lead.id:
                    continue
                entities.setdefault(investor.id, investor)
                if funding.id not in {r.id for r in origin_rounds.get(investor.id, [])}:
                    origin_rounds.setdefault(investor.id, []).append(funding)
                origins.setdefault(investor.id, set()).add(origin)

    @staticmethod
    def _preliminary(entities, origins, candidate_rounds, origin_rounds, founder, lead, excluded, limit):
        """Existing investors first, then lead-network depth/recency/stage, then founder-network backers."""
        preliminary = []
        for investor in entities.values():
            exclusion = exclusion_for(investor, founder, lead)
            if exclusion:
                if investor.id not in {e.investor.id for e in excluded}:
                    excluded.append(exclusion)
            else:
                preliminary.append(investor)

        def key(e):
            shared = candidate_rounds.get(e.id, [])
            evidence = shared + origin_rounds.get(e.id, [])
            source_rank = (0 if "existing_investor" in origins.get(e.id, ()) else
                           1 if shared and "previous_company_backer" in origins.get(e.id, ()) else
                           2 if shared else 3)
            return (source_rank, -len({r.company.id for r in evidence}),
                    -max(((r.event_date or date.min).toordinal() for r in evidence), default=0),
                    -sum(normalize_stage(r.stage) == founder.round.stage for r in shared),
                    e.name.casefold(), e.id)
        preliminary.sort(key=key)
        return preliminary[:limit]

    async def _profile(self, retrieval, entities, limit, founder, lead, profiles, excluded, incomplete, notices, trace):
        """Profile up to `limit` more investors, backfilling exclusions within the budget."""
        from .cala import CalaError
        target = len(profiles) + max(0, limit)
        cursor = fetched = 0
        while len(profiles) < target and cursor < len(entities) and retrieval.remaining() > 0:
            batch = entities[cursor:cursor + target - len(profiles)]
            cursor += len(batch)

            async def profile_one(entity):
                try:
                    profile = await retrieval.call("investor_profile", {"investor_id": entity.id, "lookback_months": 24}, InvestorProfile, "profiles")
                    if profile.investor.id != entity.id:
                        raise CalaError("invalid_profile", "Cala returned a different investor ID")
                    return entity, profile, None
                except (CalaError, BudgetExceeded) as exc:
                    return entity, None, exc

            failed = False
            for entity, profile, failure in await asyncio.gather(*(profile_one(e) for e in batch)):
                if failure:
                    failed = True
                    incomplete.append(entity)
                    notices.append(f"Profile incomplete for {entity.name}; not rejected.")
                    trace.emit("profiles", "Candidate profile unavailable; retained as incomplete", type="notice", count=1)
                    continue
                fetched += 1
                exclusion = exclusion_for(profile.investor, founder, lead, profile)
                if exclusion:
                    excluded.append(exclusion)
                else:
                    profiles.append(profile)
            # A failed retrieval remains pending; do not spend the full budget chasing replacements.
            if failed:
                break
        if cursor < len(entities) and len(profiles) < target:
            incomplete.extend(e for e in entities[cursor:] if e.id not in {i.id for i in incomplete})
        return fetched

    async def _trust_people(self, retrieval, founder_ids, lead_partner, skip_orgs, as_of):
        """Wave 1 person roles, then a bounded set of their organisations' people.

        Returns (roles, observed headcounts, founder entities). Budget exhaustion
        returns what was found; missing people data leaves trust unknown.
        """
        limit = self.settings.trust_person_limit
        people = founder_ids[:max(0, limit - (1 if lead_partner else 0))]
        if lead_partner:
            people.append(lead_partner.id)
        results = await asyncio.gather(*(self._optional(retrieval, "person_roles", {"person_id": pid}, list[Role])
                                         for pid in people))
        roles = [role for found in results if found for role in found]
        founders = {}
        for role in roles:
            if role.person.id in founder_ids:
                founders.setdefault(role.person.id, role.person)
        # Organisations where the founders or lead partner spent time, newest first.
        orgs = {}
        for role in sorted(roles, key=lambda r: (-(r.valid_until or as_of).toordinal(), r.org.id)):
            if role.org.id not in skip_orgs and role.valid_since:
                orgs.setdefault(role.org.id, role.org)
        selected = list(orgs)[:self.settings.trust_org_limit]
        previous = []
        for role in sorted(roles, key=lambda r: (-(r.valid_until or as_of).toordinal(), r.org.id)):
            if (role.person.id in founder_ids and role.kind == "founded" and role.org.id not in skip_orgs
                    and role.org.id not in previous):
                previous.append(role.org.id)
        previous = previous[:self.settings.previous_company_limit]
        org_people, previous_found = await asyncio.gather(
            asyncio.gather(*(self._optional(retrieval, "entity_people", {"entity_id": oid}, list[Role]) for oid in selected)),
            asyncio.gather(*(self._optional(retrieval, "company_rounds", {"company_id": cid, "limit": self.settings.previous_company_round_limit},
                                            list[FundingRound], enabled=self.settings.previous_company_round_limit > 0)
                             for cid in previous)))
        previous_rounds = [funding for cid, found in zip(previous, previous_found) for funding in (found or [])
                           if funding.company.id == cid and funding.sources]
        headcounts = {}
        for oid, found in zip(selected, org_people):
            if found:
                roles.extend(found)
                count = len({r.person.id for r in found})
                if count < PEOPLE_PAGE_LIMIT:
                    headcounts[oid] = count
        # Checked = founders' roles were read (so "no previous company" is a real answer).
        previous_checked = any(found is not None for found in results) and all(found is not None for found in previous_found)
        return roles, headcounts, [founders[pid] for pid in founder_ids if pid in founders], previous_rounds, previous_checked

    def get(self, run_id):
        self.cleanup()
        if run_id not in self.runs:
            raise WorkflowError("run_not_found", "Run missing or expired", 404)
        return self.runs[run_id]

    async def close(self):
        tasks = [r.task for r in self.runs.values() if r.task and not r.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, run: RunRecord):
        from .cala import CalaError

        config, trace, founder, lead = self.settings, run.trace, run.profile, run.lead
        retrieval = Retrieval(self.provider, self.cache, config, trace, run.started, self.semaphore)
        cards, excluded, incomplete, notices, profiles = [], [], [], [], []
        retrieved_people: list[Role] = []
        people_task = None
        discovered, profiled_count, graph_complete, graph_received = 0, 0, False, False
        candidate_rounds: dict[str, list[FundingRound]] = {}
        candidate_entities: dict[str, Entity] = {}
        origin_rounds: dict[str, list[FundingRound]] = {}
        origins: dict[str, set[str]] = {}
        state, error = "completed", None
        try:
            if founder.round.remaining_eur == 0:
                state = "no_open_allocation"
                notices.append("No open allocation. Firm commitments meet or exceed the target; search stopped.")
            else:
                as_of = run.confirmed_at.date()
                startup_id = founder.startup.cala_entity_id
                # Wave 0 (parallel): lead graph, startup people (founders), lead-partner
                # search and your company's own funding rounds (existing investors).
                graph, startup_people, partner_matches, startup_rounds = await asyncio.gather(
                    retrieval.call("lead_graph", {"lead_id": lead.id, "lookback_months": config.lookback_months}, GraphSnapshot, "lead_graph"),
                    self._optional(retrieval, "entity_people", {"entity_id": startup_id}, list[Role],
                                   enabled=bool(startup_id and not founder.founder_person_ids)),
                    self._optional(retrieval, "search_entities", {"query": founder.lead_partner_name or "", "kind": "person"},
                                   list[Entity], enabled=bool(founder.lead_partner_name)),
                    self._optional(retrieval, "company_rounds", {"company_id": startup_id or "", "limit": config.startup_round_limit},
                                   list[FundingRound], enabled=bool(startup_id and config.startup_round_limit)),
                )
                graph_received, graph_complete = True, graph.complete
                retrieved_people.extend([*graph.people, *(startup_people or [])])
                if graph.lead is not None:
                    if graph.lead.id != lead.id:
                        raise CalaError("invalid_lead_graph", "Cala graph identifies a different lead")
                    # Retain source-backed aliases learned after the founder's
                    # ID confirmation so a known vehicle cannot become a card.
                    lead = graph.lead.model_copy(deep=True)
                    run.lead = lead
                if not graph.complete:
                    notices.append("Cala did not confirm complete graph coverage; available shared rounds are shown.")
                # Source 1: investors in dated, sourced funding events with the exact confirmed lead.
                qualified = _qualified_rounds(graph.rounds, lead.id, months_before(as_of, config.lookback_months), as_of)
                for funding in qualified:
                    for participation in funding.participants:
                        investor = participation.investor
                        if investor.id == lead.id:
                            continue
                        candidate_entities[investor.id] = investor
                        candidate_rounds.setdefault(investor.id, []).append(funding)
                        origins.setdefault(investor.id, set()).add("lead_network")
                # Source 2: your company's existing investors from its own identified rounds.
                own_rounds = [funding for funding in deduplicate_rounds(startup_rounds or [])
                              if funding.company.id == startup_id and funding.sources]
                self._add_origin(own_rounds, "existing_investor", lead, candidate_entities, origin_rounds, origins)
                if startup_id and startup_rounds is not None:
                    trace.emit("founder_network", "Read your company's identified funding rounds for existing investors",
                               count=len(own_rounds))
                discovered = len(candidate_entities)
                preliminary = self._preliminary(candidate_entities, origins, candidate_rounds, origin_rounds,
                                                founder, lead, excluded, config.preliminary_limit)
                trace.emit("lead_graph", "Selected bounded preliminary candidates from your lead's network and cap table", count=len(preliminary))
                lead_partner = _resolve_partner(founder.lead_partner_name, graph.people, partner_matches or [], lead)
                if founder.lead_partner_name and lead_partner is None:
                    notices.append("Lead partner could not be matched to a single Cala person; lead-partner loops are unknown.")
                founder_ids = list(dict.fromkeys(founder.founder_person_ids or [
                    role.person.id for role in (startup_people or []) if role.kind == "founded"]))
                # Wave 1: founder/partner roles, then previous-company rounds and
                # organisation people, alongside the first profiling batch.
                people_task = asyncio.create_task(self._trust_people(
                    retrieval, founder_ids, lead_partner, {lead.id, startup_id}, as_of))
                reserve = config.founder_network_profile_slots if founder_ids else 0
                profiled_count += await self._profile(retrieval, preliminary, max(1, config.profile_pool_limit - reserve),
                                                      founder, lead, profiles, excluded, incomplete, notices, trace)
                trust_roles, headcounts, founders, previous_rounds, previous_checked = await people_task
                retrieved_people.extend(trust_roles)
                # Source 3: backers of the founders' previous companies.
                before = set(candidate_entities)
                self._add_origin(previous_rounds, "previous_company_backer", lead, candidate_entities, origin_rounds, origins)
                if previous_rounds:
                    trace.emit("founder_network", "Read the founders' previous companies' funding rounds for their backers",
                               count=len(set(candidate_entities) - before))
                discovered = len(candidate_entities)
                profiled_ids = {p.investor.id for p in profiles} | {e.investor.id for e in excluded} | {e.id for e in incomplete}
                backers = [e for e in self._preliminary({k: v for k, v in candidate_entities.items() if k not in profiled_ids},
                                                        origins, candidate_rounds, origin_rounds, founder, lead, excluded,
                                                        config.preliminary_limit)
                           if "previous_company_backer" in origins[e.id]]
                if backers and retrieval.remaining() > 0:
                    profiled_count += await self._profile(retrieval, backers, config.profile_pool_limit - len(profiles),
                                                          founder, lead, profiles, excluded, incomplete, notices, trace)

                if profiles and self.semantic and getattr(self.semantic, "enabled", True):
                    retrieved_people.extend(role for profile in profiles for role in profile.people)
                    trace.emit("assessments", "Assessing sector and portfolio overlap from retrieved Cala facts")
                    remaining = run.started + config.run_deadline_seconds - time.monotonic() - 0.25
                    if remaining > 0:
                        try:
                            async with asyncio.timeout(min(config.model_timeout_seconds, remaining)):
                                assessed = await self.semantic.assess(founder, profiles)
                            if {p.investor.id for p in assessed} != {p.investor.id for p in profiles}:
                                raise ValueError("Assessment changed the candidate set")
                            profiles = assessed
                            trace.emit("assessments", "Labelled assessments saved in profile snapshots")
                        except Exception:
                            notices.append("Model assessment unavailable; semantic fit remains unknown and sourced draft templates are used.")
                            trace.emit("assessments", "Model unavailable; preserving unknown fit and using template drafts", type="notice")
                    else:
                        notices.append("Drafting deadline reached; using sourced introduction templates.")
                elif profiles:
                    notices.append("Semantic assessments disabled; sector and overlap remain unknown unless supplied in the snapshot.")
                for role in startup_people or []:
                    if role.kind == "founded" and role.person.id not in {p.id for p in founders}:
                        founders.append(role.person)
                if startup_id and not founder_ids:
                    notices.append("No founder roles were found in Cala; founder-network loops are unknown.")
                roles = [*graph.people, *(startup_people or []), *trust_roles,
                         *(role for profile in profiles for role in profile.people)]
                for profile in profiles:
                    count = len({r.person.id for r in profile.people})
                    if 0 < count < PEOPLE_PAGE_LIMIT:
                        headcounts.setdefault(profile.investor.id, count)
                # Industry and headquarters for the most-shared portfolio companies
                # (Sector match / Country match). Bounded; missing ones stay "not checked".
                evidence_rounds = graph.rounds + own_rounds + previous_rounds
                company_counts: dict[str, int] = {}
                for profile in profiles:
                    ids = {r.company.id for r in candidate_rounds.get(profile.investor.id, []) + origin_rounds.get(profile.investor.id, [])
                           + profile.rounds if r.company.id != startup_id}
                    for company_id in ids:
                        company_counts[company_id] = company_counts.get(company_id, 0) + 1
                chosen = sorted(company_counts, key=lambda cid: (-company_counts[cid], cid))[:config.company_context_limit]
                if chosen:
                    trace.emit("company_context", "Checking portfolio company industry and headquarters", count=len(chosen))
                enriched = await asyncio.gather(*(self._optional(retrieval, "entity_metadata", {"entity_id": cid, "company": True},
                                                                 Entity, enabled=retrieval.remaining() > 0,
                                                                 step="company_context") for cid in chosen))
                # The owner rule means portfolio headquarters. Full metadata
                # retains office/registration country proxies for display and
                # inspection, but those proxies cannot become HQ facts.
                # Include known-round companies even when enrichment times out.
                known_companies = [funding.company for funding in evidence_rounds]
                known_companies.extend(funding.company for profile in profiles for funding in profile.rounds)
                context = rule_book.RuleContext(
                    startup_id=startup_id, startup_rounds_checked=startup_rounds is not None,
                    previous_companies_checked=bool(founders or founder_ids) and previous_checked,
                    companies=_portfolio_headquarters_companies(known_companies, enriched))
                if chosen:
                    trace.emit("company_context", "Read industry and headquarters of portfolio companies",
                               count=len({entity.id for entity in enriched if entity is not None}))
                trace.emit("scoring", "Checking each investor against the rule set")
                for profile in profiles:
                    investor_id = profile.investor.id
                    try:
                        cards.append(score_candidate(
                            profile, candidate_rounds.get(investor_id, []), founder, lead, as_of,
                            known_rounds=evidence_rounds,
                            origins=sorted(origins.get(investor_id, {"lead_network"})),
                            origin_rounds=origin_rounds.get(investor_id, []), context=context))
                    except ValueError:
                        incomplete.append(profile.investor)  # no sourced evidence left after validation
                startup = Entity(id=startup_id or f"startup:{run.id}", name=founder.startup.company_name)
                trust_graph, _ = trust.build_graph(
                    startup, founders, lead, lead_partner,
                    [trust.Candidate(card.investor, card.shared_deals, card.origin_rounds) for card in cards],
                    roles, headcounts, as_of)
                confirmed = set(founder.confirmed_loop_ids)
                for card in cards:
                    card.trust, card.loops = trust.measure(trust_graph, founders, lead, lead_partner, card.investor, confirmed)
                trace.emit("loops", "Measured trust loops through your company, founders, common contacts and the lead", type="loops",
                           count=sum(len(card.loops) for card in cards))
                trace.emit("finalizing", "Preparing your shortlist, introduction requests and allocation")
                for card in cards:
                    finalize_card(card, founder, lead)
                cards = ranker.rank(cards)[:config.profile_limit]
                state = "partial" if incomplete or not graph_complete else ("completed" if cards else "no_match")
                if not cards:
                    notices.append(f"No eligible, fully profiled candidate found in your lead's network or your founder network within the {config.lookback_months}-month lookback. No generic investors added.")
        except (BudgetExceeded, TimeoutError):
            state = "partial"
            notices.append("Processing deadline or request budget reached; returning available evidence. Unfinished profiles remain incomplete.")
        except CalaError as exc:
            state = "partial" if graph_received else "failed"
            error = {"code": exc.code, "message": exc.message}
            notices.append("Cala retrieval could not finish; no unsupported candidates were added.")
        except Exception:
            state = "failed"
            error = {"code": "pipeline_error", "message": "The run could not be assembled; inspect server logs and retry."}
            import logging
            logging.getLogger(__name__).exception("Run %s failed", run.id)
        if people_task is not None and not people_task.done():
            people_task.cancel()
        elif people_task is not None and not people_task.cancelled() and people_task.exception() is None:
            # A completed people lookup can survive a later failure elsewhere.
            retrieved_people.extend(people_task.result()[0])
        retrieved_people.extend(role for profile in profiles for role in profile.people)
        finished = utcnow()
        run.result = RunResult(run_id=run.id, state=state, profile=founder, lead=lead,
                              confirmed_at=run.confirmed_at, finished_at=finished,
                              remaining_allocation_eur=founder.round.remaining_eur, lookback_months=config.lookback_months,
                              candidates=cards, exclusions=excluded, incomplete=incomplete,
                              counts={"discovered": discovered, "profiled": profiled_count,
                                      "shortlisted": sum(card.review_status == "short" for card in cards),
                                      "flagged": sum(card.review_status == "flagged" for card in cards),
                                      "returned": len(cards),
                                      "excluded": len(excluded), "incomplete": len(incomplete)},
                              notices=list(dict.fromkeys(notices)), combined_draft=combined_draft(cards, founder, lead) if cards else "",
                              processing_ms=round((time.monotonic() - run.started) * 1000, 2),
                              cala_calls=retrieval.calls, cache_hits=retrieval.cache_hits,
                              retrieval_timestamps=sorted(set(retrieval.timestamps)), error=error,
                              scoring_version=ranker.SCORING_VERSION,
                              people=_serialized_people(retrieved_people),
                              allocation=build_allocation(cards, founder.round.remaining_eur))
        trace.emit("complete", f"Run finished: {state}", type="complete", count=len(cards))


def _merge_sources(*groups):
    sources = {(str(item.url), str(item.published_at), item.retrieved_at.isoformat(), item.label): item
               for group in groups for item in group}
    return [sources[key] for key in sorted(sources)]


def _merge_role_entities(first: Entity, second: Entity) -> Entity:
    """Keep richer identity details without merging separate Cala identities."""
    def richness(entity):
        data = entity.model_dump(mode="json", exclude_none=True)
        return len(entity.metadata), sum(bool(value) for value in data.values()), json.dumps(data, sort_keys=True)

    primary, other = sorted((first, second), key=richness, reverse=True)
    properties = {}
    for item in [*primary.metadata, *other.metadata]:
        key = (item.name, json.dumps(item.model_dump(mode="json")["value"], sort_keys=True, ensure_ascii=False))
        if key in properties:
            item = item.model_copy(update={"sources": _merge_sources(properties[key].sources, item.sources)})
        properties[key] = item
    updates = {"sources": _merge_sources(primary.sources, other.sources),
               "aliases": sorted(set(primary.aliases + other.aliases)),
               "related_entity_ids": sorted(set(primary.related_entity_ids + other.related_entity_ids)),
               "metadata": [properties[key] for key in sorted(properties)]}
    for field in ("entity_type", "description", "website", "headquarters_address", "office_address", "registered_address"):
        if getattr(primary, field) is None and getattr(other, field) is not None:
            updates[field] = getattr(other, field)
    for field in ("country", "kind"):
        if getattr(primary, field) == getattr(other, field) and getattr(primary, field + "_basis") == getattr(other, field + "_basis"):
            updates[field + "_sources"] = _merge_sources(getattr(primary, field + "_sources"), getattr(other, field + "_sources"))
    return primary.model_copy(update=updates, deep=True)


def _serialized_people(roles: list[Role]) -> list[Role]:
    """Deduplicate the display snapshot; leave trust/scoring role inputs intact."""
    people = {}
    for role in sorted(roles, key=lambda role: json.dumps(role.model_dump(mode="json"), sort_keys=True)):
        if not role.sources:
            continue
        key = (role.person.id, role.org.id, role.kind, role.valid_since, role.valid_until, role.current)
        if key in people:
            prior = people[key]
            role = role.model_copy(update={"person": _merge_role_entities(prior.person, role.person),
                                           "org": _merge_role_entities(prior.org, role.org),
                                           "sources": _merge_sources(prior.sources, role.sources)}, deep=True)
        else:
            role = role.model_copy(update={"sources": _merge_sources(role.sources)}, deep=True)
        people[key] = role
    return [people[key] for key in sorted(people, key=lambda key: tuple(str(value) for value in key))]


def _portfolio_headquarters_companies(known: list[Entity], enriched: list[Entity | None]) -> dict[str, Entity]:
    """Project metadata into the existing headquarters-only rule contract.

    Copies retain addresses, raw properties, provenance and sources. The public
    entities stay intact; only this private rule view omits proxy countries.
    """
    companies = {}
    for entity in [*known, *(item for item in enriched if item is not None)]:
        basis = (entity.country_basis or "").casefold()
        fallback = basis.startswith(("office", "registered", "registration"))
        companies[entity.id] = entity.model_copy(update={"country": None} if fallback else {}, deep=True)
    return companies


def _resolve_partner(name: str | None, lead_people: list[Role], matches: list[Entity], lead: Entity) -> Entity | None:
    """Prefer a person Cala lists at the lead fund; otherwise one exact-name search match."""
    if not name:
        return None
    wanted = normalized_name(name)
    at_lead = {role.person.id: role.person for role in lead_people if role.org.id == lead.id}
    by_id = [m for m in matches if m.id in at_lead]
    if len(by_id) == 1:
        return at_lead[by_id[0].id]
    named = [p for p in at_lead.values() if normalized_name(p.name) == wanted]
    if len(named) == 1:
        return named[0]
    exact = [m for m in matches if normalized_name(m.name) == wanted]
    return exact[0] if len(exact) == 1 else None


def _qualified_rounds(rounds, lead_id, start_date, end_date):
    """Funding IDs are identity. Merge repeated references without inflating counts."""
    return [funding for funding in deduplicate_rounds(rounds)
            if funding.event_date and start_date <= funding.event_date <= end_date
            and funding.sources and any(p.investor.id == lead_id for p in funding.participants)]


def _startup_for_detection(profile: FounderInput, matches: list[Entity]) -> Entity | None:
    """Use an exact name/alias or explicit website, never the first fuzzy match."""
    if profile.startup.cala_entity_id:
        return next((item for item in matches if item.id == profile.startup.cala_entity_id), None)

    def hostname(value):
        if not value:
            return None
        text = str(value)
        parsed = urlsplit(text if "://" in text else "https://" + text)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        return host or None

    name = normalized_name(profile.startup.company_name)
    exact = [item for item in matches if name in {normalized_name(item.name), *(normalized_name(alias) for alias in item.aliases)}]
    requested_host = hostname(profile.startup.website)
    if requested_host:
        website_matches = [item for item in matches if hostname(item.website) == requested_host]
        exact_website = [item for item in exact if item in website_matches]
        if len(exact_website) == 1:
            return exact_website[0]
        if len(website_matches) == 1 and not exact:
            return website_matches[0]
        if len(exact) == 1 and (not exact[0].website or hostname(exact[0].website) == requested_host):
            return exact[0]
        return None
    return exact[0] if len(exact) == 1 else None
