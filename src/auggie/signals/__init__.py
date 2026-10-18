"""Matching the seller's vocabulary against titles and page text (ADR 0023).

Pure functions, no model and no I/O: the same words and text always give the
same result. What a match means is the strategy's business; here a match is
only "these words appear here".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from auggie.contracts.strategy import LADDER, Hiring, Level, Technology

WORD = re.compile(r"[^\W_]+")
"""A word, for counting distance: a run of letters and digits, in any script. Punctuation and
underscores separate words."""

QUOTE_CHARS = 300
"""The longest passage a match quotes. It always holds the matched words."""


@cache
def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """A phrase, case ignored, its spaces matching any whitespace, and no letter or digit, in any
    script, right before or after it, so ``C++``, ``.NET`` and ``go-to-market`` match as written."""
    parts = [re.escape(part) for part in phrase.split()]
    if not parts:
        raise ValueError("a blank phrase would match anything")
    return re.compile(r"(?<![^\W_])" + r"\s+".join(parts) + r"(?![^\W_])", re.IGNORECASE)


def mentions(text: str, phrase: str) -> bool:
    return phrase_pattern(phrase).search(text) is not None


def family_of(title: str, hiring: Hiring) -> str | None:
    """The first family, in the strategy's order, with a phrase in ``title``; else None."""
    for family_id, family in hiring.families.items():
        if any(mentions(title, phrase) for phrase in family.titles):
            return family_id
    return None


def level_of(title: str, hiring: Hiring) -> Level | None:
    """The highest level on the ladder whose words ``title`` contains; None if unlevelled."""
    for level in LADDER:
        if any(mentions(title, word) for word in hiring.level_words(level)):
            return level
    return None


SENIOR_OR_ABOVE: frozenset[Level] = frozenset(LADDER[: LADDER.index("senior") + 1])
MANAGER_OR_ABOVE: frozenset[Level] = frozenset(LADDER[: LADDER.index("manager") + 1])
JUNIOR_OR_MID: frozenset[Level] = frozenset({"mid", "junior"})


@dataclass(frozen=True)
class StackMatch:
    """Where a page names a technology: a quote that holds the matched phrase and near word."""

    quote: str
    phrase: str
    near: str | None


def stack_matches(text: str, term: Technology) -> list[StackMatch]:
    """Every place ``text`` names ``term``, as its ``near`` words require.

    Without ``near``, a phrase may span lines, as any whitespace may sit between its words.
    With ``near``, the phrase and a near word must be on one line with at most
    ``near_words`` words between them, on either side.
    """
    if not term.near:
        found = []
        for phrase in term.phrases:
            for match in phrase_pattern(phrase).finditer(text):
                found.append(StackMatch(_quote(text, match.span()), phrase, None))
        return sorted(found, key=lambda m: text.find(m.quote))
    found = []
    for line in text.splitlines():
        hit = _line_match(line, term)
        if hit is not None:
            found.append(hit)
    return found


def _line_match(line: str, term: Technology) -> StackMatch | None:
    for phrase in term.phrases:
        for match in phrase_pattern(phrase).finditer(line):
            near = _near_word(line, match.span(), term.near, term.near_words)
            if near is not None:
                word, span = near
                both = (min(match.start(), span[0]), max(match.end(), span[1]))
                return StackMatch(_quote(line, both), phrase, word)
    return None


def _near_word(
    line: str, span: tuple[int, int], words: tuple[str, ...], limit: int
) -> tuple[str, tuple[int, int]] | None:
    for word in words:
        for match in phrase_pattern(word).finditer(line):
            if match.end() <= span[0]:
                between = line[match.end() : span[0]]
            elif match.start() >= span[1]:
                between = line[span[1] : match.start()]
            else:
                continue  # overlapping the phrase itself
            if len(WORD.findall(between)) <= limit:
                return word, match.span()
    return None


def _quote(text: str, span: tuple[int, int]) -> str:
    """A passage of ``text`` around ``span``, at most ``QUOTE_CHARS`` long beyond the span itself,
    with its whitespace collapsed. It always contains the whole span."""
    spare = max(0, QUOTE_CHARS - (span[1] - span[0])) // 2
    start, end = max(0, span[0] - spare), min(len(text), span[1] + spare)
    return " ".join(text[start:end].split())
