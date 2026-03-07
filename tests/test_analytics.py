from __future__ import annotations

import pytest

from slumggol_bot.schemas import AnalyticsEvent
from slumggol_bot.services.analytics import FailOpenAnalyticsSink


class ExplodingSink:
    async def write(self, events):  # noqa: ANN001
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_fail_open_analytics_sink_swallows_errors() -> None:
    sink = FailOpenAnalyticsSink(ExplodingSink())
    await sink.write([AnalyticsEvent(table="usage_events", payload={"event_id": "1"})])
