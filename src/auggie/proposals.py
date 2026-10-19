"""Strategy proposals from outcomes (ADR 0028): the plays, and each pain's proofs, in order of
their results, written as a patch the person reviews and applies. Never new wording, never a
removal, and never applied by the harness.

The strategy's own text is edited, so its comments and layout stay as they are, and the
result is accepted only if it parses to the same strategy in the new order.
"""

from __future__ import annotations

import difflib
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auggie.learning import Tally
from auggie.strategy import LoadedStrategy

MIN_SETTLED = 10


@dataclass(frozen=True)
class Proposal:
    what: str
    """What is reordered: "plays", or a pain's proofs."""
    before: tuple[str, ...]
    after: tuple[str, ...]
    reasons: tuple[str, ...]
    """Each moved item's rate, as the person will read it."""


def ranked(
    current: Sequence[str], results: Mapping[str, Tally], min_settled: int
) -> tuple[str, ...]:
    """``current`` with the items that have enough settled deliveries in order of their rate,
    best first, ties in their current order; every other item keeps its place."""
    eligible = [
        k for k in current if (t := results.get(k)) is not None and t.settled >= min_settled
    ]
    best_first = iter(sorted(eligible, key=lambda k: -(results[k].rate or 0.0)))
    chosen = set(eligible)
    return tuple(next(best_first) if k in chosen else k for k in current)


def _reasons(order: Sequence[str], results: Mapping[str, Tally]) -> tuple[str, ...]:
    reasons = []
    for key in order:
        tally = results.get(key)
        if tally is not None and tally.rate is not None:
            reasons.append(f"{key}: {tally.positive} of {tally.settled} settled were positive")
    return tuple(reasons)


def propose(
    loaded: LoadedStrategy,
    plays: Mapping[str, Tally],
    proofs: Mapping[str, Tally],
    min_settled: int = MIN_SETTLED,
) -> tuple[list[Proposal], list[str], str]:
    """The proposals the results support, the ones that need a person's hand, and the
    strategy's text with every proposal that could be made safely."""
    if min_settled < MIN_SETTLED:
        raise ValueError(f"a proposal needs at least {MIN_SETTLED} settled deliveries")
    if not loaded.source:
        raise ValueError("the strategy's own text is not at hand, so nothing can be proposed")
    text = loaded.source.decode("utf-8")
    strategy = loaded.strategy
    proposals: list[Proposal] = []
    by_hand: list[str] = []
    play_ids = tuple(p.id for p in strategy.plays)
    reordered = ranked(play_ids, plays, min_settled)
    if reordered != play_ids:
        proposal = Proposal("plays", play_ids, reordered, _reasons(reordered, plays))
        changed = _reorder_plays(text, play_ids, reordered)
        if changed is None:
            by_hand.append(f"put the plays in this order: {', '.join(reordered)}")
        else:
            text = changed
            proposals.append(proposal)
    for pain_id, pain in strategy.pains.items():
        reordered = ranked(pain.proofs, proofs, min_settled)
        if reordered == pain.proofs:
            continue
        proposal = Proposal(
            f"pain {pain_id}'s proofs", pain.proofs, reordered, _reasons(reordered, proofs)
        )
        changed = _reorder_proofs(text, pain_id, pain.proofs, reordered)
        if changed is None:
            by_hand.append(f"list pain {pain_id}'s proofs as: {', '.join(reordered)}")
        else:
            text = changed
            proposals.append(proposal)
    return proposals, by_hand, text


HEADER = re.compile(r"^\s*\[")
QUOTES = ("'''", '"""')


def _table_headers(lines: Sequence[str]) -> list[int]:
    """The lines that open a table, outside multi-line strings."""
    headers, inside = [], None
    for n, line in enumerate(lines):
        if inside is None and HEADER.match(line):
            headers.append(n)
        for quote in QUOTES:
            if inside in (None, quote) and line.count(quote) % 2 == 1:
                inside = None if inside == quote else quote
    return headers


def _reorder_plays(text: str, before: Sequence[str], after: Sequence[str]) -> str | None:
    lines = text.splitlines(keepends=True)
    headers = _table_headers(lines)
    plays = [n for n in headers if lines[n].strip() == "[[plays]]"]
    if len(plays) != len(before):
        return None
    following = [n for n in headers if n > plays[-1]]
    end = following[0] if following else len(lines)
    if [n for n in headers if plays[0] < n < end] != plays[1:]:
        return None  # another table sits between plays, so they are not one run of blocks
    starts = [_lead(lines, n) for n in plays]
    region_end = _lead(lines, end) if following else end
    blocks = [lines[s:e] for s, e in zip(starts, [*starts[1:], region_end], strict=True)]
    blocks = [_trimmed(block) for block in blocks]
    gap = lines[starts[-1] : region_end][len(blocks[-1]) :]
    by_id = dict(zip(before, blocks, strict=True))
    region: list[str] = []
    for n, play_id in enumerate(after):
        region.extend(by_id[play_id])
        region.append("\n" if n < len(after) - 1 else "")
    new = "".join(lines[: starts[0]]) + "".join(region) + "".join(gap) + "".join(lines[region_end:])
    if not _same_but_order(text, new, "plays", before, after):
        return None
    return new


def _lead(lines: Sequence[str], header: int) -> int:
    """Where a table's block begins: its header, or the comments right above it."""
    start = header
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    return start


def _trimmed(block: list[str]) -> list[str]:
    while block and not block[-1].strip():
        block = block[:-1]
    if block and not block[-1].endswith("\n"):
        block = [*block[:-1], block[-1] + "\n"]
    return block


def _reorder_proofs(
    text: str, pain_id: str, before: Sequence[str], after: Sequence[str]
) -> str | None:
    lines = text.splitlines(keepends=True)
    headers = _table_headers(lines)
    table = [n for n in headers if lines[n].strip() == f"[pains.{pain_id}]"]
    if len(table) != 1:
        return None
    end = next((n for n in headers if n > table[0]), len(lines))
    pattern = re.compile(r"^(\s*proofs\s*=\s*)\[[^\]\n]*\](.*)$", re.DOTALL)
    found = [n for n in range(table[0] + 1, end) if pattern.match(lines[n])]
    if len(found) != 1:
        return None  # an array over several lines is left to the person
    match = pattern.match(lines[found[0]])
    assert match is not None
    listed = ", ".join(f'"{proof}"' for proof in after)
    lines[found[0]] = f"{match.group(1)}[{listed}]{match.group(2)}"
    new = "".join(lines)
    if not _same_but_order(text, new, ("pains", pain_id, "proofs"), before, after):
        return None
    return new


def _same_but_order(
    old: str, new: str, where: str | tuple[str, ...], before: Sequence[str], after: Sequence[str]
) -> bool:
    """Whether ``new`` parses to ``old`` with only the items at ``where`` in the new order."""
    try:
        parsed_old, parsed_new = tomllib.loads(old), tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        return False
    path = (where,) if isinstance(where, str) else where
    expected: Any = parsed_old
    for key in path[:-1]:
        expected = expected[key]
    items = expected[path[-1]]
    if path == ("plays",):
        position = {play["id"]: play for play in items}
        expected[path[-1]] = [position[play_id] for play_id in after]
    else:
        expected[path[-1]] = list(after)
    return parsed_new == parsed_old and list(before) != list(after)


NO_NEWLINE = "\\ No newline at end of file\n"


def patch(loaded: LoadedStrategy, text: str) -> str:
    """A unified diff from the strategy's text to ``text``, for ``git apply`` in its repo. A
    last line without a newline is marked as Git marks it."""
    old = loaded.source.decode("utf-8")
    path = loaded.ref.path
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        text.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(line if line.endswith("\n") else f"{line}\n{NO_NEWLINE}" for line in lines)


def write_patch(path: Path, body: str) -> None:
    """Write the patch as a new file only its owner can read; never replace a file."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
