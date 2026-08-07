"""Run the capture server: ``python -m satemshi``."""

from __future__ import annotations

import logging
import os

import uvicorn

from .config import apply_env_file, load_config
from .line_bot import create_app


def main() -> None:
    # Before anything reads the environment: .env is where the setup
    # instructions put the vault path and the LINE credentials.
    applied = apply_env_file()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if applied:
        logging.getLogger(__name__).info(
            "Loaded %d setting(s) from .env: %s", len(applied), ", ".join(applied)
        )
    config = load_config()
    uvicorn.run(
        create_app(config),
        host=os.environ.get("APP_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_BIND_PORT", "8765")),
    )


if __name__ == "__main__":
    main()
