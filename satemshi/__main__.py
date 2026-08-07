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
    path, applied, shadowed = apply_env_file()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    # Say something in every case. "Found nothing" and "found everything"
    # looked identical before, which turned a missing file into a hunt.
    if applied:
        log.info(
            "Loaded %d setting(s) from %s: %s", len(applied), path, ", ".join(applied)
        )
    elif not path.is_file():
        log.warning(
            "No .env at %s — copy .env.example there and fill it in, "
            "or set the variables in the environment.",
            path,
        )
    else:
        log.warning("%s has no values filled in.", path)

    if shadowed:
        log.warning(
            "Ignored %s from %s: already set in the environment. Unset them "
            "or use a fresh shell if the file is the one you meant to edit.",
            ", ".join(shadowed),
            path,
        )
    config = load_config()
    uvicorn.run(
        create_app(config),
        host=os.environ.get("APP_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_BIND_PORT", "8765")),
    )


if __name__ == "__main__":
    main()
