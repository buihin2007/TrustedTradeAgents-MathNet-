"""Valio Aimo's quote signing key (Ed25519) and its public JWKS.

GATEWAY_SIGNING_KEY: base64url of the 32-byte Ed25519 private key. Without it a key is generated at
start-up, which is fine for a demo: quotes stay verifiable for as long as the gateway runs.
"""

import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

logger = logging.getLogger(__name__)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Signer:
    def __init__(self, seed: str | None = None):
        if seed:
            padded = seed + "=" * (-len(seed) % 4)
            self.key = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(padded))
        else:
            self.key = Ed25519PrivateKey.generate()
            logger.warning("GATEWAY_SIGNING_KEY not set: using a key generated for this run")
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.x = b64url(public)
        self.kid = "valio-aimo-" + hashlib.sha256(public).hexdigest()[:12]

    def sign(self, data: bytes) -> str:
        return b64url(self.key.sign(data))

    def jwks(self) -> dict:
        return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": self.x, "kid": self.kid, "alg": "EdDSA", "use": "sig"}]}


signer = Signer(os.getenv("GATEWAY_SIGNING_KEY"))
