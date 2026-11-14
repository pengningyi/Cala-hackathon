"""Writer agent. Turns computed facts into a one-line reason and an intro request to the lead.

If ANTHROPIC_API_KEY is set, an LLM rewrites the wording from the facts JSON only
(it is told not to add facts). Otherwise a template is used, so the app works without an LLM.
"""
from __future__ import annotations

import json
import os

import httpx

MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")


def template_intro(c: dict, lead: str, company: str, form: dict) -> str:
    founder = form.get("founder") or "the founder"
    deals = ", ".join(d["company"] for d in c.get("shared", [])[:2])
    why = f"You co-invested with them in {deals}, so a note from you would carry weight." if deals else \
          "They have been active in our space recently."
    return (f"Hi — as we fill the rest of {company}'s {form.get('stage', 'seed')} round, could you introduce me to "
            f"{c['name']}? {why} Happy to send a forwardable blurb. Thanks, {founder}")


async def enrich(shortlist: list[dict], lead: str, company: str, form: dict) -> None:
    for c in shortlist:
        c["intro"] = template_intro(c, lead, company, form)
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or not shortlist:
        return
    facts = [{"id": c["id"], "name": c["name"], "shared_deals_with_lead": c.get("shared", [])[:3],
              "rounds_last_12m": c.get("rounds_12m"), "lead_ratio": c.get("lead_ratio"),
              "score": c["score"]} for c in shortlist]
    prompt = (
        f"Founder {form.get('founder') or ''} of {company} has {lead} as lead investor for a {form.get('stage')} round. "
        "For each investor below write (a) `reason`: one sentence, max 22 words, why they fit, and (b) `intro`: a 2-3 sentence "
        f"note the founder sends to {lead} asking for an introduction. Use ONLY the facts given; never invent numbers, "
        "deals or names. Return JSON: [{\"id\":..., \"reason\":..., \"intro\":...}].\n\n" + json.dumps(facts, default=str))
    try:
        async with httpx.AsyncClient(timeout=60) as cl:
            r = await cl.post("https://api.anthropic.com/v1/messages",
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                              json={"model": MODEL, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]})
        text = r.json()["content"][0]["text"]
        data = json.loads(text[text.find("["): text.rfind("]") + 1])
        by_id = {d["id"]: d for d in data}
        for c in shortlist:
            if c["id"] in by_id:
                c["reason"] = by_id[c["id"]].get("reason") or c["reason"]
                c["intro"] = by_id[c["id"]].get("intro") or c["intro"]
    except Exception:
        pass  # keep template wording
