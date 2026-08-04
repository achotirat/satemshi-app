"""Run the capture server: ``python -m satemshi``."""

from __future__ import annotations

import logging
import os

import uvicorn

from .config import load_config
from .line_bot import create_app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    uvicorn.run(
        create_app(config),
        host=os.environ.get("APP_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_BIND_PORT", "8765")),
    )


if __name__ == "__main__":
    main()
