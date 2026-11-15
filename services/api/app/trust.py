"""Verify Valio Aimo's quote signatures like any external agent would: public JWKS + Ed25519."""

import base64
import os
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from tta_contracts import PriceAvailabilityResult, quote_signing_bytes


def jwks_url(mcp_url: str) -> str:
    parts = urlsplit(mcp_url)
    return urlunsplit((parts.scheme, parts.netloc, "/.well-known/jwks.json", "", ""))


def _bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class QuoteVerifier:
    def __init__(self, mcp_url: str):
        self.url = jwks_url(mcp_url)
        self.keys: dict[str, Ed25519PublicKey] = {}
        self.fetched = 0.0

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(self.url)
            response.raise_for_status()
            keys = response.json().get("keys", [])
        self.keys = {
            key["kid"]: Ed25519PublicKey.from_public_bytes(_bytes(key["x"]))
            for key in keys
            if key.get("kty") == "OKP" and key.get("crv") == "Ed25519" and key.get("kid") and key.get("x")
        }
        self.fetched = monotonic()

    async def check(self, quote: PriceAvailabilityResult) -> tuple[bool, str]:
        """(trusted, sentence for the buyer)."""
        if not quote.signature or not quote.key_id:
            return False, "The quote is not signed"
        try:
            if quote.key_id not in self.keys or monotonic() - self.fetched > 300:
                await self._refresh()
            key = self.keys.get(quote.key_id)
            if key is None:
                return False, "The quote's signing key is not published by Valio Aimo"
            key.verify(_bytes(quote.signature), quote_signing_bytes(quote))
        except InvalidSignature:
            return False, "The quote signature does not match: prices or stock were altered"
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return False, "Could not fetch Valio Aimo's public key to verify the quote"
        until = quote.valid_until
        if until and until < datetime.now(UTC):
            return False, "Signed by Valio Aimo, but the quote has expired"
        zone = ZoneInfo(os.getenv("BUYER_TIMEZONE") or "Europe/Helsinki")
        when = f", valid until {until.astimezone(zone).strftime('%H:%M')}" if until else ""
        return True, f"Prices and stock signed by Valio Aimo{when}"
