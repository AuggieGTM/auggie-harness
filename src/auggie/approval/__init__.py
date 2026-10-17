"""Approve stage: turn a person's decision about the version they saw into a HumanApproval."""

from __future__ import annotations

from auggie.contracts import Actor, Artifact, Contract, DeliveryTarget, HumanApproval
from auggie.contracts.approval import ApprovalDecision, ApprovalScope, DeliveryPlatform
from auggie.contracts.common import Text, UtcDatetime


class ReviewDecision(Contract):
    """What a reviewer decided. ``platform`` is set for delivery decisions only."""

    scope: ApprovalScope
    decision: ApprovalDecision
    reviewer: Actor
    decided_at: UtcDatetime
    expires_at: UtcDatetime | None = None
    note: Text | None = None
    platform: DeliveryPlatform | None = None


def approval_for(shown: Artifact, review: ReviewDecision, *, approval_id: str) -> HumanApproval:
    """Bind ``review`` to exactly the version that was shown, and to its recipient."""
    if (review.scope == "delivery") != (review.platform is not None):
        raise ValueError("a delivery decision names a platform, and only delivery does")
    target = None
    if review.platform is not None:
        if shown.recipient_id is None:
            raise ValueError("only a draft with a recipient can be approved for delivery")
        target = DeliveryTarget(platform=review.platform, recipient_id=shown.recipient_id)
    return HumanApproval(
        approval_id=approval_id,
        customer_id=shown.customer_id,
        artifact=shown.ref,
        scope=review.scope,
        target=target,
        decision=review.decision,
        reviewer=review.reviewer,
        decided_at=review.decided_at,
        expires_at=review.expires_at,
        note=review.note,
    )
