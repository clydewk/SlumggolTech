from __future__ import annotations

import uvicorn

from slumggol_bot.api.app import app, get_settings


def run() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
