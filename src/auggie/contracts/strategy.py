"""Approved strategy: what accounts are judged against and what may be written (ADR 0013).

A strategy file is TOML in the customer's knowledge namespace. This contract is
its validated shape. Loading the file and computing its ``StrategyRef`` is I/O
and happens outside the contracts.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints, field_validator, model_validator

from auggie.contracts.common import Contract, FrozenMap, RecordId, Slug, Text

FieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]

BUILTIN_FIELDS: frozenset[str] = frozenset({"first_name", "account_name"})
"""Template fields filled from the account and recipient records, not from evidence."""

Phrase = Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("a vocabulary phrase cannot be blank")
    return value


Words = Annotated[str, StringConstraints(min_length=1, max_length=200), AfterValidator(_not_blank)]
"""A phrase of the signal vocabulary: never blank, since a blank phrase would match anything."""


# ---- Signal vocabulary (ADR 0023) -------------------------------------------

Level = Literal[
    "vp", "director", "manager", "lead", "principal", "staff", "senior", "mid", "junior"
]
LADDER: tuple[Level, ...] = (
    "vp",
    "director",
    "manager",
    "lead",
    "principal",
    "staff",
    "senior",
    "mid",
    "junior",
)
"""Seniority levels from the top. A title takes the highest level whose words it contains."""

DEFAULT_LEVEL_WORDS: Mapping[Level, tuple[str, ...]] = MappingProxyType(
    {
        "vp": ("vice president", "vp", "svp", "evp", "chief"),
        "director": ("director", "head of"),
        "manager": ("manager",),
        "lead": ("lead",),
        "principal": ("principal",),
        "staff": ("staff",),
        "senior": ("senior", "sr"),
        "mid": ("mid-level", "intermediate"),
        "junior": ("junior", "jr", "associate", "entry level", "intern", "graduate"),
    }
)
"""Generic job-title words for each level. They name levels, never a domain; a strategy may
replace any level's words (ADR 0023)."""

PatternName = Literal[
    "new_openings", "more_openings", "senior_openings", "leader_opening", "long_listed"
]

Label = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class HiringFamily(Contract):
    """A family of roles the seller counts, recognised by phrases in a role's title."""

    label: Label
    titles: Annotated[tuple[Words, ...], Field(min_length=1)]


class NewOpenings(Contract):
    label: Label | None = None
    min_roles: Annotated[int, Field(ge=1)] = 2


class MoreOpenings(Contract):
    label: Label | None = None
    min_increase: Annotated[int, Field(ge=1)] = 3


class SeniorOpenings(Contract):
    label: Label | None = None
    min_roles: Annotated[int, Field(ge=1)] = 3


class LeaderOpening(Contract):
    label: Label | None = None
    max_others: Annotated[int, Field(ge=0)] = 1


class LongListed(Contract):
    label: Label | None = None
    min_days: Annotated[int, Field(ge=1)] = 120


class HiringPatterns(Contract):
    """Thresholds for the listing patterns, and the window a baseline snapshot is chosen from
    (ADR 0024)."""

    baseline_days: Annotated[int, Field(gt=0)] = 28
    max_lookback_days: Annotated[int, Field(gt=0)] = 180
    new_openings: NewOpenings = Field(default_factory=NewOpenings)
    more_openings: MoreOpenings = Field(default_factory=MoreOpenings)
    senior_openings: SeniorOpenings = Field(default_factory=SeniorOpenings)
    leader_opening: LeaderOpening = Field(default_factory=LeaderOpening)
    long_listed: LongListed = Field(default_factory=LongListed)

    @model_validator(mode="after")
    def _window(self) -> HiringPatterns:
        if self.baseline_days >= self.max_lookback_days:
            raise ValueError("baseline_days must be less than max_lookback_days")
        return self


class Hiring(Contract):
    """Families in the file's order: a role belongs to the first one its title matches."""

    families: FrozenMap[Slug, HiringFamily]
    levels: FrozenMap[Level, Annotated[tuple[Words, ...], Field(min_length=1)]] = Field(
        default_factory=dict, validate_default=True
    )
    patterns: HiringPatterns = Field(default_factory=HiringPatterns)

    @model_validator(mode="after")
    def _has_families(self) -> Hiring:
        if not self.families:
            raise ValueError("signals.hiring needs at least one family")
        return self

    def level_words(self, level: Level) -> tuple[str, ...]:
        return self.levels.get(level, DEFAULT_LEVEL_WORDS[level])


class Technology(Contract):
    """A technology the seller looks for on an account's pages. With ``near``, a phrase counts
    only when one of those words is on the same line, at most ``near_words`` words away."""

    label: Label
    phrases: Annotated[tuple[Words, ...], Field(min_length=1)]
    near: tuple[Words, ...] = ()
    near_words: Annotated[int, Field(ge=0, le=50)] = 8


class Stack(Contract):
    terms: FrozenMap[Slug, Technology]

    @model_validator(mode="after")
    def _has_terms(self) -> Stack:
        if not self.terms:
            raise ValueError("signals.stack needs at least one term")
        return self


class Signals(Contract):
    """The seller's own vocabulary. The code holds none (ADR 0023)."""

    hiring: Hiring | None = None
    stack: Stack | None = None


class Source(Contract):
    """A kind of source the research step looks at for each account."""

    label: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class _Criterion(Contract):
    id: Slug
    question: Text


class _PageCriterion(_Criterion):
    """A criterion judged from one of the strategy's page sources."""

    source: Slug


class HeadcountRange(_PageCriterion):
    """Met when the source states a staff count within ``min`` and ``max``."""

    rule: Literal["headcount_range"]
    min: Annotated[int, Field(ge=0)]
    max: Annotated[int, Field(ge=0)]
    disqualifying: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> HeadcountRange:
        if self.min > self.max:
            raise ValueError(f"{self.id}: min cannot exceed max")
        return self


class JobPosting(_PageCriterion):
    """Met when the source shows an open role matching a keyword, published recently."""

    rule: Literal["job_posting"]
    title_keywords: Annotated[tuple[Phrase, ...], Field(min_length=1)]
    max_age_days: Annotated[int, Field(gt=0)]


class Mentions(_PageCriterion):
    """Met when the source contains every phrase, optionally published recently.

    ``rationale`` is the strategy's own explanation of what a match means.
    """

    rule: Literal["mentions"]
    phrases: Annotated[tuple[Phrase, ...], Field(min_length=1)]
    basis: Literal["source_fact", "inference"]
    rationale: Text
    max_age_days: Annotated[int, Field(gt=0)] | None = None


class FamilyOpenings(_Criterion):
    """Met when the account's careers listing has at least ``min_roles`` open roles in a family."""

    rule: Literal["hiring_family"]
    family: Slug
    min_roles: Annotated[int, Field(ge=1)] = 1


class ListingPattern(_Criterion):
    """Met when a listing pattern holds for a family (ADR 0024)."""

    rule: Literal["hiring_pattern"]
    family: Slug
    pattern: PatternName


class StackMention(_PageCriterion):
    """Met when a page source names one of the seller's technologies, as its term defines."""

    rule: Literal["stack_term"]
    term: Slug


AnyCriterion = (
    HeadcountRange | JobPosting | Mentions | FamilyOpenings | ListingPattern | StackMention
)
Criterion = Annotated[AnyCriterion, Field(discriminator="rule")]
PAGE_CRITERIA = (HeadcountRange, JobPosting, Mentions, StackMention)
LISTING_CRITERIA = (FamilyOpenings, ListingPattern)


class MergeField(Contract):
    """A template field filled from the first capture group of ``pattern`` in ``source``."""

    source: Slug
    pattern: Annotated[str, StringConstraints(min_length=1, max_length=300)]

    @field_validator("pattern")
    @classmethod
    def _one_group(cls, value: str) -> str:
        try:
            compiled = re.compile(value)
        except re.error as error:
            raise ValueError(f"invalid pattern: {error}") from error
        if compiled.groups != 1:
            raise ValueError("a merge field pattern needs exactly one capture group")
        return value


class Play(Contract):
    """A draft template, usable only when every criterion in ``requires`` is met.

    The template's fixed wording is approved copy. Only its ``{field}``
    placeholders are filled, and rendering never reflows text.
    """

    id: Slug
    requires: Annotated[tuple[Slug, ...], Field(min_length=1)]
    template: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    fields: FrozenMap[FieldName, MergeField] = Field(default_factory=dict, validate_default=True)

    @model_validator(mode="after")
    def _placeholders_are_declared(self) -> Play:
        used = template_fields(self.template)
        unknown = used - BUILTIN_FIELDS - set(self.fields)
        if unknown:
            raise ValueError(f"play {self.id!r} uses undeclared fields: {sorted(unknown)}")
        unused = set(self.fields) - used
        if unused:
            raise ValueError(f"play {self.id!r} declares unused fields: {sorted(unused)}")
        return self


class Strategy(Contract):
    """One approved strategy revision."""

    schema_version: Literal[1] = 1
    strategy_id: RecordId
    customer_id: Slug
    sources: FrozenMap[Slug, Source]
    criteria: Annotated[tuple[Criterion, ...], Field(min_length=1)]
    required: Annotated[tuple[Slug, ...], Field(min_length=1)]
    plays: tuple[Play, ...] = ()
    signals: Signals = Field(default_factory=Signals)

    @model_validator(mode="after")
    def _references_resolve(self) -> Strategy:
        ids = [criterion.id for criterion in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("criterion ids must be unique")
        for name in self.required:
            if name not in ids:
                raise ValueError(f"required criterion {name!r} is not defined")
        for criterion in self.criteria:
            if isinstance(criterion, PAGE_CRITERIA) and criterion.source not in self.sources:
                raise ValueError(f"{criterion.id}: unknown source {criterion.source!r}")
            if isinstance(criterion, LISTING_CRITERIA):
                hiring = self.signals.hiring
                if hiring is None or criterion.family not in hiring.families:
                    raise ValueError(f"{criterion.id}: unknown hiring family {criterion.family!r}")
            if isinstance(criterion, StackMention):
                stack = self.signals.stack
                if stack is None or criterion.term not in stack.terms:
                    raise ValueError(f"{criterion.id}: unknown stack term {criterion.term!r}")
        if len({play.id for play in self.plays}) != len(self.plays):
            raise ValueError("play ids must be unique")
        for play in self.plays:
            for name in play.requires:
                if name not in ids:
                    raise ValueError(f"play {play.id!r} requires undefined criterion {name!r}")
            for field_name, field in play.fields.items():
                if field.source not in self.sources:
                    raise ValueError(
                        f"play {play.id!r} field {field_name!r}: unknown source {field.source!r}"
                    )
        return self

    def criterion(self, criterion_id: str) -> AnyCriterion:
        for criterion in self.criteria:
            if criterion.id == criterion_id:
                return criterion
        raise KeyError(criterion_id)


def template_fields(template: str) -> set[str]:
    """Names of the ``{field}`` placeholders in ``template``.

    Only plain names are allowed: no attribute access, indexing, conversion or
    format spec, so rendering cannot reach beyond the values it is given.
    """
    names: set[str] = set()
    for _, name, spec, conversion in string.Formatter().parse(template):
        if name is None:
            continue
        if spec or conversion or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
            raise ValueError(f"placeholder {{{name}}} must be a plain lowercase field name")
        names.add(name)
    return names
