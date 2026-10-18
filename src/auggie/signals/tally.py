"""Hiring tallies: listing patterns counted by rules from careers readings (ADR 0024).

A tally is a finding, not evidence. Each function here returns met, unmet or
unknown from readings, exactly as ADR 0024's table says each reading can
settle it, with the roles that illustrate the answer. Missing data never
settles unmet.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from auggie.contracts.strategy import Hiring, PatternName
from auggie.signals import JUNIOR_OR_MID, MANAGER_OR_ABOVE, SENIOR_OR_ABOVE, family_of, level_of

ReadStatus = Literal["complete", "partial", "inconsistent", "failed"]
Answer = Literal["met", "unmet", "unknown"]

ILLUSTRATIONS = 3
"""Role links shown with a pattern; they illustrate it, the readings justify it."""


@dataclass(frozen=True)
class Role:
    role_id: str
    title: str
    url: str
    posted_at: datetime | None = None


@dataclass(frozen=True)
class Reading:
    """One read attempt of one account's listing, as the tally needs it.

    ``listing`` names the account and listing it read; a baseline must name the same one.
    ``order`` is the Store's recording order, used only to break a tie between readings
    made at the same time. Each role ID appears once: a reader collapses repeats with
    ``count_once`` first, and a reading that repeats one is refused.
    """

    reading_id: str
    listing: str
    read_at: datetime
    status: ReadStatus
    roles: tuple[Role, ...] = ()
    order: int = 0

    def __post_init__(self) -> None:
        ids = [role.role_id for role in self.roles]
        if len(set(ids)) != len(ids):
            raise ValueError("a reading lists a role ID twice; count each role once first")

    @property
    def usable(self) -> bool:
        return self.status in ("complete", "partial")

    @property
    def complete(self) -> bool:
        return self.status == "complete"


@dataclass(frozen=True)
class Outcome:
    answer: Answer
    reason: str
    roles: tuple[Role, ...] = ()
    readings: tuple[str, ...] = ()


def count_once(roles: Iterable[Role]) -> tuple[tuple[Role, ...], bool]:
    """Each role once, by its ID, and whether the copies agreed.

    Identical copies, as pages that overlap produce, collapse into one. Copies of one ID
    that differ mean the listing gave two answers: the reading is then inconsistent
    (ADR 0024).
    """
    seen: dict[str, Role] = {}
    consistent = True
    for role in roles:
        known = seen.setdefault(role.role_id, role)
        if known != role:
            consistent = False
    return tuple(seen.values()), consistent


def in_family(reading: Reading, family: str, hiring: Hiring) -> tuple[Role, ...]:
    return tuple(r for r in reading.roles if family_of(r.title, hiring) == family)


def choose_baseline(current: Reading, earlier: Iterable[Reading], hiring: Hiring) -> Reading | None:
    """The newest complete reading at least ``baseline_days`` and at most
    ``max_lookback_days`` before ``current``. Ties go to the later recorded one."""
    window = hiring.patterns
    newest = current.read_at - timedelta(days=window.baseline_days)
    oldest = current.read_at - timedelta(days=window.max_lookback_days)
    eligible = [
        r
        for r in earlier
        if r.complete
        and r.listing == current.listing
        and r.reading_id != current.reading_id
        and oldest <= r.read_at <= newest
    ]
    return max(eligible, key=lambda r: (r.read_at, r.order), default=None)


def _unknown(reason: str, *readings: Reading) -> Outcome:
    return Outcome("unknown", reason, readings=tuple(r.reading_id for r in readings))


def _settled(answer: Answer, reason: str, roles: Sequence[Role], *readings: Reading) -> Outcome:
    return Outcome(
        answer, reason, tuple(roles[:ILLUSTRATIONS]), tuple(r.reading_id for r in readings)
    )


def _unusable(current: Reading) -> Outcome | None:
    if current.status == "failed":
        return _unknown("The listing could not be read.", current)
    if current.status == "inconsistent":
        return _unknown("The listing's pages did not agree, so they settle nothing.", current)
    return None


def family_openings(current: Reading, family: str, min_roles: int, hiring: Hiring) -> Outcome:
    stop = _unusable(current)
    if stop:
        return stop
    roles = in_family(current, family, hiring)
    count = f"{'at least ' if not current.complete else ''}{len(roles)}"
    if len(roles) >= min_roles:
        return _settled("met", f"{count} open roles in the family.", roles, current)
    if current.complete:
        return _settled("unmet", f"{len(roles)} open roles in the family.", roles, current)
    return _unknown(f"At least {len(roles)} open roles; the listing was not read in full.", current)


def listing_pattern(
    pattern: PatternName,
    current: Reading,
    earlier: Iterable[Reading],
    family: str,
    hiring: Hiring,
) -> Outcome:
    """Whether ``pattern`` holds for ``family``, per ADR 0024's table."""
    stop = _unusable(current)
    if stop:
        return stop
    settings = hiring.patterns
    roles = in_family(current, family, hiring)
    if pattern in ("new_openings", "more_openings"):
        baseline = choose_baseline(current, earlier, hiring)
        if baseline is None:
            return _unknown("No complete earlier reading is in the comparison window.", current)
        before = in_family(baseline, family, hiring)
        now = f"{'' if current.complete else 'at least '}{len(roles)}"
        if pattern == "new_openings":
            minimum = settings.new_openings.min_roles
            if before:
                return _settled(
                    "unmet", f"{len(before)} openings already before.", before, current, baseline
                )
            if len(roles) >= minimum:
                return _settled("met", f"None before; {now} open now.", roles, current, baseline)
            if current.complete:
                return _settled(
                    "unmet", f"None before; {len(roles)} open now.", roles, current, baseline
                )
            return _unknown(
                "None before; the current listing was not read in full.", current, baseline
            )
        increase = len(roles) - len(before)
        if increase >= settings.more_openings.min_increase:
            return _settled("met", f"{len(before)} before, {now} now.", roles, current, baseline)
        if current.complete:
            return _settled(
                "unmet", f"{len(before)} before, {len(roles)} now.", roles, current, baseline
            )
        return _unknown("The current listing was not read in full.", current, baseline)
    if pattern == "long_listed":
        age = timedelta(days=settings.long_listed.min_days)
        old = [r for r in roles if r.posted_at is not None and current.read_at - r.posted_at >= age]
        if old:
            return _settled("met", f"Listed since {old[0].posted_at:%Y-%m-%d}.", old, current)
        if current.complete and all(r.posted_at is not None for r in roles):
            return _settled("unmet", "Every role in the family is more recent.", roles, current)
        return _unknown(
            "Not every role in the family is dated, or the listing is partial.", current
        )
    if not current.complete:
        return _unknown("The listing was not read in full.", current)
    levels = [level_of(r.title, hiring) for r in roles]
    unlevelled = levels.count(None)
    if pattern == "senior_openings":
        senior = [r for r, lv in zip(roles, levels, strict=True) if lv in SENIOR_OR_ABOVE]
        low = sum(1 for lv in levels if lv in JUNIOR_OR_MID)
        minimum = settings.senior_openings.min_roles
        if len(senior) >= minimum and low == 0 and unlevelled == 0:
            return _settled("met", f"{len(senior)} roles, all senior or above.", senior, current)
        if low or len(senior) + unlevelled < minimum:
            return _settled(
                "unmet", f"{len(senior)} senior roles, {low} junior or mid.", roles, current
            )
        return _unknown(f"{unlevelled} roles have no level in their title.", current)
    leaders = [r for r, lv in zip(roles, levels, strict=True) if lv in MANAGER_OR_ABOVE]
    limit = settings.leader_opening.max_others
    if leaders and len(roles) - 1 <= limit:
        return _settled("met", f"A leader's opening among {len(roles)} roles.", leaders, current)
    if len(roles) > limit + 1 or (not leaders and not unlevelled):
        return _settled(
            "unmet", f"{len(roles)} roles, {len(leaders)} of them leaders.", roles, current
        )
    return _unknown(f"{unlevelled} roles have no level in their title.", current)
