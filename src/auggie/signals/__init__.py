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

WORD = re.compile(r"[A-Za-z0-9]+")
"""A word, for counting distance: a run of letters and digits. Punctuation separates words."""


@cache
def phrase_pattern(phrase: str) -> re.Pattern[str]:
    """A phrase, case ignored, its spaces matching any whitespace, and no letter or digit right
    before or after it, so ``C++``, ``.NET`` and ``go-to-market`` match as written."""
    parts = [re.escape(part) for part in phrase.split()]
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(parts) + r"(?![A-Za-z0-9])", re.IGNORECASE)


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
    """Where a page names a technology: the line, and the phrase and near word it matched."""

    line: str
    phrase: str
    near: str | None


def stack_matches(text: str, term: Technology) -> list[StackMatch]:
    """Every line of ``text`` that names ``term``, as its ``near`` words require.

    With ``near``, the phrase and a near word must be on the same line with at most
    ``near_words`` words between them, on either side. A phrase never spans lines here.
    """
    found = (_line_match(line, term) for line in text.splitlines())
    return [match for match in found if match is not None]


def _line_match(line: str, term: Technology) -> StackMatch | None:
    for phrase in term.phrases:
        for match in phrase_pattern(phrase).finditer(line):
            if not term.near:
                return StackMatch(line.strip(), phrase, None)
            near = _near_word(line, match.span(), term.near, term.near_words)
            if near is not None:
                return StackMatch(line.strip(), phrase, near)
    return None


def _near_word(line: str, span: tuple[int, int], words: tuple[str, ...], limit: int) -> str | None:
    for word in words:
        for match in phrase_pattern(word).finditer(line):
            if match.end() <= span[0]:
                between = line[match.end() : span[0]]
            elif match.start() >= span[1]:
                between = line[span[1] : match.start()]
            else:
                continue  # overlapping the phrase itself
            if len(WORD.findall(between)) <= limit:
                return word
    return None
