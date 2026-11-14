"""Environment configuration. Missing credentials never disable authentication."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    supabase_url: str = ""
    supabase_key: str = field(default="", repr=False)
    database_url: str = field(default="", repr=False)
    mcp_url: str = "http://nginx/mcp"
    agent_key: str = field(default="", repr=False)
    openrouter_api_key: str = field(default="", repr=False)
    llm_model: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
            supabase_key=os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", ""),
            database_url=os.getenv("DATABASE_URL", ""),
            mcp_url=os.getenv("MCP_URL") or "http://nginx/mcp",
            agent_key=os.getenv("AGENT_KEY", ""),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", ""),
        )
