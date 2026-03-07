from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisMode(StrEnum):
    GATED = "gated"
    ALL_MESSAGES_LLM = "all_messages_llm"


class ContentKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class FingerprintMatchType(StrEnum):
    EXACT = "exact"
    SIMHASH = "simhash"


class Verdict(StrEnum):
    FALSE = "false"
    MISLEADING = "misleading"
    UNSUPPORTED = "unsupported"
    UNCLEAR = "unclear"
    NON_FACTUAL = "non_factual"


class ClaimCategory(StrEnum):
    SCAM = "scam"
    PUBLIC_HEALTH = "public_health"
    PUBLIC_SAFETY = "public_safety"
    CIVIC = "civic"
    OTHER = "other"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Actionability(StrEnum):
    MONITOR = "monitor"
    COUNTERMESSAGE_READY = "countermessage_ready"
    URGENT_REVIEW = "urgent_review"


class GroupStyleProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dominant_languages: list[str] = Field(default_factory=list)
    emoji_density: float = 0.0
    average_length: float = 0.0
    punctuation_bias: list[str] = Field(default_factory=list)
    discourse_particles: list[str] = Field(default_factory=list)
    message_count: int = 0


class NormalizedMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    occurred_at: datetime
    group_id: str
    group_display_name: str | None = None
    message_id: str
    transport_message_id: int | None = None
    sender_id: str
    content_kind: ContentKind
    command_name: str | None = None
    command_arg_text: str = ""
    text: str = ""
    quoted_text: str = ""
    caption: str = ""
    forwarded: bool = False
    forwarded_many_times: bool = False
    media_url: str | None = None
    media_mimetype: str | None = None
    media_duration_seconds: float | None = None
    detected_languages: list[str] = Field(default_factory=list)
    text_sha256: str | None = None
    text_simhash: str | None = None
    media_sha256: str | None = None
    image_phash: str | None = None
    transcript_sha256: str | None = None
    transcript_text: str | None = None

    def available_hashes(self) -> list[str]:
        return [
            hash_value
            for hash_value in [
                self.text_sha256,
                self.media_sha256,
                self.image_phash,
                self.transcript_sha256,
            ]
            if hash_value
        ]

    @property
    def primary_text(self) -> str:
        parts = [self.text, self.caption, self.transcript_text or ""]
        return "\n".join(part for part in parts if part)

    def command_target_text(self) -> str:
        parts = [
            self.command_arg_text,
            self.quoted_text,
            self.caption,
            self.transcript_text or "",
        ]
        return "\n".join(
            part
            for part in parts
            if part
        ).strip()


class HashObservation(BaseModel):
    hash_key: str
    cross_group_count: int = 0
    same_group_count: int = 0
    match_type: FingerprintMatchType = FingerprintMatchType.EXACT
    distance: int | None = None


class CandidateDecision(BaseModel):
    candidate: bool
    reason_codes: list[str] = Field(default_factory=list)
    hash_observations: list[HashObservation] = Field(default_factory=list)
    match_type: FingerprintMatchType | None = None
    match_distance: int | None = None


class EvidenceSource(BaseModel):
    title: str
    url: str
    domain: str
    published_at: str | None = None


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    web_search_calls: int = 0
    estimated_cost_usd: float = 0.0
    transcription_cost_usd: float = 0.0


class FactCheckResult(BaseModel):
    needs_reply: bool
    verdict: Verdict
    confidence: float
    canonical_claim_en: str
    canonical_text_simhash: str | None = None
    reply_language: str
    reply_text: str
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceSource] = Field(default_factory=list)
    claim_category: ClaimCategory = ClaimCategory.OTHER
    risk_level: RiskLevel = RiskLevel.LOW
    actionability: Actionability = Actionability.MONITOR
    has_official_sg_source: bool = False
    official_source_domain_count: int = 0
    usage: ModelUsage = Field(default_factory=ModelUsage)
    cache_hit: bool = False
    cache_match_type: str | None = None
    cache_match_distance: int | None = None
    claim_key: str | None = None


class HotClaim(BaseModel):
    hash_key: str
    claim_key: str | None = None
    text_simhash: str | None = None
    reason: str
    score: float


class HotClaimMatch(BaseModel):
    claim_key: str
    match_type: FingerprintMatchType
    distance: int = 0


class AnalyticsEvent(BaseModel):
    table: Literal[
        "message_events",
        "claim_events",
        "factcheck_events",
        "reply_events",
        "usage_events",
    ]
    payload: dict[str, Any]


class GroupMetrics(BaseModel):
    group_id: str
    hash_reuse_count: int = 0
    claim_spread_count: int = 0
    spend_usd: float = 0.0
    reply_count: int = 0


class DashboardSummary(BaseModel):
    lookback_hours: int
    candidate_message_count: int = 0
    factcheck_count: int = 0
    reply_count: int = 0
    unique_groups: int = 0
    trending_claim_count: int = 0
    high_risk_claim_count: int = 0
    spend_usd: float = 0.0


class TrendingClaimRow(BaseModel):
    claim_key: str
    canonical_claim_en: str
    claim_category: ClaimCategory = ClaimCategory.OTHER
    risk_level: RiskLevel = RiskLevel.LOW
    actionability: Actionability = Actionability.MONITOR
    latest_verdict: Verdict | None = None
    has_official_sg_source: bool = False
    official_source_domain_count: int = 0
    distinct_groups: int = 0
    event_count: int = 0
    reply_count: int = 0
    max_confidence: float = 0.0
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class ClaimGroupSpreadRow(BaseModel):
    claim_key: str
    group_id: str
    group_display_name: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    event_count: int = 0
    reply_count: int = 0
