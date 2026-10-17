"""Building blocks shared by every contract."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    StringConstraints,
    WrapSerializer,
)


class Contract(BaseModel):
    """Base for all contracts: immutable, and unknown fields are rejected.

    A record that needs to change is superseded by a new record, not edited.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


K = TypeVar("K")
V = TypeVar("V")


def _read_only[Key, Value](value: Mapping[Key, Value]) -> Mapping[Key, Value]:
    return MappingProxyType(dict(value))


def _as_dict(value: Mapping[Any, Any], handler: SerializerFunctionWrapHandler) -> Any:
    return handler(dict(value))


FrozenMap = Annotated[Mapping[K, V], AfterValidator(_read_only), WrapSerializer(_as_dict)]
"""A mapping field that cannot be changed after validation. ``frozen`` alone stops
attribute assignment, not changes inside a dict. Serializes as a plain object."""


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""Timezone-aware timestamp normalized to UTC. Naive datetimes are rejected."""

RecordId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
"""Opaque record identifier. The Store decides its concrete form."""

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")]
"""Lowercase, hyphenated, human-readable identifier (customers, criteria)."""

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
"""Content digest in ``sha256:<hex>`` form."""

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


def sha256_digest(content: str | bytes) -> str:
    """Return the ``sha256:<hex>`` digest of ``content`` (str is UTF-8 encoded)."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return "sha256:" + hashlib.sha256(data).hexdigest()


ActorKind = Literal["human", "rule", "model", "service"]


class Actor(Contract):
    """Who or what performed an action.

    ``rule`` is deterministic logic, ``model`` is a generative or evaluative
    model, ``service`` is harness or integration code (an acquisition adapter,
    a delivery adapter), and ``human`` is a person.
    """

    kind: ActorKind
    id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None


class StrategyRef(Contract):
    """The exact approved strategy revision a record was produced under."""

    commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    path: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    digest: Digest
