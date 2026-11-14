"""Measure ten API-confirmed runs against live Cala, using hypothetical founders.

Run from backend/: .venv/Scripts/python scripts/acceptance_live.py
Credentials are read by Settings from the ignored backend/.env. The runner never
prints credentials, request headers, or exception bodies. All lead commitments
and founder profiles below are explicitly hypothetical, including the Cala demo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path
import statistics
import sys
import time
import uuid

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.main import create_app
from app.models import FounderInput, RunResult
from app.runtime import utcnow


SCENARIOS = {
    "cala": {
        "lead_name": "Lightspeed Venture Partners",
        "lead_id": "5a9d0c1c-1e04-4816-b38a-28c4f560569b",
        "company_name": "Cala - hypothetical seed demo",
        "product_description": "Verified public knowledge graph for AI agents",
        "sector": "AI infrastructure",
        "target_customer": "Teams building AI agents",
        "problem_solved": "Reliable structured public knowledge",
        "country": "Spain", "stage": "seed",
    },
    "arcane": {
        "lead_name": "Seedcamp",
        "lead_id": "aa87dde8-7dc3-4fc0-957f-143da6ddc0b7",
        "company_name": "Arcane - hypothetical marketing seed demo",
        "product_description": "AI workspace for planning and producing marketing campaigns",
        "sector": "Marketing software",
        "target_customer": "Marketing teams at small businesses",
        "problem_solved": "Fragmented campaign planning and repetitive content production",
        "country": "United Kingdom", "stage": "seed",
    },
    "nordic_grid": {
        "lead_name": "Lifeline Ventures",
        "lead_id": "476a73ac-afa2-4713-9f73-188bc7ec0b95",
        "company_name": "Nordic Grid - fictional Series A demo",
        "product_description": "Grid flexibility software for aggregating industrial electricity demand",
        "sector": "Energy software",
        "target_customer": "Electricity aggregators and industrial energy managers",
        "problem_solved": "Coordinating flexible demand to balance renewable electricity supply",
        "country": "Finland", "stage": "series_a",
    },
}

SCHEDULE = ["cala", "cala", "arcane", "arcane", "nordic_grid", "nordic_grid",
            "cala", "arcane", "nordic_grid", "cala"]


class ApiFailure(Exception):
    """Only a controlled stage/status is included; remote error bodies stay private."""


def profile_for(scenario):
    return FounderInput.model_validate({
        "startup": {key: scenario[key] for key in (
            "company_name", "product_description", "sector", "target_customer", "problem_solved", "country")},
        "lead_name": scenario["lead_name"],
        "round": {
            "stage": scenario["stage"], "target_eur": "10000000",
            "firm_commitments_eur": "6000000", "conditional_interest_eur": "500000",
            "lead_status": "conditional",
        },
    }).model_dump(mode="json")


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def distribution(values):
    return {"count": len(values), "median": round(statistics.median(values), 3) if values else None,
            "p95": round(percentile(values, 0.95), 3) if values else None,
            "minimum": round(min(values), 3) if values else None,
            "maximum": round(max(values), 3) if values else None}


def validate_result(result: RunResult, expected_lead_id, events, deadline, call_limit):
    checks = {
        "selected_lead_matches_confirmation": result.lead is not None and result.lead.id == expected_lead_id,
        "conditional_lead": result.profile.round.lead_status == "conditional",
        "at_most_five_cards": len(result.candidates) <= 5,
        "has_usable_card": bool(result.candidates),
        "within_call_budget": result.cala_calls <= call_limit,
        "within_processing_deadline": result.processing_ms <= deadline * 1000,
        "normalized_scores": all(0 <= card.fit_score <= 100 and 0 <= card.profile_completeness <= 1
                                 for card in result.candidates),
        "same_identified_round": all(
            card.shared_deals and all(
                deal.id and expected_lead_id in {p.investor.id for p in deal.participants}
                and card.investor.id in {p.investor.id for p in deal.participants}
                for deal in card.shared_deals)
            for card in result.candidates),
        "sourced_dated_deals": all(
            deal.event_date is not None and deal.sources
            and all(source.url and source.retrieved_at for source in deal.sources)
            for card in result.candidates for deal in card.shared_deals),
        "known_components_sourced": all(component.sources for card in result.candidates
                                         for component in card.components if component.known),
        "public_considerations_sourced": all(
            item.sources for card in result.candidates for item in card.considerations
            if item.basis in {"Cala fact", "LLM assessment"}),
        "drafts_present_and_provisional": all(
            bool(card.draft.strip()) and "provisional" in card.draft.lower() for card in result.candidates),
        "combined_draft_present": bool(result.combined_draft.strip()) if result.candidates else True,
        "cala_retrieval_timestamps_present": bool(result.retrieval_timestamps),
        "sse_confirmation_and_complete": bool(events) and events[0]["step"] == "confirmation"
                                         and events[-1]["type"] == "complete",
        "sse_sequence_contiguous": [event["id"] for event in events] == list(range(1, len(events) + 1)),
        "public_facts_not_from_fixture": all(
            "synthetic.example.test" not in str(source.url)
            for card in result.candidates for source in card.sources),
        "not_failed": result.state not in {"failed", "no_match", "no_open_allocation"},
    }
    return {name: bool(value) for name, value in checks.items()}


def summary(rows):
    finished = [row for row in rows if "result" in row]
    return {
        "confirmed_runs": len(finished),
        "resolver_ms": distribution([row["resolver_ms"] for row in finished]),
        "processing_ms": distribution([row["result"]["processing_ms"] for row in finished]),
        "cala_calls": distribution([row["result"]["cala_calls"] for row in finished]),
        "cache_hits": distribution([row["result"]["cache_hits"] for row in finished]),
        "profile_completeness": distribution([card["profile_completeness"]
                                             for row in finished for card in row["result"]["candidates"]]),
        "usable_result_rate": sum(bool(row["result"]["candidates"]) for row in finished) / len(rows) if rows else 0,
        "all_assertions_pass": bool(rows) and all(row.get("checks") and all(row["checks"].values()) for row in rows),
        "draft_availability_rate": sum(bool(card["draft"].strip()) for row in finished
                                       for card in row["result"]["candidates"]) / max(1, sum(
                                           len(row["result"]["candidates"]) for row in finished)),
    }


async def execute():
    logging.basicConfig(level=logging.WARNING)
    batch_id = utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    cache_root = BACKEND / ".runtime" / "acceptance-cache" / batch_id
    cache_root.mkdir(parents=True, exist_ok=True)
    config = Settings(model_provider="codex", cache_dir=cache_root, cache_ttl_seconds=3600)
    app = create_app(config)
    if not app.state.workflow.provider.configured:
        raise ApiFailure("Live Cala credentials are not configured")
    report_path = BACKEND / "reports" / "backend-acceptance.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "batch_id": batch_id, "started_at": utcnow().isoformat(),
        "data_source": "Live Cala MCP; no fixture responses",
        "founder_context": "All three startup/round profiles and lead commitments are hypothetical. Nordic Grid is fictional.",
        "model_provider": config.model_provider,
        "scope": {"lookback_months": config.lookback_months, "profile_window_months": 24,
                  "deadline_seconds": config.run_deadline_seconds, "call_limit": config.cala_call_limit,
                  "concurrency": config.cala_concurrency, "cache_root": str(cache_root)},
        "cold_warm_definition": "A separate new cache directory per scenario; first confirmed run cold, later runs warm. MCP tool metadata is reused across scenarios. In-flight deduplication is recorded as cache use.",
        "percentile_method": "Linear interpolation at p*(n-1)",
        "limitations": [
            "Bounded Cala graph/profile coverage produces explicit partial results; this is not exhaustive investor discovery.",
            "Draft availability and evidence checks establish technical usability; founder feedback about an actionable introduction is not collected by this script.",
            "Status, no-allocation, no-match, timeout, exclusion, and synthetic concurrency acceptance cases are covered separately by automated tests.",
        ],
        "runs": [],
    }

    def save():
        report["summary"] = summary(report["runs"])
        report["by_cache_state"] = {state: summary([row for row in report["runs"] if row["cache_state"] == state])
                                    for state in ("cold", "warm")}
        report["by_scenario"] = {name: summary([row for row in report["runs"] if row["scenario"] == name])
                                  for name in SCENARIOS}
        temporary = report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(report_path)

    seen = set()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acceptance.local") as client:
            for index, name in enumerate(SCHEDULE, start=1):
                scenario = SCENARIOS[name]
                row = {"number": index, "scenario": name, "cache_state": "warm" if name in seen else "cold",
                       "started_at": utcnow().isoformat()}
                seen.add(name)
                # All prior work is complete; only the raw-response cache directory changes.
                app.state.workflow.cache.directory = cache_root / name
                profile = profile_for(scenario)
                print(f"Run {index}/10: {name} ({row['cache_state']})", flush=True)
                try:
                    response = await client.post("/api/resolve", json={"profile": profile, "lookup_startup": False})
                    if response.status_code != 200:
                        raise ApiFailure(f"Entity resolution returned HTTP {response.status_code}")
                    resolved = response.json()
                    row["resolver_ms"] = resolved["resolver_ms"]
                    if scenario["lead_id"] not in {entity["id"] for entity in resolved["lead_matches"]}:
                        raise ApiFailure("Expected lead ID was not offered by live Cala resolution")
                    confirmed = await client.post("/api/runs", json={
                        "resolution_id": resolved["id"], "lead_id": scenario["lead_id"],
                        "profile_confirmed": True, "profile": profile,
                    })
                    if confirmed.status_code != 202:
                        raise ApiFailure(f"Run confirmation returned HTTP {confirmed.status_code}")
                    status = confirmed.json()
                    stop = time.monotonic() + config.run_deadline_seconds + 5
                    while True:
                        response = await client.get(status["result_url"])
                        if response.status_code == 200:
                            break
                        if response.status_code != 202:
                            raise ApiFailure(f"Run result returned HTTP {response.status_code}")
                        if time.monotonic() >= stop:
                            raise ApiFailure("API result exceeded the global deadline plus reporting grace period")
                        await asyncio.sleep(0.05)
                    result = RunResult.model_validate(response.json())
                    trace_response = await client.get(status["events_url"])
                    if trace_response.status_code != 200:
                        raise ApiFailure(f"Progress trace returned HTTP {trace_response.status_code}")
                    events = [json.loads(line[6:]) for line in trace_response.text.splitlines() if line.startswith("data: ")]
                    row["result"] = result.model_dump(mode="json")
                    row["events"] = events
                    row["checks"] = validate_result(result, scenario["lead_id"], events,
                                                    config.run_deadline_seconds, config.cala_call_limit)
                    print(f"  {result.state}: {result.processing_ms / 1000:.3f}s, {result.cala_calls} Cala calls, "
                          f"{result.cache_hits} cache hits, {len(result.candidates)} cards, "
                          f"checks {'PASS' if all(row['checks'].values()) else 'FAIL'}", flush=True)
                except ApiFailure as exc:
                    row["error"] = str(exc)
                    print(f"  FAILED: {exc}", flush=True)
                except Exception as exc:
                    # Never serialize remote/client exceptions that could contain headers.
                    row["error"] = f"Acceptance execution failed ({type(exc).__name__}); inspect local server behavior"
                    print(f"  FAILED: {type(exc).__name__}", flush=True)
                report["runs"].append(row)
                save()
    report["finished_at"] = utcnow().isoformat()
    save()
    print(json.dumps({"report": str(report_path), "summary": report["summary"]}, indent=2), flush=True)
    return 0 if len(report["runs"]) == 10 and report["summary"]["confirmed_runs"] == 10 and report["summary"]["all_assertions_pass"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(execute()))
    except ApiFailure as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
