"""Environment configuration. Missing credentials never disable authentication."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    supabase_url: str = ""
    supabase_key: str = field(default="", repr=False)
    database_url: str = field(default="", repr=False)
    mcp_url: str = "http://nginx/mcp"
    agent_key: str = field(default="", repr=False)
    openrouter_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    llm_fast_model: str = ""          # optional faster model for the agent; defaults to llm_model
    llm_fallback_model: str = "anthropic/claude-haiku-5.5"
    jev_model: str = "~typesafe/jev-latest"
    buyer_timezone: str = "Europe/Helsinki"

    def today(self):
        return datetime.now(ZoneInfo(self.buyer_timezone)).date()

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
            llm_fast_model=os.getenv("LLM_FAST_MODEL", ""),
            llm_fallback_model=os.getenv("LLM_FALLBACK_MODEL", "anthropic/claude-haiku-5.5"),
            jev_model=os.getenv("JEV_MODEL") or "~typesafe/jev-latest",
            buyer_timezone=os.getenv("BUYER_TIMEZONE") or "Europe/Helsinki",
        )
