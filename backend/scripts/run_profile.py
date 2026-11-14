"""Exercise the real HTTP workflow and record timings; contains no investor data."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path

import httpx


async def execute(args):
    scenarios = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in args.profile]
    choices, records = {}, []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        for index in range(args.runs):
            path, request = scenarios[index % len(scenarios)]
            response = await client.post("/api/resolve", json=request)
            response.raise_for_status()
            resolution = response.json()
            print(f"{path.name}: resolver {resolution['resolver_ms']:.1f} ms")
            selected = choices.get(str(path), args.lead_id)
            if not resolution["no_open_allocation"]:
                for entity in resolution["lead_matches"]:
                    print(f"  {entity['id']}: {entity['name']} | {entity['country'] or 'country unknown'} | {entity.get('website') or 'website unknown'}")
                if selected is None:
                    selected = input("Confirm the startup profile in your JSON and enter the correct lead ID (blank cancels): ").strip()
                    if not selected:
                        raise RuntimeError("Founder confirmation cancelled")
                if selected not in {e["id"] for e in resolution["lead_matches"]}:
                    raise RuntimeError("Chosen lead ID was not returned by this resolution")
                choices[str(path)] = selected
            response = await client.post("/api/runs", json={
                "resolution_id": resolution["id"], "lead_id": selected,
                "profile_confirmed": True, "profile": request["profile"],
            })
            response.raise_for_status()
            status = response.json()
            poll_deadline = time.monotonic() + 100
            while True:
                result_response = await client.get(status["result_url"])
                result_response.raise_for_status()
                if result_response.status_code != 202:
                    break
                if time.monotonic() >= poll_deadline:
                    raise RuntimeError("Backend did not finish within the expected processing deadline")
                await asyncio.sleep(0.25)
            result = result_response.json()
            record = {
                "scenario": path.name, "resolver_ms": resolution["resolver_ms"],
                "processing_ms": result["processing_ms"], "cala_calls": result["cala_calls"],
                "cache_hits": result["cache_hits"],
                "cache_observation": "cache_used" if result["cache_hits"] else "no_cache_hits",
                "state": result["state"], "counts": result["counts"],
                "mean_profile_completeness": statistics.mean(c["profile_completeness"] for c in result["candidates"]) if result["candidates"] else None,
                "has_copyable_draft": bool(result["candidates"]) and all(c["draft"].strip() for c in result["candidates"]),
                "founder_actionable_feedback": None, "result": result,
            }
            records.append(record)
            print(f"  {result['state']}: {result['counts']['shortlisted']} cards, {result['processing_ms']:.1f} ms, {result['cala_calls']} calls, {result['cache_hits']} cache hits")
    durations = sorted(record["processing_ms"] for record in records)
    report = {
        "source": "live HTTP workflow; no synthetic data adapter",
        "runs": len(records), "scenarios": len(scenarios),
        "median_processing_ms": statistics.median(durations),
        "p95_processing_ms": durations[math.ceil(len(durations) * 0.95) - 1],
        "draft_availability_rate": sum(r["has_copyable_draft"] for r in records) / len(records),
        "note": "Draft availability is technical only; founder usefulness feedback is collected separately. Cache use can be mixed within a run.",
        "records": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {args.report}; median {report['median_processing_ms']:.1f} ms, p95 {report['p95_processing_ms']:.1f} ms")
    return 1 if any(r["state"] == "failed" for r in records) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, action="append", required=True, help="JSON ResolveRequest; repeat for different scenarios")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--lead-id", help="Explicit founder-confirmed Cala ID; otherwise select interactively per scenario")
    parser.add_argument("--report", type=Path, default=Path("reports/live.json"))
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 100:
        parser.error("--runs must be between 1 and 100")
    try:
        return asyncio.run(execute(args))
    except httpx.HTTPStatusError as exc:
        print(f"HTTP {exc.response.status_code}: {exc.response.text}")
        return 1
    except (OSError, ValueError, RuntimeError, httpx.RequestError) as exc:
        print(f"Workflow could not finish: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
