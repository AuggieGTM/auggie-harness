"""Deliver stage: check authorization, then hand off. Only simulated hand-off exists (ADR 0006)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from auggie.contracts import (
    Actor,
    Artifact,
    ArtifactRef,
    CustomerScope,
    DeliveryTarget,
    HumanApproval,
    ensure_in_scope,
)

SIMULATED_DELIVERY = Actor(kind="service", id="simulated-delivery", version="0")


class DeliveryBlocked(Exception):
    """Delivery is not authorized. Nothing was handed off."""


def authorize_delivery(
    *,
    scope: CustomerScope,
    requested: ArtifactRef,
    latest: Artifact,
    target: DeliveryTarget,
    approvals: Iterable[HumanApproval],
    at: datetime,
) -> HumanApproval:
    """Return the delivery approval that permits sending ``requested`` to ``target`` at ``at``.

    Raises ``ScopeViolation`` if the draft or any approval belongs to another
    customer. Raises ``DeliveryBlocked`` unless the scope allows simulated
    delivery, the requested version is the latest one, no reviewer rejected
    that version, and both a content approval and a delivery approval for
    this recipient apply to exactly that version now.
    """
    approvals = list(approvals)
    ensure_in_scope(scope, latest)
    for approval in approvals:
        ensure_in_scope(scope, approval)
    if scope.execution.delivery != "simulated" or target.platform != "simulated":
        raise DeliveryBlocked(
            f"scope {scope.customer_id!r} allows {scope.execution.delivery!r} delivery and "
            f"only simulated delivery exists"
        )
    if requested.kind != "draft":
        raise DeliveryBlocked("only drafts are delivered")
    if latest.ref != requested:
        raise DeliveryBlocked(
            f"{requested.artifact_id} {requested.content_hash} was superseded by "
            f"{latest.ref.content_hash}; earlier approvals no longer apply"
        )
    if target.recipient_id != latest.recipient_id:
        raise DeliveryBlocked(f"the draft is for {latest.recipient_id}, not {target.recipient_id}")
    of_this_version = [a for a in approvals if a.artifact == requested and a.decided_at <= at]
    if any(a.decision != "approved" for a in of_this_version):
        raise DeliveryBlocked("a reviewer rejected or asked for changes to this version")
    if not any(a.authorizes(artifact=requested, scope="content", at=at) for a in of_this_version):
        raise DeliveryBlocked("no content approval of this version applies now")
    for approval in of_this_version:
        if approval.authorizes(artifact=requested, scope="delivery", target=target, at=at):
            return approval
    raise DeliveryBlocked(
        f"no delivery approval of this version for {target.platform}:{target.recipient_id} "
        f"applies now"
    )
