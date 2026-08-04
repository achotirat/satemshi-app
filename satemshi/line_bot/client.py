"""Minimal LINE Messaging API client.

Only the three calls the capture flow needs: reply to an event, push a
message outside a reply window, and download the binary content of an
image message.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.line.me/v2/bot"
DATA_API_BASE = "https://api-data.line.me/v2/bot"

# LINE truncates a text message at 5000 characters.
MAX_TEXT_LENGTH = 5000


class MessagingClient(Protocol):
    """The surface the handlers depend on (so tests can substitute it)."""

    async def reply(self, reply_token: str, text: str) -> None: ...

    async def push(self, to: str, text: str) -> None: ...

    async def get_content(self, message_id: str) -> tuple[bytes, str]: ...


def _messages(text: str) -> list[dict[str, str]]:
    text = text.strip() or "(empty)"
    if len(text) > MAX_TEXT_LENGTH:
        text = text[: MAX_TEXT_LENGTH - 1] + "…"
    return [{"type": "text", "text": text}]


class LineClient:
    def __init__(self, access_token: str, timeout: float = 10.0) -> None:
        self._access_token = access_token
        self._client = httpx.AsyncClient(timeout=timeout)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def reply(self, reply_token: str, text: str) -> None:
        await self._post(
            f"{API_BASE}/message/reply",
            {"replyToken": reply_token, "messages": _messages(text)},
        )

    async def push(self, to: str, text: str) -> None:
        await self._post(
            f"{API_BASE}/message/push", {"to": to, "messages": _messages(text)}
        )

    async def get_content(self, message_id: str) -> tuple[bytes, str]:
        response = await self._client.get(
            f"{DATA_API_BASE}/message/{message_id}/content", headers=self._headers
        )
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "")

    async def _post(self, url: str, payload: dict) -> None:
        response = await self._client.post(url, headers=self._headers, json=payload)
        if response.status_code >= 400:
            # A failed reply must not lose the capture that was already
            # written to the vault, so log and carry on.
            logger.warning(
                "LINE API %s returned %s: %s",
                url,
                response.status_code,
                response.text[:500],
            )
