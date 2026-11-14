"""Append one current-cap semantic API measurement without rewriting ten-run stats."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import sys
import time

import httpx

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from acceptance_live import validate_result
from app.config import Settings
from app.main import create_app
from app.models import RunResult
from app.runtime import utcnow


def original_fingerprint(report):
    original = {key: value for key, value in report.items() if key != "supplemental_runs"}
    return hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()


async def execute():
    logging.basicConfig(level=logging.WARNING)
    report_path = BACKEND / "reports" / "backend-acceptance.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    original_hash = original_fingerprint(report)
    previous = next(row for row in report["runs"] if row["scenario"] == "cala")
    profile, lead = previous["result"]["profile"], previous["result"]["lead"]
    cache_dir = Path(report["scope"]["cache_root"]) / "cala"
    config = Settings(model_provider="codex", model_timeout_seconds=20,
                      cache_dir=cache_dir, cache_ttl_seconds=3600)
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://acceptance.local") as client:
            resolved = await client.post("/api/resolve", json={"profile": profile, "lookup_startup": False})
            if resolved.status_code != 200:
                raise RuntimeError(f"Resolution returned HTTP {resolved.status_code}")
            resolution = resolved.json()
            if lead["id"] not in {entity["id"] for entity in resolution["lead_matches"]}:
                raise RuntimeError("Expected lead ID was not offered by Cala resolution")
            started = await client.post("/api/runs", json={
                "resolution_id": resolution["id"], "lead_id": lead["id"],
                "profile_confirmed": True, "profile": profile,
            })
            if started.status_code != 202:
                raise RuntimeError(f"Confirmation returned HTTP {started.status_code}")
            status = started.json()
            stop = time.monotonic() + config.run_deadline_seconds + 5
            while True:
                response = await client.get(status["result_url"])
                if response.status_code == 200:
                    break
                if response.status_code != 202 or time.monotonic() >= stop:
                    raise RuntimeError("Confirmed run did not finish within its API budget")
                await asyncio.sleep(0.05)
            result = RunResult.model_validate(response.json())
            trace_response = await client.get(status["events_url"])
            if trace_response.status_code != 200:
                raise RuntimeError("Progress trace unavailable")
            events = [json.loads(line[6:]) for line in trace_response.text.splitlines() if line.startswith("data: ")]
    checks = validate_result(result, lead["id"], events, config.run_deadline_seconds, config.cala_call_limit)
    entries = [(card, assessment) for card in result.candidates for assessment in card.profile.assessments]
    checks["at_least_five_semantic_assessments"] = len(entries) >= 5
    checks["every_candidate_has_assessment"] = all(card.profile.assessments for card in result.candidates)
    checks["semantic_sources_belong_to_retrieved_profile"] = all(
        assessment.sources and assessment.company_id in {event.company.id for event in card.profile.rounds}
        and {str(source.url) for source in assessment.sources}.issubset({
            str(source.url) for event in card.profile.rounds for source in [*event.sources, *event.company.sources]
        }) for card, assessment in entries
    )
    checks["semantic_assessments_labelled"] = all(assessment.label == "LLM assessment" for _, assessment in entries)
    checks["no_model_failure_notice"] = not any("Model assessment unavailable" in notice for notice in result.notices)
    supplemental = {
        "recorded_at": utcnow().isoformat(), "scenario": "cala", "cache_state": "warm",
        "purpose": "Representative whole HTTP/SSE pipeline with the current 20-second Codex cap; excluded from the original ten-run statistics.",
        "model_provider": config.model_provider, "model_timeout_seconds": config.model_timeout_seconds,
        "resolver_ms": resolution["resolver_ms"], "semantic_assessment_count": len(entries),
        "checks": checks, "all_assertions_pass": all(checks.values()),
        "result": result.model_dump(mode="json"), "events": events,
    }
    report.setdefault("supplemental_runs", []).append(supplemental)
    if original_fingerprint(report) != original_hash:
        raise RuntimeError("Original benchmark content changed unexpectedly")
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(report_path)
    print(json.dumps({
        "processing_ms": result.processing_ms, "resolver_ms": resolution["resolver_ms"],
        "cala_calls": result.cala_calls, "cache_hits": result.cache_hits,
        "cards": len(result.candidates), "state": result.state,
        "semantic_assessment_count": len(entries), "checks": checks,
        "original_benchmark_preserved": True, "report": str(report_path),
    }, indent=2), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(execute()))
    except Exception as exc:
        # Exception text from MCP or model transports could contain sensitive context.
        print(f"Supplemental acceptance failed ({type(exc).__name__})", file=sys.stderr)
        raise SystemExit(2)
