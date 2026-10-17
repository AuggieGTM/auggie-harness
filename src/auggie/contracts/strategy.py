"""Approved strategy: what accounts are judged against and what may be written (ADR 0013).

A strategy file is TOML in the customer's knowledge namespace. This contract is
its validated shape. Loading the file and computing its ``StrategyRef`` is I/O
and happens outside the contracts.
"""

from __future__ import annotations

import re
import string
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from auggie.contracts.common import Contract, FrozenMap, RecordId, Slug, Text

FieldName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]

BUILTIN_FIELDS: frozenset[str] = frozenset({"first_name", "account_name"})
"""Template fields filled from the account and recipient records, not from evidence."""

Phrase = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Source(Contract):
    """A kind of source the research step looks at for each account."""

    label: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class _Criterion(Contract):
    id: Slug
    question: Text
    source: Slug


class HeadcountRange(_Criterion):
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


class JobPosting(_Criterion):
    """Met when the source shows an open role matching a keyword, published recently."""

    rule: Literal["job_posting"]
    title_keywords: Annotated[tuple[Phrase, ...], Field(min_length=1)]
    max_age_days: Annotated[int, Field(gt=0)]


class Mentions(_Criterion):
    """Met when the source contains every phrase, optionally published recently.

    ``rationale`` is the strategy's own explanation of what a match means.
    """

    rule: Literal["mentions"]
    phrases: Annotated[tuple[Phrase, ...], Field(min_length=1)]
    basis: Literal["source_fact", "inference"]
    rationale: Text
    max_age_days: Annotated[int, Field(gt=0)] | None = None


Criterion = Annotated[HeadcountRange | JobPosting | Mentions, Field(discriminator="rule")]


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

    @model_validator(mode="after")
    def _references_resolve(self) -> Strategy:
        ids = [criterion.id for criterion in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("criterion ids must be unique")
        for name in self.required:
            if name not in ids:
                raise ValueError(f"required criterion {name!r} is not defined")
        for criterion in self.criteria:
            if criterion.source not in self.sources:
                raise ValueError(f"{criterion.id}: unknown source {criterion.source!r}")
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

    def criterion(self, criterion_id: str) -> HeadcountRange | JobPosting | Mentions:
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
