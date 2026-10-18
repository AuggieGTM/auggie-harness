"""Human approvals: a person's decision on one exact artifact version, for one purpose."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import model_validator

from auggie.contracts.common import Actor, Contract, Digest, RecordId, Slug, Text, UtcDatetime

ArtifactKind = Literal["brief", "draft"]
ApprovalScope = Literal["content", "delivery"]
ApprovalDecision = Literal["approved", "rejected", "changes_requested"]
DeliveryPlatform = Literal["emailbison", "instantly", "gmail", "simulated"]


class ArtifactRef(Contract):
    """An artifact pinned to its exact content. Any edit changes the hash."""

    artifact_id: RecordId
    kind: ArtifactKind
    content_hash: Digest


class DeliveryTarget(Contract):
    """Where an approved draft may be sent. Recipients are records, not raw addresses."""

    platform: DeliveryPlatform
    recipient_id: RecordId


class HumanApproval(Contract):
    """A reviewer's decision, bound to an artifact hash and a scope.

    Approving content does not authorize delivery. A delivery approval names
    its target. Editing the artifact invalidates every earlier approval of it.
    """

    schema_version: Literal[1] = 1
    approval_id: RecordId
    customer_id: Slug
    artifact: ArtifactRef
    scope: ApprovalScope
    target: DeliveryTarget | None = None
    decision: ApprovalDecision
    reviewer: Actor
    decided_at: UtcDatetime
    expires_at: UtcDatetime | None = None
    revoked_at: UtcDatetime | None = None
    note: Text | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> HumanApproval:
        if self.reviewer.kind != "human":
            raise ValueError("approvals must come from a human reviewer")
        if (self.scope == "delivery") != (self.target is not None):
            raise ValueError("a delivery approval needs a target, and only delivery has one")
        if self.scope == "delivery" and self.artifact.kind != "draft":
            raise ValueError("only drafts can be approved for delivery")
        if self.expires_at is not None and self.expires_at <= self.decided_at:
            raise ValueError("expires_at must be later than decided_at")
        if self.revoked_at is not None and self.revoked_at < self.decided_at:
            raise ValueError("revoked_at cannot be earlier than decided_at")
        return self

    def authorizes(
        self,
        *,
        artifact: ArtifactRef,
        scope: ApprovalScope,
        at: datetime,
        target: DeliveryTarget | None = None,
    ) -> bool:
        """Whether this approval permits ``scope`` on exactly ``artifact`` at time ``at``."""
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        return (
            self.decision == "approved"
            and self.scope == scope
            and self.artifact == artifact
            and self.target == target
            and (self.revoked_at is None or at < self.revoked_at)
            and (self.expires_at is None or at < self.expires_at)
            and at >= self.decided_at
        )
