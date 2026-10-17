"""Artifacts: one exact version of a brief or draft, with what it was composed from (ADR 0014)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from auggie.contracts.approval import ArtifactRef
from auggie.contracts.common import (
    Actor,
    Contract,
    Digest,
    RecordId,
    Slug,
    StrategyRef,
    UtcDatetime,
    sha256_digest,
)

Content = Annotated[str, StringConstraints(min_length=1, max_length=50_000)]
"""Exact artifact text. Never stripped or normalized: its bytes are what approvals bind to."""


class Artifact(Contract):
    """One version of a brief or draft.

    An edit is a new version with the same ``artifact_id``, a new hash, and
    ``supersedes`` naming the hash it replaces. Approvals bind to one hash, so
    an edit voids every earlier approval of the artifact.
    """

    schema_version: Literal[1] = 1
    customer_id: Slug
    ref: ArtifactRef
    media_type: Literal["text/markdown"] = "text/markdown"
    content: Content
    account_id: RecordId
    recipient_id: RecordId | None = None
    qualification_id: RecordId
    evidence_ids: Annotated[tuple[RecordId, ...], Field(min_length=1)]
    strategy: StrategyRef
    composed_by: Actor
    composed_at: UtcDatetime
    supersedes: Digest | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Artifact:
        if sha256_digest(self.content) != self.ref.content_hash:
            raise ValueError("content does not match ref.content_hash")
        if (self.ref.kind == "draft") != (self.recipient_id is not None):
            raise ValueError("a draft needs a recipient, and only drafts have one")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must not repeat")
        if self.supersedes == self.ref.content_hash:
            raise ValueError("a version cannot supersede itself")
        return self
