from __future__ import annotations

from collections import Counter

from slumggol_bot.schemas import GroupStyleProfile, NormalizedMessage


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


def _infer_lingo_style(text: str, current_style: str, message_count: int) -> str:
    """
    Infer lingo style from message text.
    Tilts the current style based on signals found — does not hard-switch.
    Returns one of: genz_professional, professional, senior, mixed.
    """
    lower = text.lower()
    genz_score = sum(1 for signal in _GENZ_SIGNALS if signal in lower)
    senior_score = sum(1 for signal in _SENIOR_SIGNALS if signal in lower)

    # Not enough data yet — keep mixed
    if message_count < 10:
        return current_style

    if genz_score > senior_score and genz_score >= 2:
        return "genz_professional"
    if senior_score > genz_score and senior_score >= 2:
        return "senior"
    if genz_score == 0 and senior_score == 0:
        # Neutral — lean professional
        return "professional" if current_style == "mixed" else current_style
    return current_style


_LINGO_TONE_GUIDANCE: dict[str, str] = {
    "genz_professional": (
        "Tone: Gen Z professional. Be direct, sharp, and confident. "
        "Light casual phrasing is fine (e.g. 'ok so', 'actually', 'ngl') but keep it credible. "
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
        "Light use of discourse particles like 'lah', 'leh', 'hor' is fine. "
        "Keep it conversational but credible."
    ),
}


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

        # Infer lingo style unless admin has set an override
        if profile.lingo_style_override:
            lingo_style = profile.lingo_style_override
        else:
            lingo_style = _infer_lingo_style(text, profile.lingo_style, next_count)

        return GroupStyleProfile(
            dominant_languages=dominant_languages,
            emoji_density=emoji_density,
            average_length=average_length,
            punctuation_bias=sorted(set(profile.punctuation_bias + punctuation_seen)),
            discourse_particles=discourse_particles[:10],
            message_count=next_count,
            lingo_style=lingo_style,
            lingo_style_override=profile.lingo_style_override,
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
        tone = _LINGO_TONE_GUIDANCE.get(profile.lingo_style, _LINGO_TONE_GUIDANCE["mixed"])
        return (
            f"Use a familiar composite group tone. Dominant languages: {languages}. "
            f"Average length: {profile.average_length:.1f} chars. "
            f"Emoji density: {profile.emoji_density:.2f}. "
            f"Punctuation style: {punctuation}. "
            f"Common discourse particles: {particles}. "
            f"{tone} "
            "Do not imitate any specific person or name."
        )
