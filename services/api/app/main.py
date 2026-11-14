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


def create_app(settings: Settings | None = None, *, store=None, gateway=None, llm=None) -> FastAPI:
    config = settings or Settings.from_env()
    verifier = SupabaseVerifier(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned_store = None
        async with httpx.AsyncClient(timeout=30) as client:
            if app.state.store is None and config.database_url:
                owned_store = PostgresStore(config.database_url)
                await owned_store.open()
                app.state.store = owned_store
            app.state.gateway = gateway or Gateway(config)
            app.state.llm = llm or OpenRouter(config, client)
            app.state.pipeline = Pipeline(app.state.store, app.state.gateway, app.state.llm)
            try:
                yield
            finally:
                await app.state.tasks.close()
                await verifier.close()
                if owned_store:
                    await owned_store.close()

    application = FastAPI(title="TTA buyer API", lifespan=lifespan)
    application.state.settings = config
    application.state.verifier = verifier
    application.state.store = store
    application.state.tasks = Tasks()
    application.include_router(router)

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
