"""Careers snapshots: what one company's job listing showed on one read (ADR 0024).

A snapshot is evidence, recorded as the listing service stated it. The roles'
families, levels and patterns are never stored here: rules apply the seller's
vocabulary when they count.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, StringConstraints, model_validator

from auggie.contracts.common import (
    Actor,
    Contract,
    Digest,
    RecordId,
    Slug,
    Text,
    UtcDatetime,
    sha256_digest,
)

ListingService = Literal["lever", "greenhouse", "ashby"]
ReadStatus = Literal["complete", "partial", "inconsistent", "failed"]

Board = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
"""The company's name on the listing service, as the service's own URLs spell it."""

Short = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class CareersListing(Contract):
    """A company's listing, named in the prospect file and confirmed by the person."""

    service: ListingService
    board: Board
    url: HttpUrl
    """The public page a person opens to see the listing."""


class OpenRole(Contract):
    """One open role, as the listing states it. ``posted_at`` only when the service documents a
    field for when the role was posted (ADR 0024)."""

    role_id: Short
    title: Short
    team: Short | None = None
    location: Short | None = None
    url: HttpUrl
    posted_at: UtcDatetime | None = None


def roles_digest(roles: Iterable[OpenRole]) -> str:
    """The content hash of a snapshot's roles, in the order the listing gave them."""
    data = [role.model_dump(mode="json") for role in roles]
    return sha256_digest(json.dumps(data, sort_keys=True, separators=(",", ":")))


class CareersSnapshot(Contract):
    """One read attempt of one account's listing, kept whatever its outcome."""

    schema_version: Literal[1] = 1
    snapshot_id: RecordId
    customer_id: Slug
    account_id: RecordId
    listing: CareersListing
    read_at: UtcDatetime
    status: ReadStatus
    failure_reason: Text | None = None
    roles: tuple[OpenRole, ...] = Field(default=(), max_length=2000)
    content_hash: Digest
    acquired_via: Actor

    @model_validator(mode="after")
    def _consistent(self) -> CareersSnapshot:
        if self.status == "failed":
            if self.roles or self.failure_reason is None:
                raise ValueError("a failed read has a reason and no roles")
        elif self.failure_reason is not None:
            raise ValueError("only a failed read has a failure reason")
        ids = [role.role_id for role in self.roles]
        if len(set(ids)) != len(ids):
            raise ValueError("a snapshot lists each role ID once")
        if roles_digest(self.roles) != self.content_hash:
            raise ValueError("content_hash does not match the roles")
        return self

    @property
    def listing_key(self) -> str:
        """The account and listing a baseline must share with the current reading."""
        return f"{self.account_id}/{self.listing.service}:{self.listing.board}"
