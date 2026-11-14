"""Buyer REST API. Health stays public; business endpoints require a user JWT."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .auth import SupabaseVerifier
from .config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    verifier = SupabaseVerifier(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await verifier.close()

    application = FastAPI(title="TTA buyer API", lifespan=lifespan)
    application.state.settings = config
    application.state.verifier = verifier

    @application.get("/health")
    @application.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "service": "api"}

    return application


app = create_app()
