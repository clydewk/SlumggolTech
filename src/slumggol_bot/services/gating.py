from __future__ import annotations

from slumggol_bot.schemas import AnalysisMode, CandidateDecision, HashObservation, NormalizedMessage


class CandidateGate:
    def decide(
        self,
        *,
        message: NormalizedMessage,
        analysis_mode: AnalysisMode,
        hash_observations: list[HashObservation],
        is_hot_hash: bool,
    ) -> CandidateDecision:
        if analysis_mode == AnalysisMode.ALL_MESSAGES_LLM:
            return CandidateDecision(
                candidate=True,
                reason_codes=["demo_override"],
                hash_observations=hash_observations,
            )

        reason_codes: list[str] = []
        if message.forwarded_many_times:
            reason_codes.append("forwarded_many_times")
        if is_hot_hash:
            reason_codes.append("hot_hash")

        for observation in hash_observations:
            if message.forwarded and observation.cross_group_count >= 2:
                reason_codes.append("forwarded_cross_group_reuse")
                break
        for observation in hash_observations:
            if observation.same_group_count >= 3:
                reason_codes.append("same_group_repeat")
                break

        return CandidateDecision(
            candidate=bool(reason_codes),
            reason_codes=reason_codes,
            hash_observations=hash_observations,
        )

