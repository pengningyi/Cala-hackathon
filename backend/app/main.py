from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException

from .cala import CalaError, CalaMCPProvider
from .cala_native import CalaNativeProvider
from .config import Settings
from .chat_model import ChatModelError, LiveResultChat
from .models import (
    ChatRequest, ChatResponse, CompanyConfirmation, CompanyMatchRequest, CompanyMatches,
    ConfirmCompanyMatches, Resolution, ResolveRequest, RunResult, RunStatus, StartRunRequest,
)
from .pipeline import Workflow, WorkflowError
from .runtime import BudgetExceeded
from .semantic import CodexSemantic, OpenAICompatibleSemantic


async def _connected_chat_reply(service, result, payload, http_request):
    """Stop model work promptly when its browser request disappears."""
    async def disconnected():
        while not await http_request.is_disconnected():
            await asyncio.sleep(.1)

    generation = asyncio.create_task(service.reply(result, payload))
    connection = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((generation, connection), return_when=asyncio.FIRST_COMPLETED)
        if generation in done:
            return generation.result()
        raise WorkflowError("chat_client_disconnected", "Chat request was cancelled.", 499)
    finally:
        for task in (generation, connection):
            if not task.done():
                task.cancel()
        await asyncio.gather(generation, connection, return_exceptions=True)


def create_app(settings: Settings | None = None, provider=None, semantic=None, chat=None):
    settings = settings or Settings()
    if provider is None:
        common = dict(endpoint=settings.cala_mcp_url, token=settings.cala_mcp_token,
                      transport=settings.cala_transport, timeout_seconds=settings.cala_request_timeout_seconds,
                      api_key=settings.cala_api_key)
        provider = (CalaMCPProvider(mapping_path=settings.cala_tool_mapping, **common)
                    if settings.cala_tool_mapping else CalaNativeProvider(**common))
    if semantic is None and settings.model_provider == "codex":
        semantic = CodexSemantic(executable=settings.codex_executable,
                                 timeout_seconds=settings.model_timeout_seconds, enabled=True)
    elif semantic is None and settings.model_provider == "openai_compatible":
        semantic = OpenAICompatibleSemantic(base_url=settings.model_base_url, api_key=settings.model_api_key,
                                            model=settings.model_name, timeout_seconds=settings.model_timeout_seconds)
    workflow = Workflow(settings, provider, semantic)
    chat_service = chat if chat is not None else LiveResultChat(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await workflow.close()

    app = FastAPI(title="RoundCloser API", version="0.1.0", lifespan=lifespan,
                  description="Resolve a Cala lead, confirm the founder profile, and start a bounded co-investor search.")
    app.state.workflow = workflow
    app.state.chat = chat_service
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Last-Event-ID"])

    def error_response(code, message, status, details=None):
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}})

    @app.exception_handler(WorkflowError)
    async def workflow_error(request, exc):
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(CalaError)
    async def cala_error(request, exc):
        return error_response(exc.code, exc.message, 503 if exc.transient or exc.code == "cala_not_configured" else 502)

    @app.exception_handler(ChatModelError)
    async def chat_model_error(request, exc):
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(BudgetExceeded)
    async def budget_error(request, exc):
        return error_response("resolver_timeout", "Entity resolution exceeded its time or call budget; retry or refine the query", 504)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not reflect the request body or internal exception objects into errors.
        details = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return error_response("validation_error", "Request validation failed", 422, details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response("http_error", str(exc.detail), exc.status_code)

    @app.get("/api/health")
    async def health():
        return {"status": "healthy", "cala_configured": bool(getattr(provider, "configured", False)),
                "model_provider": settings.model_provider, "storage": "in_memory", "api_version": "0.1.0"}

    @app.get("/api/integrations/cala/tools")
    async def cala_tools():
        """Read-only tool discovery. Does not start or count as a confirmed run."""
        if not hasattr(provider, "discover_tools"):
            raise WorkflowError("discovery_unavailable", "Provider does not support tool discovery", 503)
        try:
            async with asyncio.timeout(settings.cala_request_timeout_seconds):
                return {"tools": await provider.discover_tools()}
        except TimeoutError:
            raise WorkflowError("discovery_timeout", "Cala tool discovery timed out", 504) from None

    @app.post("/api/resolve", response_model=Resolution)
    async def resolve(request: ResolveRequest):
        return await workflow.resolve(request)

    @app.post("/api/onboarding/matches", response_model=CompanyMatches)
    async def company_matches(request: CompanyMatchRequest):
        return await workflow.match_companies(request)

    @app.post("/api/onboarding/matches/{match_id}/confirm", response_model=CompanyConfirmation)
    async def confirm_company_matches(match_id: str, request: ConfirmCompanyMatches):
        return workflow.confirm_companies(match_id, request)

    @app.post("/api/runs", response_model=RunStatus, status_code=202)
    async def start_run(request: StartRunRequest):
        return workflow.start(request)

    @app.get("/api/runs/{run_id}", response_model=RunStatus)
    async def run_status(run_id: str):
        return workflow.get(run_id).status()

    @app.get("/api/runs/{run_id}/result", response_model=RunResult,
             responses={202: {"model": RunStatus, "description": "Processing is still running"}})
    async def run_result(run_id: str):
        run = workflow.get(run_id)
        if run.result is None:
            return JSONResponse(status_code=202, content=jsonable_encoder(run.status()), headers={"Retry-After": "1"})
        return run.result

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, last_event_id: str | None = Header(default=None)):
        run = workflow.get(run_id)
        try:
            cursor = int(last_event_id or "0")
            if cursor < 0 or cursor > len(run.trace.events):
                raise ValueError()
        except ValueError:
            raise WorkflowError("invalid_event_cursor", "Last-Event-ID must identify an existing event or zero") from None

        async def stream():
            nonlocal cursor
            while True:
                run.trace.changed.clear()
                for event in run.trace.events[cursor:]:
                    yield f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
                    cursor = event.id
                if run.result is not None or await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(run.trace.changed.wait(), timeout=10)
                except TimeoutError:
                    yield ": keepalive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{run_id}/chat", response_model=ChatResponse)
    async def result_chat(run_id: str, request: ChatRequest, http_request: Request):
        run = workflow.get(run_id)
        if run.result is None:
            raise WorkflowError("run_not_ready", "Wait for the run result before asking about its shortlist", 409)
        if request.selected_investor_id is not None and request.selected_investor_id not in {
            card.investor.id for card in run.result.candidates
        }:
            raise WorkflowError("invalid_chat_selection", "Select an investor from this run's shortlist")
        return await _connected_chat_reply(chat_service, run.result, request, http_request)

    return app


app = create_app()
