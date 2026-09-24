"""Fixed-up spelling of names Whisper hears right but spells wrong.

Deliberately narrow. This is not a rewriter: it does not touch grammar, order,
filler words or phrasing. The only job is turning "cloud flare" into
"Cloudflare" so the verbatim text is actually usable.
"""

from __future__ import annotations

import re

_FILLERS = re.compile(r"\b(?:um+|uh+|erm+|ah+)\b[,]?\s*", re.IGNORECASE)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%)\]}])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def _match_case(replacement: str, original: str) -> str:
    """Keep the speaker's capitalisation when the correction is all lower."""
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _flexible(term: str) -> re.Pattern:
    """Match a term however Whisper split it: spaced, hyphenated or joined.

    Whisper writes the same name three ways across runs ("webhook",
    "web-hook", "web hook"), so a correction keyed on one spelling misses the
    other two. Every gap in the key matches a space, a hyphen, or nothing.
    """
    parts = [re.escape(part) for part in re.split(r"[\s-]+", term.strip()) if part]
    return re.compile(r"\b" + r"[\s-]*".join(parts) + r"\b", re.IGNORECASE)


def apply_corrections(text: str, corrections: dict[str, str]) -> str:
    for wrong, right in corrections.items():
        text = _flexible(wrong).sub(lambda m, r=right: _match_case(r, m.group(0)), text)
    return text


def fix_casing(text: str, terms: list[str]) -> str:
    """Restore the real capitalisation of names, changing no words.

    Whisper hears "GitHub" correctly and then writes it "github". This
    puts the capitals back. Only terms with a single true spelling belong here:
    anything that is also an ordinary English word (tide, crude, square) would
    get wrongly capitalised mid-sentence.
    """
    for term in terms:
        text = _flexible(term).sub(lambda m, t=term: t, text)
    return text


def strip_fillers(text: str) -> str:
    cleaned = _FILLERS.sub("", text)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else cleaned


def tidy(text: str) -> str:
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def finalise(text: str, *, corrections: dict[str, str] | None = None,
             casing: list[str] | None = None,
             fillers: bool = False, trailing_space: bool = True) -> str:
    if not text:
        return ""
    if fillers:
        text = strip_fillers(text)
    if corrections:
        text = apply_corrections(text, corrections)
    if casing:
        text = fix_casing(text, casing)
    text = tidy(text)
    if text and trailing_space:
        text += " "
    return text
