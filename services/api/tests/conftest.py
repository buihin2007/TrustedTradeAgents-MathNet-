import asyncio
import sys

import pytest
from fastapi.testclient import TestClient

from app.auth import Identity, current_user
from app.config import Settings
from app.main import create_app
from tests.factories import USER_ID, FakeGateway, FakeLLM, MemoryStore

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def api():
    store = MemoryStore()
    gateway = FakeGateway()
    app = create_app(Settings(), store=store, gateway=gateway, llm=FakeLLM())
    app.dependency_overrides[current_user] = lambda: Identity(str(USER_ID), "buyer-jwt")
    with TestClient(app) as client:
        yield client, app, store, gateway
