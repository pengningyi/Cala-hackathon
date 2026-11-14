"""Agent pipeline: Resolver -> Graph Explorer -> Sector Scout -> Profiler -> Scorer -> Writer.

Each step emits events that the UI renders live:
  step     {id, status: start|done, label, detail}
  call     {endpoint, input, ms, n, cached}           (emitted by the Cala client)
  node     {node: {id, kind, label, sub}}              kind: lead|deal|competitor|investor|round
  edge     {edge: {from, to, kind, weight}}            kind: lead-deal|deal-inv|inv-round|conflict
  result   {candidates, summary, allocation}
  error    {message}

Cala's knowledge_query output schema depends on the query, so every row goes through
`norm_round`, which accepts the common key spellings. Run scripts/probe.py once with your key
to see the raw shapes and extend the key lists below if needed.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import date
from typing import Any, Awaitable, Callable, Optional

from cala import Cala, CalaError
from scoring import allocation, build_reason, parse_date, parse_money, score_candidate, stage_key
import writer

Emit = Callable[[dict], Awaitable[None]]

MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "16"))
FUNDING_TERMS = ("INVEST", "FUND", "ROUND", "CORPORATE_EVENT", "BACK", "PORTFOLIO")


# ----------------------------------------------------------------- helpers
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k]
    lower = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] not in (None, "", []):
            return lower[k.lower()]
    return None


def as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            out.append(x.get("name") if isinstance(x, dict) else str(x))
        return [x.strip() for x in out if x and str(x).strip()]
    if isinstance(v, dict):
        return [v.get("name", "")] if v.get("name") else []
    parts = re.split(r",|;| and |\|", str(v))
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in ("unknown", "n/a", "undisclosed")]


def norm_round(row: dict) -> dict:
    investors = as_list(pick(row, "investors", "investor_names", "participants", "backers", "investor", "participating_investors"))
    leads = as_list(pick(row, "lead_investor", "lead_investors", "lead", "led_by", "lead_investor_name"))
    return {
        "company": str(pick(row, "company", "company_name", "startup", "organization", "name") or "").strip(),
        "investors": list(dict.fromkeys(investors + leads)),
        "leads": leads,
        "date": pick(row, "date", "announced_date", "announcement_date", "funding_date", "announced_on", "year"),
        "stage": pick(row, "round_type", "series", "stage", "funding_type", "round", "round_name"),
        "amount": pick(row, "amount", "funding_amount", "money_raised", "amount_raised", "size"),
        "sector": pick(row, "industry", "sector", "industries", "category", "vertical"),
        "country": pick(row, "country", "headquarters_country", "location", "hq_country"),
    }


def same(a: str, b: str) -> bool:
    a, b = slug(a), slug(b)
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


async def rows(cala: Cala, text: str) -> list[dict]:
    try:
        data = await cala.query(text)
    except CalaError:
        return []
    return [norm_round(r) for r in (data.get("results") or []) if isinstance(r, dict)]


# ----------------------------------------------------------------- resolver
async def resolve(cala: Cala, form: dict) -> dict:
    """Step 1. Fuzzy-match company and lead; auto-detect the lead from the company's latest round."""
    company = form.get("company", "").strip()
    comps = await cala.search_entities(company, ["Company", "Organization"], limit=6)
    leads, detected = [], None
    if form.get("lead", "").strip():
        leads = await cala.search_entities(form["lead"].strip(), ["Company", "Organization"], limit=6)
    else:
        latest = await rows(cala, f"funding_rounds.company={company}.order_by=date DESC.limit=3"
                                  f".return(company, round_type, date, amount, lead_investor, investors)")
        for r in latest:
            if r["leads"]:
                detected = {"lead": r["leads"][0], "round": r}
                leads = await cala.search_entities(r["leads"][0], ["Company", "Organization"], limit=4)
                break
    return {"companies": comps, "leads": leads, "detected": detected}


# ----------------------------------------------------------------- pipeline
async def run(cala: Cala, form: dict, sel: dict, emit: Emit) -> None:
    company = sel.get("company_name") or form["company"]
    lead = sel.get("lead_name") or form.get("lead", "")
    stage = form.get("stage", "Seed")
    sector = form.get("sector", "")
    country = form.get("country", "")
    target = parse_money(form.get("target")) or 0
    committed = parse_money(form.get("committed")) or 0
    open_eur = max(0.0, target - committed)
    exclude = {slug(x) for x in as_list(form.get("existing"))} | {slug(lead)}
    sector_terms = [t for t in re.split(r"[,/;]| and ", sector.lower()) if t.strip()] + [sector.lower()]
    sector_terms = [t.strip() for t in sector_terms if len(t.strip()) > 2]
    since = date(date.today().year - 2, date.today().month, 1).isoformat()

    lead_id = "lead:" + slug(lead)
    await emit({"type": "node", "node": {"id": lead_id, "kind": "lead", "label": lead, "sub": "your lead investor"}})
    await emit({"type": "node", "node": {"id": "round", "kind": "round", "label": f"{company}", "sub": f"{stage} · €{open_eur / 1e6:.1f}M open"}})

    # ---- step 2: graph explorer (lead -> its rounds -> co-investors)
    await emit({"type": "step", "id": "explore", "status": "start", "label": "Graph Explorer",
                "detail": f"Walking {lead}'s past rounds to find who joined them"})
    lead_rounds = await rows(cala, f"funding_rounds.investors={lead}.date>={since}.order_by=date DESC.limit=40"
                                   f".return(company, round_type, date, amount, lead_investor, investors, industry, country)")
    if not lead_rounds and sel.get("lead_id"):
        lead_rounds = await explore_by_relationships(cala, sel["lead_id"], lead)

    cands: dict[str, dict] = {}
    for r in lead_rounds:
        if not r["company"] or same(r["company"], company):
            continue
        did = "deal:" + slug(r["company"])
        await emit({"type": "node", "node": {"id": did, "kind": "deal", "label": r["company"],
                                              "sub": " · ".join(str(x) for x in (r["stage"], r["date"]) if x)}})
        await emit({"type": "edge", "edge": {"from": lead_id, "to": did, "kind": "lead-deal"}})
        for inv in r["investors"]:
            k = slug(inv)
            if not k or k in exclude or same(inv, lead):
                continue
            c = cands.setdefault(k, {"id": "inv:" + k, "name": inv, "shared": [], "origin": "lead-graph"})
            c["shared"].append({"company": r["company"], "stage": r["stage"], "date": r["date"]})
            await emit({"type": "edge", "edge": {"from": did, "to": c["id"], "kind": "deal-inv"}})
    await emit({"type": "step", "id": "explore", "status": "done", "label": "Graph Explorer",
                "detail": f"{len(lead_rounds)} lead rounds, {len(cands)} co-investors"})

    # ---- step 3: sector scout (active investors in similar companies)
    await emit({"type": "step", "id": "sector", "status": "start", "label": "Sector Scout",
                "detail": f"Recent {stage} rounds in {sector or 'your sector'}"})
    sector_rounds = await rows(cala, f"funding_rounds.company.industry={sector}.round_type={stage}.date>={since}"
                                     f".order_by=date DESC.limit=40.return(company, round_type, date, amount, lead_investor, investors, country)") if sector else []
    added = 0
    for r in sector_rounds:
        for inv in r["investors"]:
            k = slug(inv)
            if not k or k in exclude or k in cands or same(inv, lead):
                continue
            cands[k] = {"id": "inv:" + k, "name": inv, "shared": [], "origin": "sector"}
            added += 1
    await emit({"type": "step", "id": "sector", "status": "done", "label": "Sector Scout",
                "detail": f"{len(sector_rounds)} sector rounds, {added} new candidates"})

    ranked = sorted(cands.values(), key=lambda c: -len(c["shared"]))[:MAX_CANDIDATES]
    for c in ranked:
        await emit({"type": "node", "node": {"id": c["id"], "kind": "investor", "label": c["name"],
                                              "sub": f"{len(c['shared'])} shared deals" if c["shared"] else "sector-active"}})

    # ---- competitors (for the conflict flag)
    competitors = as_list(form.get("competitors"))
    if not competitors:
        comp_rows = await rows(cala, f"companies.competitors_of={company}.limit=10.return(name)")
        competitors = [r["company"] for r in comp_rows if r["company"]]
    competitors = [x for x in competitors if not same(x, company)]

    # ---- step 4: profiler
    await emit({"type": "step", "id": "profile", "status": "start", "label": "Profiler",
                "detail": f"Pulling the last 24 months of rounds for {len(ranked)} investors"})

    async def profile(c):
        rs = await rows(cala, f"funding_rounds.investors={c['name']}.date>={since}.order_by=date DESC.limit=30"
                              f".return(company, round_type, date, amount, lead_investor, investors, industry, country)")
        c["rounds"] = [{**r, "lead": (any(same(x, c["name"]) for x in r["leads"]) if r["leads"] else None)} for r in rs]

    await asyncio.gather(*(profile(c) for c in ranked))
    await emit({"type": "step", "id": "profile", "status": "done", "label": "Profiler",
                "detail": f"{sum(len(c['rounds']) for c in ranked)} rounds profiled"})

    # ---- step 5: score + flags (code, not LLM)
    await emit({"type": "step", "id": "score", "status": "start", "label": "Scorer", "detail": "Scoring 6 signals, applying 5 flag rules"})
    ctx = {"stage": stage, "sector_terms": sector_terms, "country": country, "open_eur": open_eur, "competitors": competitors}
    for c in ranked:
        score_candidate(c, ctx)
        c["reason"] = build_reason(c, lead)
        for f in c["flags"]:
            if f["kind"] == "conflict":
                cid = "comp:" + slug(f["company"])
                await emit({"type": "node", "node": {"id": cid, "kind": "competitor", "label": f["company"], "sub": "your competitor"}})
                await emit({"type": "edge", "edge": {"from": c["id"], "to": cid, "kind": "conflict"}})
        if c["status"] == "short":
            await emit({"type": "edge", "edge": {"from": c["id"], "to": "round", "kind": "inv-round"}})
    ranked.sort(key=lambda c: -c["score"])
    shortlist = [c for c in ranked if c["status"] == "short"][:10]
    flagged = [c for c in ranked if c["status"] == "flagged"]
    await emit({"type": "step", "id": "score", "status": "done", "label": "Scorer",
                "detail": f"{len(shortlist)} shortlisted, {len(flagged)} flagged"})

    # ---- step 6: writer (sources + optional LLM wording)
    await emit({"type": "step", "id": "write", "status": "start", "label": "Writer", "detail": "Attaching sources to the top picks"})

    async def evidence(c):
        if not c["shared"]:
            return
        d = c["shared"][0]
        try:
            ans = await cala.search(f"Who invested in {d['company']}'s {d.get('stage') or ''} round?")
        except CalaError:
            return
        srcs = []
        for kb in ans.get("context", []) or []:
            for o in kb.get("origins", []) or []:
                doc = o.get("document") or {}
                if doc.get("url"):
                    srcs.append({"name": doc.get("name") or (o.get("source") or {}).get("name", "source"), "url": doc["url"]})
        c["sources"] = list({s["url"]: s for s in srcs}.values())[:4]

    await asyncio.gather(*(evidence(c) for c in shortlist[:5] + [f for f in flagged if any(x["kind"] == "conflict" for x in f["flags"])]))
    await writer.enrich(shortlist, lead, company, form)
    await emit({"type": "step", "id": "write", "status": "done", "label": "Writer", "detail": "Reasons and intro drafts ready"})

    await emit({"type": "result", "candidates": shortlist + flagged,
                "summary": {"open_eur": open_eur, "scanned": len(ranked), "shortlisted": len(shortlist), "flagged": len(flagged),
                            "lead": lead, "company": company, "competitors": competitors},
                "allocation": allocation(shortlist, open_eur)})


async def explore_by_relationships(cala: Cala, lead_id: str, lead: str) -> list[dict]:
    """Fallback when dot-notation returns nothing: introspect the lead entity, follow any
    funding-like relationship to companies, then ask for each company's rounds."""
    try:
        intro = await cala.introspect(lead_id)
    except CalaError:
        return []
    rels = intro.get("relationships", {}) or {}
    out_rel = [r for r in rels.get("outgoing", []) if any(t in r for t in FUNDING_TERMS)]
    in_rel = [r for r in rels.get("incoming", []) if any(t in r for t in FUNDING_TERMS)]
    if not (out_rel or in_rel):
        return []
    body = {"properties": ["name"], "relationships": {"outgoing": {r: {"limit": 25} for r in out_rel},
                                                       "incoming": {r: {"limit": 25} for r in in_rel}}}
    try:
        ent = await cala.retrieve(lead_id, body, label=f"{lead} relationships")
    except CalaError:
        return []
    names = []
    for direction in ("outgoing", "incoming"):
        for _, items in (ent.get("relationships", {}).get(direction, {}) or {}).items():
            for it in items or []:
                if it.get("entity_type") in ("Company", "Organization"):
                    names.append(it.get("name"))
    out = []
    for name in list(dict.fromkeys(n for n in names if n))[:10]:
        out += await rows(cala, f"funding_rounds.company={name}.order_by=date DESC.limit=3"
                                f".return(company, round_type, date, amount, lead_investor, investors, industry, country)")
    return [r for r in out if any(same(x, lead) for x in r["investors"])]
