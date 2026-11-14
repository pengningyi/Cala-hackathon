"""Deterministic scoring and flagging. No LLM here: results must be stable between runs.

The same rules are mirrored in static/index.html (function scoreCandidate) for demo mode.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from statistics import median
from typing import Any, Optional

WEIGHTS = {"coinv": 35, "activity": 20, "stage": 15, "sector": 15, "follow": 10, "geo": 5}
INACTIVE_DAYS = 365
LEAD_ONLY_RATIO = 0.8
STAGE_MIN_SHARE = 0.10
MIN_ROUNDS_FOR_RATIO_FLAGS = 3


def parse_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    s = str(v).strip()
    m = re.search(r"(19|20)\d{2}-\d{2}-\d{2}", s)
    if m:
        return date.fromisoformat(m.group(0))
    m = re.search(r"((19|20)\d{2})-(\d{2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(3)), 1)
    for fmt in ("%b %Y", "%B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        return date(int(m.group(0)), 7, 1)
    return None


def parse_money(v: Any) -> Optional[float]:
    """'€2.5M', '$700K', '~1,200,000', 'over 100M' -> float (EUR/USD treated as equal)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).lower().replace(",", "").replace("~", "").replace("over", "").strip()
    mult = 1.0
    for suf, m in (("bn", 1e9), ("b", 1e9), ("m", 1e6), ("k", 1e3)):
        idx = s.find(suf)
        if idx > 0 and (s[idx - 1].isdigit() or s[idx - 1] in ". "):
            mult = m
            s = s[:idx]
            break
    num = "".join(ch for ch in s if ch.isdigit() or ch == ".")
    try:
        return float(num) * mult if num else None
    except ValueError:
        return None


def stage_key(s: Any) -> str:
    s = str(s or "").lower()
    if "pre" in s and "seed" in s:
        return "pre-seed"
    if "seed" in s:
        return "seed"
    for letter in "abcdefg":
        if f"series {letter}" in s or s.strip() == letter:
            return f"series-{letter}"
    return s.strip() or "unknown"


def score_candidate(c: dict, ctx: dict) -> dict:
    """c = profile built by the pipeline; ctx = {stage, sector_terms, country, open_eur, competitors}.
    Returns c with score, signals, flags, status."""
    today = date.today()
    rounds = c.get("rounds", [])
    n = len(rounds)
    signals: dict[str, dict] = {}

    shared = c.get("shared", [])
    recent_shared = sum(1 for d in shared if (parse_date(d.get("date")) or date(1900, 1, 1)) >= date(today.year - 2, today.month, 1))
    coinv = min(1.0, (len(shared) + 0.5 * recent_shared) / 3) if shared else 0.0
    signals["coinv"] = {"v": coinv, "label": f"{len(shared)} shared deal{'s' if len(shared) != 1 else ''} with lead"}

    dated = [parse_date(r.get("date")) for r in rounds]
    dated = [d for d in dated if d]
    r12 = sum(1 for d in dated if (today - d).days <= 365)
    last = max(dated) if dated else None
    c["rounds_12m"] = r12 if dated else None
    c["last_date"] = last.isoformat() if last else None
    signals["activity"] = ({"v": min(1.0, r12 / 4), "label": f"{r12} rounds in 12 months"} if dated
                           else {"v": None, "label": "activity unknown"})

    if n:
        st = sum(1 for r in rounds if stage_key(r.get("stage")) == stage_key(ctx["stage"])) / n
        signals["stage"] = {"v": st, "label": f"{round(st * 100)}% of rounds at {ctx['stage']}"}
        terms = [t.lower() for t in ctx.get("sector_terms", []) if t]
        if terms:
            sec = sum(1 for r in rounds if any(t in str(r.get("sector", "")).lower() for t in terms)) / n
            signals["sector"] = {"v": sec, "label": f"{round(sec * 100)}% in your sector"}
        else:
            signals["sector"] = {"v": None, "label": "sector unknown"}
        known_lead = [r for r in rounds if r.get("lead") is not None]
        if known_lead:
            led = sum(1 for r in known_lead if r["lead"]) / len(known_lead)
            c["lead_ratio"] = led
            signals["follow"] = {"v": 1 - led, "label": f"follows in {round((1 - led) * 100)}% of rounds"}
        else:
            signals["follow"] = {"v": None, "label": "lead/follow unknown"}
        if ctx.get("country"):
            geo = any(ctx["country"].lower() in str(r.get("country", "")).lower() for r in rounds)
            signals["geo"] = {"v": 1.0 if geo else 0.0, "label": f"{'has' if geo else 'no'} deals in {ctx['country']}"}
        tickets = [parse_money(r.get("amount")) / max(1, len(r.get("investors", [])) or 1)
                   for r in rounds if parse_money(r.get("amount"))]
        c["ticket"] = median(tickets) if tickets else None
    else:
        for k in ("stage", "sector", "follow", "geo"):
            signals[k] = {"v": None, "label": "unknown"}
        c["ticket"] = None

    c["signals"] = signals
    c["score"] = round(sum(WEIGHTS[k] * (signals.get(k, {}).get("v") or 0) for k in WEIGHTS))

    # ---------------------------------------------------------------- flags
    flags = []
    comp = {x.lower() for x in ctx.get("competitors", [])}
    hits = [r for r in rounds if str(r.get("company", "")).lower() in comp]
    if hits:
        h = hits[0]
        flags.append({"kind": "conflict", "severity": "red",
                      "reason": f"Backed competitor {h['company']} ({h.get('stage') or 'round'}, {h.get('date') or 'date unknown'})",
                      "company": h["company"], "source": h.get("source")})
    if last and (today - last).days > INACTIVE_DAYS:
        flags.append({"kind": "inactive", "severity": "grey", "reason": f"No round found since {last.isoformat()}"})
    if c.get("lead_ratio") is not None and n >= MIN_ROUNDS_FOR_RATIO_FLAGS and c["lead_ratio"] > LEAD_ONLY_RATIO:
        flags.append({"kind": "lead-only", "severity": "grey", "reason": f"Leads {round(c['lead_ratio'] * 100)}% of its rounds"})
    if signals["stage"]["v"] is not None and n >= MIN_ROUNDS_FOR_RATIO_FLAGS and signals["stage"]["v"] < STAGE_MIN_SHARE:
        flags.append({"kind": "stage", "severity": "grey", "reason": f"Rarely invests at {ctx['stage']}"})
    if c.get("ticket") and ctx.get("open_eur") and c["ticket"] > ctx["open_eur"]:
        flags.append({"kind": "ticket", "severity": "amber",
                      "reason": f"Typical ticket ~€{c['ticket'] / 1e6:.1f}M exceeds the open €{ctx['open_eur'] / 1e6:.1f}M"})
    c["flags"] = flags
    c["status"] = "flagged" if any(f["severity"] in ("red", "grey") for f in flags) else "short"
    return c


def build_reason(c: dict, lead_name: str) -> str:
    shared = c.get("shared", [])
    bits = []
    if shared:
        names = ", ".join(d["company"] for d in shared[:2])
        bits.append(f"Co-invested with {lead_name} in {names}" + (f" and {len(shared) - 2} more" if len(shared) > 2 else ""))
    elif c.get("origin") == "sector":
        bits.append("Active in your sector and stage")
    if c.get("rounds_12m"):
        bits.append(f"{c['rounds_12m']} rounds in the last 12 months")
    if c.get("lead_ratio") is not None:
        bits.append(f"follows in {round((1 - c['lead_ratio']) * 100)}% of deals")
    return "; ".join(bits) or "Limited data in Cala"


def allocation(shortlist: list[dict], open_eur: float) -> list[dict]:
    """Greedy: fill the open amount with typical tickets of the top-ranked investors."""
    out, left = [], open_eur or 0
    for c in shortlist:
        if left <= 0:
            break
        t = c.get("ticket") or 0
        if not t:
            continue
        take = min(t, left)
        out.append({"id": c["id"], "name": c["name"], "eur": take})
        left -= take
    if left > 0:
        out.append({"id": None, "name": "Still open", "eur": left})
    return out
