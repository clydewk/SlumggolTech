from __future__ import annotations

from collections import Counter

from slumggol_bot.schemas import GroupStyleProfile, NormalizedMessage

_TONE_BUFFER_SIZE = 100
_TONE_INFERENCE_INTERVAL = 100


def _emoji_count(text: str) -> int:
    return sum(1 for character in text if ord(character) > 10000)


_GENZ_SIGNALS = [
    "ngl", "fr", "no cap", "lowkey", "highkey", "vibe", "slay", "bet",
    "imo", "tbh", "idk", "omg", "lol", "lmao", "bruh", "ok so",
    "literally", "actually", "wait", "omg wait", "not gonna lie",
]

_SENIOR_SIGNALS = [
    "please be informed", "kindly", "dear all", "forwarded",
    "please share", "god bless", "take care", "warm regards",
    "as per", "herewith", "please note",
]

TONE_PRESETS: dict[str, str] = {
    "genz_professional": (
        "Tone: Gen Z professional. Be direct, sharp, and confident. "
        "Light casual phrasing is fine but keep it credible. "
        "Avoid boomer-style filler. Short punchy sentences. 1-2 emoji max."
    ),
    "professional": (
        "Tone: Professional. Clear, measured, and trustworthy. "
        "Minimal slang. No emoji unless the group uses them heavily. "
        "Structured sentences."
    ),
    "senior": (
        "Tone: Warm and accessible. Speak plainly and clearly. "
        "Avoid jargon and abbreviations. Be reassuring. "
        "Use full sentences. One emoji at most."
    ),
    "mixed": (
        "Tone: Neutral Singlish. Friendly and direct. "
        "Light use of discourse particles like lah, leh, hor is fine. "
        "Keep it conversational but credible."
    ),
}


def tone_prompt_block(profile: GroupStyleProfile) -> str:
    override = profile.lingo_style_override
    if override:
        if override in TONE_PRESETS:
            return TONE_PRESETS[override]
        return f"Tone instruction (custom): {override}"
    if profile.generated_tone:
        return f"Tone instruction (inferred from group history): {profile.generated_tone}"
    return TONE_PRESETS.get(profile.lingo_style, TONE_PRESETS["mixed"])


def should_regenerate_tone(profile: GroupStyleProfile) -> bool:
    if profile.lingo_style_override:
        return False
    if len(profile.tone_sample_buffer) < _TONE_BUFFER_SIZE:
        return False
    return profile.message_count % _TONE_INFERENCE_INTERVAL == 0


def tone_inference_prompt(profile: GroupStyleProfile) -> str:
    sample = "\n".join(f"- {msg}" for msg in profile.tone_sample_buffer[-50:])
    return (
        "You are analysing the communication style of a Telegram group chat in Singapore.\n"
        "Based on these recent messages, describe the group tone in 2-3 sentences.\n"
        "Focus on: formality level, use of Singlish, emoji usage, sentence length, "
        "and any distinctive speech patterns.\n"
        "This description will be used to instruct a fact-checking bot to match the group style.\n"
        "Be specific and practical. Do not use bullet points. Plain text only.\n\n"
        f"Recent messages:\n{sample}"
    )


def _infer_lingo_style_heuristic(text: str, current_style: str, message_count: int) -> str:
    lower = text.lower()
    genz_score = sum(1 for signal in _GENZ_SIGNALS if signal in lower)
    senior_score = sum(1 for signal in _SENIOR_SIGNALS if signal in lower)
    if message_count < 10:
        return current_style
    if genz_score > senior_score and genz_score >= 2:
        return "genz_professional"
    if senior_score > genz_score and senior_score >= 2:
        return "senior"
    if genz_score == 0 and senior_score == 0:
        return "professional" if current_style == "mixed" else current_style
    return current_style


class StyleProfileService:
    def update_profile(
        self,
        profile: GroupStyleProfile,
        message: NormalizedMessage,
    ) -> GroupStyleProfile:
        text = message.primary_text
        languages = list(profile.dominant_languages)
        languages.extend(message.detected_languages)
        language_counts = Counter(languages)
        dominant_languages = [item for item, _ in language_counts.most_common(3)]

        next_count = profile.message_count + 1
        average_length = (
            (profile.average_length * profile.message_count) + len(text)
        ) / max(next_count, 1)
        emoji_density = (
            (profile.emoji_density * profile.message_count) + _emoji_count(text)
        ) / max(next_count, 1)

        punctuation_seen = []
        for punctuation in ["!", "?", "...", ".", ","]:
            if punctuation in text:
                punctuation_seen.append(punctuation)

        discourse_particles = profile.discourse_particles[:]
        for token in ["lah", "leh", "hor", "ah", "ok", "please"]:
            if token in text.lower() and token not in discourse_particles:
                discourse_particles.append(token)

        buffer = profile.tone_sample_buffer[:]
        if text.strip():
            buffer.append(text.strip())
        buffer = buffer[-_TONE_BUFFER_SIZE:]

        if profile.lingo_style_override and profile.lingo_style_override in TONE_PRESETS:
            lingo_style = profile.lingo_style_override
        elif not profile.lingo_style_override:
            lingo_style = _infer_lingo_style_heuristic(text, profile.lingo_style, next_count)
        else:
            lingo_style = profile.lingo_style

        return GroupStyleProfile(
            dominant_languages=dominant_languages,
            emoji_density=emoji_density,
            average_length=average_length,
            punctuation_bias=sorted(set(profile.punctuation_bias + punctuation_seen)),
            discourse_particles=discourse_particles[:10],
            message_count=next_count,
            lingo_style=lingo_style,
            lingo_style_override=profile.lingo_style_override,
            tone_sample_buffer=buffer,
            generated_tone=profile.generated_tone,
        )

    def prompt_guidance(self, profile: GroupStyleProfile) -> str:
        languages = (
            ", ".join(profile.dominant_languages)
            if profile.dominant_languages
            else "English"
        )
        punctuation = (
            ", ".join(profile.punctuation_bias)
            if profile.punctuation_bias
            else "light punctuation"
        )
        particles = (
            ", ".join(profile.discourse_particles)
            if profile.discourse_particles
            else "none"
        )
        tone = tone_prompt_block(profile)
        return (
            f"Use a familiar composite group tone. Dominant languages: {languages}. "
            f"Average length: {profile.average_length:.1f} chars. "
            f"Emoji density: {profile.emoji_density:.2f}. "
            f"Punctuation style: {punctuation}. "
            f"Common discourse particles: {particles}. "
            f"{tone} "
            "Do not imitate any specific person or name."
        )