from __future__ import annotations

from datetime import UTC, datetime

from slumggol_bot.schemas import EvidenceSource

_FRESH_DAYS = 180
_ACCEPTABLE_DAYS = 730


def _parse_published_at(published_at: str) -> datetime | None:
    value = published_at.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _days_old(published_at: str | None) -> float | None:
    if not published_at:
        return None
    parsed = _parse_published_at(published_at)
    if parsed is None:
        return None
    return (datetime.now(UTC) - parsed).days


def score_source(published_at: str | None) -> float:
    if not published_at:
        return 0.6
    age = _days_old(published_at)
    if age is None:
        return 0.6
    if age <= _FRESH_DAYS:
        return 1.0
    if age <= _ACCEPTABLE_DAYS:
        return 0.7
    return 0.3


def score_evidence(evidence: list[EvidenceSource]) -> float:
    if not evidence:
        return 0.6
    return max(score_source(source.published_at) for source in evidence)


def freshness_caveat(score: float, evidence: list[EvidenceSource]) -> str | None:
    if score >= 0.7:
        return None

    parsed_dates = [
        parsed
        for source in evidence
        if source.published_at
        for parsed in [_parse_published_at(source.published_at)]
        if parsed is not None
    ]
    if not parsed_dates:
        return None

    most_recent = max(parsed_dates)
    return (
        f"Note: the newest source I found is from {most_recent.year}, "
        "so this may be outdated and is worth verifying again."
    )
