from __future__ import annotations

from dataclasses import dataclass

# Languages we treat as mutually intelligible / same-family for conflict purposes.
_SAME_FAMILY: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"zh", "zh-hans", "zh-hant", "zh-sg", "zh-tw", "zh-cn"}),
        frozenset({"ms", "id"}),
    }
)


def _family(lang: str) -> frozenset[str]:
    normalised = lang.lower().strip()
    for family in _SAME_FAMILY:
        if normalised in family:
            return family
    return frozenset({normalised})


def _conflicts(a: str, b: str) -> bool:
    return _family(a).isdisjoint(_family(b))


@dataclass(frozen=True, slots=True)
class LanguageConflict:
    message_languages: list[str]
    group_languages: list[str]
    languages_to_reply_in: list[str]


def detect_conflict(
    message_languages: list[str],
    group_languages: list[str],
) -> LanguageConflict | None:
    if not message_languages or not group_languages:
        return None

    foreign = [
        lang for lang in message_languages if all(_conflicts(lang, g) for g in group_languages)
    ]
    if not foreign:
        return None

    seen: set[str] = set()
    reply_langs: list[str] = []
    for lang in (*group_languages, *foreign):
        if lang not in seen:
            seen.add(lang)
            reply_langs.append(lang)

    return LanguageConflict(
        message_languages=message_languages,
        group_languages=group_languages,
        languages_to_reply_in=reply_langs,
    )


def conflict_prompt_block(conflict: LanguageConflict) -> str:
    lang_list = ", ".join(conflict.languages_to_reply_in)
    return (
        f"\nLANGUAGE_CONFLICT DETECTED\n"
        f"The forwarded message language(s): {', '.join(conflict.message_languages)}\n"
        f"This group's dominant language(s): {', '.join(conflict.group_languages)}\n"
        f"You must populate reply_versions with one entry per language, "
        f"in this order: {lang_list}\n"
    )
