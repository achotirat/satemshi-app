"""LINE webhook signature verification.

LINE signs the *raw* request body with the channel secret (HMAC-SHA256,
base64). The body must be verified before it is parsed — re-serialised
JSON will not match.
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header:
        return False
    return hmac.compare_digest(sign(secret, body), header)
