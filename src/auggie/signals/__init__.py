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
"""How much text a quote aims for around its matches. Matched words are never cut, so two long
matched phrases far apart can make a quote somewhat longer."""


@cache
def phrase_pattern(phrase: str, *, ignore_case: bool = True) -> re.Pattern[str]:
    """A phrase, case ignored unless asked, its spaces matching any whitespace, and no letter or
    digit, in any script, right before or after it, so ``C++``, ``.NET`` and ``go-to-market``
    match as written."""
    parts = [re.escape(part) for part in phrase.split()]
    if not parts:
        raise ValueError("a blank phrase would match anything")
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(r"(?<![^\W_])" + r"\s+".join(parts) + r"(?![^\W_])", flags)


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
    """Where a page names a technology: a bounded quote that holds the matched phrase and near
    word, and the phrase's position in the text, for ordering."""

    quote: str
    phrase: str
    near: str | None
    at: int


def stack_matches(text: str, term: Technology) -> list[StackMatch]:
    """Every place ``text`` names ``term``, as its ``near`` words require, in text order.

    Without ``near``, a phrase may span lines, as any whitespace may sit between its words.
    With ``near``, the phrase and a near word must be on one line with at most
    ``near_words`` words between them, on either side.
    """
    found: list[StackMatch] = []
    if not term.near:
        for phrase in term.phrases:
            for match in phrase_pattern(phrase).finditer(text):
                found.append(StackMatch(quote(text, [match.span()]), phrase, None, match.start()))
        return sorted(found, key=lambda m: m.at)
    offset = 0
    for line in text.splitlines(keepends=True):
        hit = _line_match(line, term, offset)
        if hit is not None:
            found.append(hit)
        offset += len(line)
    return found


def _line_match(line: str, term: Technology, offset: int) -> StackMatch | None:
    for phrase in term.phrases:
        for match in phrase_pattern(phrase).finditer(line):
            near = _near_word(line, match.span(), term.near, term.near_words)
            if near is not None:
                word, span = near
                quoted = quote(line, [match.span(), span])
                return StackMatch(quoted, phrase, word, offset + match.start())
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


def quote(text: str, spans: list[tuple[int, int]]) -> str:
    """A quote that holds every span, with its whitespace collapsed and its length bounded.

    Spans that fit within ``QUOTE_CHARS`` together are quoted as one passage. Spans farther
    apart are each quoted with a little context, joined by an ellipsis, so the quote never
    drops a matched word and never grows with the gap between them.
    """
    start, end = min(a for a, _ in spans), max(b for _, b in spans)
    if end - start <= QUOTE_CHARS:
        return _passage(text, start, end, QUOTE_CHARS)
    return " … ".join(_passage(text, a, b, QUOTE_CHARS // 2) for a, b in sorted(spans))


def _passage(text: str, start: int, end: int, width: int) -> str:
    spare = max(0, width - (end - start)) // 2
    return " ".join(text[max(0, start - spare) : min(len(text), end + spare)].split())
