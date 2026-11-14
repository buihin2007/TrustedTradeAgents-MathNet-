"""Local Windows/Linux entrypoint; Docker uses uvicorn directly on Linux."""

import asyncio
import sys

import uvicorn

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
