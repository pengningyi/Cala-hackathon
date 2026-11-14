from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Settings
from .models import (
    Entity, FounderInput, FundingRound, GraphSnapshot, InvestorProfile, Resolution,
    ResolveRequest, RunResult, RunStatus, StartRunRequest,
)
from .runtime import BudgetExceeded, ResponseCache, Retrieval, Trace, utcnow
from .scoring import (
    combined_draft, deduplicate_rounds, exclusion_for, months_before, normalize_stage,
    rank_candidates, score_candidate,
)


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


class Workflow:
    def __init__(self, settings: Settings, provider, semantic=None):
        self.settings, self.provider, self.semantic = settings, provider, semantic
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
        self.runs: dict[str, RunRecord] = {}

    def cleanup(self):
        now = utcnow()
        self.resolutions = {k: v for k, v in self.resolutions.items() if v.expires_at > now}
        self.runs = {k: v for k, v in self.runs.items()
                     if v.result is None or (now - v.result.finished_at).total_seconds() < self.settings.retention_seconds}

    async def resolve(self, request: ResolveRequest):
        self.cleanup()
        if len(self.resolutions) >= self.settings.max_stored_runs:
            raise WorkflowError("capacity", "Resolution capacity reached; retry after older resolutions expire", 429)
        started, now = time.monotonic(), utcnow()
        leads, startups, notices = [], [], []
        if request.profile.round.remaining_eur > 0:
            retrieval = Retrieval(self.provider, self.cache, self.settings, Trace(), semaphore=self.semaphore)
            # Resolution latency is separate from confirmed-run processing.
            retrieval.stop_at = started + self.settings.cala_request_timeout_seconds
            leads = await retrieval.call("search_entities", {"query": request.profile.lead_name, "kind": "investor"}, list[Entity], "resolver")
            if request.lookup_startup:
                try:
                    startups = await retrieval.call("search_entities", {"query": request.profile.startup.company_name, "kind": "company"}, list[Entity], "resolver")
                except Exception:
                    notices.append("Optional startup lookup unavailable; confirm the manual startup profile.")
            if not leads:
                notices.append("No Cala lead match found. Refine the lead name before starting a run.")
        else:
            notices.append("No open allocation. No Cala search was performed.")
        resolution = Resolution(id=uuid.uuid4().hex, profile=request.profile.model_copy(deep=True),
                                lead_matches=leads, startup_matches=startups,
                                created_at=now, expires_at=now + timedelta(seconds=self.settings.retention_seconds),
                                resolver_ms=round((time.monotonic() - started) * 1000, 2),
                                no_open_allocation=request.profile.round.remaining_eur == 0, notices=notices)
        self.resolutions[resolution.id] = resolution
        return resolution

    def start(self, request: StartRunRequest):
        self.cleanup()
        resolution = self.resolutions.get(request.resolution_id)
        if resolution is None:
            raise WorkflowError("resolution_not_found", "Resolution missing or expired; resolve again", 404)
        if request.profile.lead_name != resolution.profile.lead_name:
            raise WorkflowError("lead_changed", "Resolve the updated lead name before confirmation")
        if request.profile.startup.cala_entity_id and request.profile.startup.cala_entity_id not in {e.id for e in resolution.startup_matches}:
            raise WorkflowError("invalid_startup_selection", "Select a startup ID offered by this resolution or use a manual profile")
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
        discovered, profiled_count, graph_complete, graph_received = 0, 0, False, False
        candidate_rounds: dict[str, list[FundingRound]] = {}
        candidate_entities: dict[str, Entity] = {}
        state, error = "completed", None
        try:
            if founder.round.remaining_eur == 0:
                state = "no_open_allocation"
                notices.append("No open allocation. Firm commitments meet or exceed the target; search stopped.")
            else:
                as_of = run.confirmed_at.date()
                graph = await retrieval.call("lead_graph", {"lead_id": lead.id, "lookback_months": config.lookback_months}, GraphSnapshot, "lead_graph")
                graph_received, graph_complete = True, graph.complete
                if graph.lead is not None:
                    if graph.lead.id != lead.id:
                        raise CalaError("invalid_lead_graph", "Cala graph identifies a different lead")
                    # Retain source-backed aliases learned after the founder's
                    # ID confirmation so a known vehicle cannot become a card.
                    lead = graph.lead.model_copy(deep=True)
                    run.lead = lead
                if not graph.complete:
                    notices.append("Cala did not confirm complete graph coverage; available shared rounds are shown.")
                # Only dated, sourced funding events with the exact confirmed lead qualify.
                qualified = _qualified_rounds(graph.rounds, lead.id, months_before(as_of, config.lookback_months), as_of)
                for funding in qualified:
                    for participation in funding.participants:
                        investor = participation.investor
                        if investor.id == lead.id:
                            continue
                        candidate_entities[investor.id] = investor
                        candidate_rounds.setdefault(investor.id, []).append(funding)
                discovered = len(candidate_entities)
                preliminary = []
                for investor in candidate_entities.values():
                    exclusion = exclusion_for(investor, founder, lead)
                    if exclusion:
                        excluded.append(exclusion)
                    else:
                        preliminary.append(investor)
                preliminary.sort(key=lambda e: (
                    -len({r.company.id for r in candidate_rounds[e.id]}),
                    -max(r.event_date.toordinal() for r in candidate_rounds[e.id]),
                    -sum(normalize_stage(r.stage) == founder.round.stage for r in candidate_rounds[e.id]),
                    e.name.casefold(), e.id))
                preliminary = preliminary[:config.preliminary_limit]
                trace.emit("lead_graph", "Selected bounded preliminary candidates with identified shared rounds", count=len(preliminary))
                cursor = 0
                # Backfill only exclusions, respecting the same remaining time/call budget.
                while len(profiles) < config.profile_limit and cursor < len(preliminary) and retrieval.remaining() > 0:
                    batch = preliminary[cursor:cursor + config.profile_limit - len(profiles)]
                    cursor += len(batch)

                    async def profile_one(entity):
                        try:
                            profile = await retrieval.call("investor_profile", {"investor_id": entity.id, "lookback_months": 24}, InvestorProfile, "profiles")
                            if profile.investor.id != entity.id:
                                raise CalaError("invalid_profile", "Cala returned a different investor ID")
                            return entity, profile, None
                        except (CalaError, BudgetExceeded) as exc:
                            return entity, None, exc

                    for entity, profile, failure in await asyncio.gather(*(profile_one(e) for e in batch)):
                        if failure:
                            incomplete.append(entity)
                            notices.append(f"Profile incomplete for {entity.name}; not rejected.")
                            trace.emit("profiles", "Candidate profile unavailable; retained as incomplete", type="notice", count=1)
                            continue
                        profiled_count += 1
                        exclusion = exclusion_for(profile.investor, founder, lead, profile)
                        if exclusion:
                            excluded.append(exclusion)
                        else:
                            profiles.append(profile)
                    # A failed retrieval remains pending; do not spend the full budget chasing replacements.
                    if incomplete:
                        break
                if cursor < len(preliminary) and len(profiles) < config.profile_limit:
                    incomplete.extend(preliminary[cursor:])

                if profiles and self.semantic and getattr(self.semantic, "enabled", True):
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
                trace.emit("scoring", "Applying frozen PRD weights, status rules and deterministic ranking")
                for profile in profiles:
                    cards.append(score_candidate(profile, candidate_rounds[profile.investor.id], founder, lead, as_of))
                cards = rank_candidates(cards)[:config.profile_limit]
                state = "partial" if incomplete or not graph_complete else ("completed" if cards else "no_match")
                if not cards:
                    notices.append(f"No eligible, fully profiled lead-connected candidate found within the {config.lookback_months}-month lookback. No generic investors added.")
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
        finished = utcnow()
        run.result = RunResult(run_id=run.id, state=state, profile=founder, lead=lead,
                              confirmed_at=run.confirmed_at, finished_at=finished,
                              remaining_allocation_eur=founder.round.remaining_eur, lookback_months=config.lookback_months,
                              candidates=cards, exclusions=excluded, incomplete=incomplete,
                              counts={"discovered": discovered, "profiled": profiled_count, "shortlisted": len(cards),
                                      "excluded": len(excluded), "incomplete": len(incomplete)},
                              notices=list(dict.fromkeys(notices)), combined_draft=combined_draft(cards, founder, lead) if cards else "",
                              processing_ms=round((time.monotonic() - run.started) * 1000, 2),
                              cala_calls=retrieval.calls, cache_hits=retrieval.cache_hits,
                              retrieval_timestamps=sorted(set(retrieval.timestamps)), error=error)
        trace.emit("complete", f"Run finished: {state}", type="complete", count=len(cards))


def _qualified_rounds(rounds, lead_id, start_date, end_date):
    """Funding IDs are identity. Merge repeated references without inflating counts."""
    return [funding for funding in deduplicate_rounds(rounds)
            if funding.event_date and start_date <= funding.event_date <= end_date
            and funding.sources and any(p.investor.id == lead_id for p in funding.participants)]
