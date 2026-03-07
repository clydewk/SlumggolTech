from __future__ import annotations

import logging

import uvicorn

from slumggol_bot.api.app import app, get_settings


def run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(app, host=settings.host, port=settings.port)
