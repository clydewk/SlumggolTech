from __future__ import annotations

from datetime import UTC, datetime, timedelta

from slumggol_bot.schemas import EvidenceSource
from slumggol_bot.services.freshness import freshness_caveat, score_evidence, score_source


def test_score_source_handles_fresh_old_and_unknown_dates() -> None:
    fresh = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
    acceptable = (datetime.now(UTC) - timedelta(days=365)).date().isoformat()
    stale = (datetime.now(UTC) - timedelta(days=1200)).date().isoformat()

    assert score_source(fresh) == 1.0
    assert score_source(acceptable) == 0.7
    assert score_source(stale) == 0.3
    assert score_source(None) == 0.6


def test_score_evidence_uses_best_available_source() -> None:
    fresh = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
    evidence = [
        EvidenceSource(
            title="Archive",
            url="https://example.com/archive",
            domain="example.com",
            published_at="2020-01-01",
        ),
        EvidenceSource(
            title="Recent",
            url="https://example.com/recent",
            domain="example.com",
            published_at=fresh,
        ),
    ]

    assert score_evidence(evidence) == 1.0


def test_freshness_caveat_uses_latest_known_year() -> None:
    evidence = [
        EvidenceSource(
            title="Archive",
            url="https://example.com/archive",
            domain="example.com",
            published_at="2020-01-01",
        ),
        EvidenceSource(
            title="Unknown",
            url="https://example.com/unknown",
            domain="example.com",
        ),
    ]

    assert freshness_caveat(0.3, evidence) == (
        "Note: the newest source I found is from 2020, "
        "so this may be outdated and is worth verifying again."
    )
