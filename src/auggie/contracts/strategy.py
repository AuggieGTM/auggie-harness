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

from pydantic import (
    AfterValidator,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
    model_validator,
)

from auggie.contracts.common import Contract, FrozenMap, RecordId, Slug, Text

FieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]

BUILTIN_FIELDS: frozenset[str] = frozenset({"first_name", "account_name"})
"""Template fields filled from the account and recipient records, not from evidence."""

PAIN_FIELDS: frozenset[str] = frozenset({"pain", "offer"})
ProofField = Literal["proof_claim", "proof_customer"]
PROOF_FIELD_NAMES: tuple[ProofField, ...] = ("proof_claim", "proof_customer")
PROOF_FIELDS: frozenset[str] = frozenset(PROOF_FIELD_NAMES)
"""Fields a play that names a pain may use, from its approved copy and first usable proof."""

HIRING_FIELDS: frozenset[str] = frozenset({"hiring_count", "hiring_family", "hiring_date"})
"""Fields a play that names a hiring criterion may use, from this run's tally (ADR 0026)."""

Phrase = Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("cannot be blank")
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


# ---- The seller's point of view (ADR 0026) ------------------------------------


Copy = Annotated[str, StringConstraints(min_length=1, max_length=2000), AfterValidator(_not_blank)]
"""Wording the seller approved. Rendering never reflows it."""


def _single_spaced(value: str) -> str:
    if not re.fullmatch(r"\S+(?: \S+)*", value):
        raise ValueError("a claim is one line of words with single spaces, as the draft quotes it")
    return value


Claim = Annotated[
    str, StringConstraints(min_length=1, max_length=1000), AfterValidator(_single_spaced)
]
"""The exact words a draft quotes from a proof's page. A page may break or space them
differently; any run of whitespace there matches one space here."""


class Proof(Contract):
    """One customer story on one public page of the seller's: a vendor claim, quoted exactly."""

    customer: Annotated[Label, AfterValidator(_not_blank)]
    url: HttpUrl
    claim: Claim
    kind: Literal["result", "scale"]
    caveat: Text | None = None
    """What the story does not prove. Shown at review, never in a draft."""


class Proofs(Contract):
    """``[proofs]``: the seller's customer stories by ID, and how old a read of a proof's page
    may be for the proof to be used. In the file, each story is its own ``[proofs.<id>]``."""

    max_age_days: Annotated[int, Field(ge=1, le=3650)] = 180
    by_id: FrozenMap[Slug, Proof] = Field(default_factory=dict, validate_default=True)

    @model_validator(mode="before")
    @classmethod
    def _stories(cls, data: object) -> object:
        # ``by_id`` is no slug, so no story can be named that; its absence means file form.
        if isinstance(data, Mapping) and "by_id" not in data:
            stories = {k: v for k, v in data.items() if k != "max_age_days"}
            age = {"max_age_days": data["max_age_days"]} if "max_age_days" in data else {}
            return {**age, "by_id": stories}
        return data


class Pain(Contract):
    """An angle the seller approved: a problem teams like the account's often have. Never a
    fact about the account, and never recorded as a finding."""

    statement: Copy
    fit: Copy
    """Where the seller fits."""
    limits: Copy
    """Where the seller stops."""
    offer: Copy
    """The one ask a draft closes on."""
    proofs: tuple[Slug, ...] = ()
    """Proof IDs, best first."""
    metric: Copy | None = None
    threshold: Copy | None = None
    consequence: Copy | None = None
    """What to measure, where it starts to hurt, and what breaks then: shown at review only."""

    @field_validator("proofs")
    @classmethod
    def _once(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("a pain lists each proof once")
        return value


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
    placeholders are filled, and rendering never reflows text. A play that names
    a ``pain`` may use its fields; one that names a ``hiring`` criterion may use
    that tally's fields (ADR 0026).
    """

    id: Slug
    requires: Annotated[tuple[Slug, ...], Field(min_length=1)]
    template: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    fields: FrozenMap[FieldName, MergeField] = Field(default_factory=dict, validate_default=True)
    pain: Slug | None = None
    hiring: Slug | None = None

    @property
    def uses_proof(self) -> bool:
        return bool(template_fields(self.template) & PROOF_FIELDS)

    @model_validator(mode="after")
    def _placeholders_are_declared(self) -> Play:
        used = template_fields(self.template)
        reserved = set(self.fields) & (BUILTIN_FIELDS | PAIN_FIELDS | PROOF_FIELDS | HIRING_FIELDS)
        if reserved:
            raise ValueError(f"play {self.id!r} declares reserved fields: {sorted(reserved)}")
        available = BUILTIN_FIELDS | set(self.fields)
        if self.pain is not None:
            available |= PAIN_FIELDS | PROOF_FIELDS
        if self.hiring is not None:
            available |= HIRING_FIELDS
        unknown = used - available
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
    proofs: Proofs = Field(default_factory=Proofs)
    pains: FrozenMap[Slug, Pain] = Field(default_factory=dict, validate_default=True)

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
        for pain_id, pain in self.pains.items():
            for proof_id in pain.proofs:
                if proof_id not in self.proofs.by_id:
                    raise ValueError(f"pain {pain_id!r} names undefined proof {proof_id!r}")
        for play in self.plays:
            for name in play.requires:
                if name not in ids:
                    raise ValueError(f"play {play.id!r} requires undefined criterion {name!r}")
            if play.pain is not None:
                named = self.pains.get(play.pain)
                if named is None:
                    raise ValueError(f"play {play.id!r} names undefined pain {play.pain!r}")
                if play.uses_proof and not named.proofs:
                    raise ValueError(
                        f"play {play.id!r} quotes a proof, but pain {play.pain!r} has none"
                    )
            if play.hiring is not None:
                if play.hiring not in play.requires:
                    raise ValueError(
                        f"play {play.id!r}: its hiring criterion {play.hiring!r} must be required"
                    )
                if not isinstance(self.criterion(play.hiring), FamilyOpenings):
                    raise ValueError(
                        f"play {play.id!r}: hiring names {play.hiring!r}, not a hiring_family"
                    )
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
