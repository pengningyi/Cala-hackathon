"""Roundfill API. Run:  uvicorn main:app --reload --port 8000  then open http://localhost:8000"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

from cala import Cala, CalaError  # noqa: E402
import pipeline  # noqa: E402

app = FastAPI(title="Roundfill")
STATIC = Path(__file__).parent / "static"


class Form(BaseModel):
    founder: str = ""
    company: str
    website: str = ""
    sector: str = ""
    country: str = ""
    stage: str = "Seed"
    target: str = ""
    committed: str = ""
    lead: str = ""
    existing: str = ""
    competitors: str = ""


class RunReq(BaseModel):
    form: Form
    company_id: str | None = None
    company_name: str | None = None
    lead_id: str | None = None
    lead_name: str | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "cala": bool(os.getenv("CALA_API_KEY")), "llm": bool(os.getenv("ANTHROPIC_API_KEY"))}


@app.post("/api/resolve")
async def resolve(form: Form):
    calls = []

    async def log(ev):
        calls.append(ev)

    cala = Cala(os.getenv("CALA_API_KEY", ""), on_call=log)
    try:
        out = await pipeline.resolve(cala, form.model_dump())
    except CalaError as e:
        raise HTTPException(502, str(e))
    finally:
        await cala.close()
    return {**out, "calls": calls}


@app.post("/api/run")
async def run(req: RunReq):
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev):
        await queue.put(ev)

    async def worker():
        cala = Cala(os.getenv("CALA_API_KEY", ""), on_call=emit)
        try:
            await pipeline.run(cala, req.form.model_dump(), req.model_dump(exclude={"form"}), emit)
        except Exception as e:  # surface any failure to the UI
            await emit({"type": "error", "message": str(e)})
        finally:
            await cala.close()
            await queue.put(None)

    async def stream():
        task = asyncio.create_task(worker())
        while True:
            ev = await queue.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev, default=str)}\n\n"
        await task

    return StreamingResponse(stream(), media_type="text/event-stream")
