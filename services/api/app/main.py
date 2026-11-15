"""Buyer REST API. Health stays public; business endpoints require a user JWT."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout

from .auth import SupabaseVerifier
from .config import Settings
from .gateway import Gateway, GatewayDenied, GatewayUnavailable
from .llm import OpenRouter
from .pipeline import Pipeline, Tasks
from .routes import router
from .store import Conflict, PostgresStore
from .telemetry import create_provider


def create_app(settings: Settings | None = None, *, store=None, gateway=None, llm=None, trace_provider=None) -> FastAPI:
    config = settings or Settings.from_env()
    verifier = SupabaseVerifier(config)
    provider = trace_provider or create_provider()
    tracer = provider.get_tracer("tta.api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned_store = None
        async with httpx.AsyncClient(timeout=30) as client:
            if app.state.store is None and config.database_url:
                owned_store = PostgresStore(config.database_url)
                await owned_store.open()
                app.state.store = owned_store
            app.state.gateway = gateway or Gateway(config)
            app.state.llm = llm or OpenRouter(config, client, tracer_instance=tracer)
            app.state.pipeline = Pipeline(
                app.state.store, app.state.gateway, app.state.llm, tracer_instance=tracer, today=config.today
            )
            try:
                yield
            finally:
                await app.state.tasks.close()
                await verifier.close()
                if owned_store:
                    await owned_store.close()
                provider.shutdown()

    application = FastAPI(title="TTA buyer API", lifespan=lifespan)
    application.state.settings = config
    application.state.verifier = verifier
    application.state.store = store
    application.state.tasks = Tasks()
    application.include_router(router)

    @application.middleware("http")
    async def trace_request(request, call_next):
        if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
            return await call_next(request)
        with tracer.start_as_current_span("request.api", record_exception=False):
            return await call_next(request)

    @application.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(GatewayDenied)
    async def denied_handler(request, exc):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Gateway denied the operation",
                "reason": exc.reason.value if exc.reason else None,
            },
        )

    @application.exception_handler(GatewayUnavailable)
    async def gateway_handler(request, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    async def database_handler(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Buyer database unavailable"})

    application.add_exception_handler(DatabaseError, database_handler)
    application.add_exception_handler(PoolTimeout, database_handler)

    @application.get("/health")
    @application.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "service": "api"}

    return application


app = create_app()
