"""Run once with your key to see the raw shapes Cala returns for the queries the pipeline uses.

    cd backend && python ../scripts/probe.py "Cala AI" "Some Lead VC"

If a row uses key names that pipeline.norm_round does not know, add them to the lists there.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")
from cala import Cala  # noqa: E402


async def main(company: str, lead: str | None):
    async def log(ev):
        print(f"  · {ev['endpoint']}  {ev['ms']}ms  n={ev['n']}  {ev.get('error') or ''}")

    cala = Cala(os.environ["CALA_API_KEY"], on_call=log, use_cache=False)
    try:
        print("\n== entity_search company"); ents = await cala.search_entities(company, ["Company", "Organization"])
        print(json.dumps(ents[:5], indent=2))
        q = (f"funding_rounds.company={company}.order_by=date DESC.limit=3"
             ".return(company, round_type, date, amount, lead_investor, investors)")
        print("\n== knowledge_query latest rounds"); print(json.dumps(await cala.query(q), indent=2)[:3000])
        if lead:
            q = (f"funding_rounds.investors={lead}.order_by=date DESC.limit=10"
                 ".return(company, round_type, date, amount, lead_investor, investors, industry, country)")
            print("\n== knowledge_query lead rounds"); print(json.dumps(await cala.query(q), indent=2)[:4000])
            le = await cala.search_entities(lead, ["Company", "Organization"])
            if le:
                print("\n== introspection lead"); print(json.dumps(await cala.introspect(le[0]["id"]), indent=2)[:3000])
        print("\n== knowledge_search"); ans = await cala.search(f"Who invested in {company}?")
        print(ans.get("content", "")[:1500])
    finally:
        await cala.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Cala AI", sys.argv[2] if len(sys.argv) > 2 else None))
