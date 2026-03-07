from __future__ import annotations

from slumggol_bot.workers.settings import _build_cron_jobs


def test_outbreak_refresh_cron_jobs_are_configured() -> None:
    cron_jobs = _build_cron_jobs()

    assert cron_jobs
