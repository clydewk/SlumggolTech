from __future__ import annotations

from collections import Counter

from slumggol_bot.schemas import GroupStyleProfile, NormalizedMessage


def _emoji_count(text: str) -> int:
    return sum(1 for character in text if ord(character) > 10000)


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

        return GroupStyleProfile(
            dominant_languages=dominant_languages,
            emoji_density=emoji_density,
            average_length=average_length,
            punctuation_bias=sorted(set(profile.punctuation_bias + punctuation_seen)),
            discourse_particles=discourse_particles[:10],
            message_count=next_count,
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
        return (
            f"Use a familiar composite group tone. Dominant languages: {languages}. "
            f"Average length: {profile.average_length:.1f} chars. "
            f"Emoji density: {profile.emoji_density:.2f}. "
            f"Punctuation style: {punctuation}. "
            f"Common discourse particles: {particles}. "
            "Do not imitate any specific person or name."
        )
