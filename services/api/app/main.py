"""Phase 0 stub of the buyer backend. P3 builds on this; keep /health. Public routes live under /api."""
from fastapi import FastAPI

app = FastAPI(title="TTA buyer API")


@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "api"}
