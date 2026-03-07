from __future__ import annotations

from slumggol_bot.schemas import (
    AnalysisMode,
    CandidateDecision,
    FingerprintMatchType,
    HashObservation,
    NormalizedMessage,
)


class CandidateGate:
    def decide(
        self,
        *,
        message: NormalizedMessage,
        analysis_mode: AnalysisMode,
        hash_observations: list[HashObservation],
        simhash_observation: HashObservation | None,
        is_hot_hash: bool,
    ) -> CandidateDecision:
        if analysis_mode == AnalysisMode.ALL_MESSAGES_LLM:
            return CandidateDecision(
                candidate=True,
                reason_codes=["demo_override"],
                hash_observations=hash_observations,
            )

        reason_codes: list[str] = []
        match_type: FingerprintMatchType | None = None
        match_distance: int | None = None
        if message.forwarded_many_times:
            reason_codes.append("forwarded_many_times")
        if is_hot_hash:
            reason_codes.append("hot_hash")
            match_type = FingerprintMatchType.EXACT
            match_distance = 0

        for observation in hash_observations:
            if message.forwarded and observation.cross_group_count >= 2:
                reason_codes.append("forwarded_cross_group_reuse")
                if match_type is None:
                    match_type = FingerprintMatchType.EXACT
                    match_distance = 0
                break
        for observation in hash_observations:
            if observation.same_group_count >= 3:
                reason_codes.append("same_group_repeat")
                if match_type is None:
                    match_type = FingerprintMatchType.EXACT
                    match_distance = 0
                break
        if simhash_observation is not None:
            if message.forwarded and simhash_observation.cross_group_count >= 2:
                reason_codes.append("forwarded_cross_group_reuse_simhash")
                if match_type is None:
                    match_type = FingerprintMatchType.SIMHASH
                    match_distance = simhash_observation.distance
            if simhash_observation.same_group_count >= 3:
                reason_codes.append("same_group_repeat_simhash")
                if match_type is None:
                    match_type = FingerprintMatchType.SIMHASH
                    match_distance = simhash_observation.distance

        return CandidateDecision(
            candidate=bool(reason_codes),
            reason_codes=reason_codes,
            hash_observations=(
                hash_observations + ([simhash_observation] if simhash_observation else [])
            ),
            match_type=match_type,
            match_distance=match_distance,
        )
