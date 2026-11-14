"""Phase 0 stub of the Valio Aimo gateway. P2 replaces the tools; keep /health and the run() call."""
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP("Valio Aimo gateway")


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp"})


@mcp.tool
def ping() -> str:
    """Connectivity check. Remove once real tools exist."""
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=9000, path="/mcp")
