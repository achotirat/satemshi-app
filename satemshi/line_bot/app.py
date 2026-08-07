"""FastAPI application exposing the LINE webhook."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from ..config import Config, load_config
from .client import LineClient, MessagingClient
from .handlers import CaptureBot
from .signature import verify

logger = logging.getLogger(__name__)


def create_app(
    config: Config | None = None, client: MessagingClient | None = None
) -> FastAPI:
    config = config or load_config()
    owns_client = client is None
    client = client or LineClient(config.line_channel_access_token)
    bot = CaptureBot(config, client)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not config.line_channel_secret:
            logger.warning(
                "LINE_CHANNEL_SECRET is empty — every webhook call will be "
                "rejected until it is set."
            )
        # Say whether the vault is being committed at boot, rather than
        # leaving it to be discovered a window after the first capture.
        await bot.git.preflight()
        yield
        # Shutdown closes an open coalescing window: commit what is in it
        # instead of waiting for a timer that will not fire again.
        await bot.git.aclose()
        if owns_client and hasattr(client, "aclose"):
            await client.aclose()

    app = FastAPI(title="Satemshi capture", lifespan=lifespan)
    app.state.config = config
    app.state.bot = bot

    @app.get("/healthz")
    async def healthz() -> dict:
        return {
            "status": "ok",
            "vault_present": config.vault_path.is_dir(),
            "webhook_path": config.line_bot.webhook_path,
        }

    @app.post(config.line_bot.webhook_path)
    async def webhook(
        request: Request, x_line_signature: str | None = Header(default=None)
    ) -> Response:
        body = await request.body()
        if not verify(config.line_channel_secret, body, x_line_signature):
            logger.warning("Rejected a webhook call with a bad signature")
            return JSONResponse({"detail": "bad signature"}, status_code=401)

        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return JSONResponse({"detail": "bad payload"}, status_code=400)

        for event in payload.get("events") or []:
            try:
                await bot.handle_event(event)
            except Exception:  # one bad event must not stall the rest
                logger.exception("Failed to handle LINE event %s", event.get("type"))

        # LINE retries anything that is not a 200, so acknowledge once the
        # events have been processed.
        return JSONResponse({"ok": True})

    return app
