"""Small async OpenRouter client: query embeddings and JSON-schema chat with a fallback model."""
import json
import os

import httpx

URL = "https://openrouter.ai/api/v1"


class OpenRouter:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=30)
        self.headers = {"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"}

    async def embed(self, text: str) -> str | None:
        """Embedding as a pgvector literal '[...]', or None if the API fails (search falls back to full-text)."""
        try:
            r = await self.client.post(f"{URL}/embeddings", headers=self.headers, timeout=10,
                                       json={"model": os.environ.get("EMBED_MODEL", "baai/bge-m3"), "input": [text]})
            r.raise_for_status()
            return "[" + ",".join(f"{x:.6f}" for x in r.json()["data"][0]["embedding"]) + "]"
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return None

    async def chat_json(self, prompt: str, schema: dict, model_env: str = "LLM_MODEL") -> dict:
        """Ask for JSON matching schema. Tries the model, then LLM_MODEL_FALLBACK. Raises RuntimeError if both fail."""
        models = [m for m in (os.environ.get(model_env) or os.environ.get("LLM_MODEL"),
                              os.environ.get("LLM_MODEL_FALLBACK")) if m]
        errors = []
        for model in models:
            try:
                r = await self.client.post(f"{URL}/chat/completions", headers=self.headers, timeout=30, json={
                    "model": model, "max_tokens": 4000, "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                    "reasoning": {"effort": "low", "exclude": True},
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "out", "strict": True, "schema": schema}},
                    "provider": {"sort": "price", "require_parameters": True}})
                r.raise_for_status()
                return json.loads(r.json()["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                errors.append(f"{model}: {type(e).__name__}")
        raise RuntimeError("; ".join(errors) or "no LLM_MODEL configured")
