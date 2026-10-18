"""Artifacts: one exact version of a brief or draft, with what it was composed from (ADR 0014)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

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

    A version cites the page evidence (``evidence_ids``) and careers snapshots
    (``snapshot_ids``, ADR 0024) it was composed from: at least one of either.
    """

    schema_version: Literal[1] = 1
    customer_id: Slug
    ref: ArtifactRef
    media_type: Literal["text/markdown"] = "text/markdown"
    content: Content
    account_id: RecordId
    recipient_id: RecordId | None = None
    qualification_id: RecordId
    evidence_ids: tuple[RecordId, ...] = ()
    snapshot_ids: tuple[RecordId, ...] = ()
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
        if not self.evidence_ids and not self.snapshot_ids:
            raise ValueError("an artifact cites the evidence or snapshots it was composed from")
        for name, ids in (("evidence_ids", self.evidence_ids), ("snapshot_ids", self.snapshot_ids)):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} must not repeat")
        if self.supersedes == self.ref.content_hash:
            raise ValueError("a version cannot supersede itself")
        return self
