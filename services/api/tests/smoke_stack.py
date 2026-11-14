"""Manual smoke check for the isolated Compose stack, without buyer credentials."""

import argparse
import asyncio

import httpx
from fastmcp import Client


async def main(base_url):
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as http:
        health = await http.get("/api/health")
        assert health.status_code == 200, f"API health returned {health.status_code}"
        assert health.json() == {"status": "ok", "service": "api"}
        denied = await http.post("/api/requests", json={"text": "Connectivity check"})
        assert denied.status_code == 401, f"Unauthenticated request returned {denied.status_code}"
    async with Client(f"{base_url}/mcp", timeout=15) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        assert names, "MCP tool discovery returned no tools"
        if "ping" in names:
            response = await client.call_tool("ping", {})
            assert response.data == "pong"
    print("Compose smoke passed: nginx -> API health 200, unauthenticated request 401, MCP discovery/ping OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    asyncio.run(main(parser.parse_args().base_url.rstrip("/")))
