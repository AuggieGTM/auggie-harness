"""Deliver stage: check authorization, then hand off, simulated or through one platform's adapter.

Each sending platform has its own thin adapter (ADR 0020). How a real send is
recorded is ADR 0022.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from auggie.contracts import (
    Actor,
    Artifact,
    ArtifactRef,
    CustomerScope,
    DeliveryTarget,
    HumanApproval,
    ensure_in_scope,
)
from auggie.contracts.approval import DeliveryPlatform

SIMULATED_DELIVERY = Actor(kind="service", id="simulated-delivery", version="0")

LIVE_PLATFORMS = frozenset({"gmail"})
"""Platforms the gate lets a live scope deliver on: those with an adapter."""


class DeliveryBlocked(Exception):
    """Delivery is not authorized. Nothing was handed off."""


class SendRefused(Exception):
    """Nothing was sent: the platform refused the message, or the adapter stopped before it.

    ``reason`` says why in words that hold no credential or message content.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SendUnknown(Exception):
    """The message may have gone out without the platform confirming it. Never retried."""


class Sender(Protocol):
    """One platform's delivery adapter.

    ``send`` returns the platform's receipt. It raises ``SendRefused`` only when
    it knows nothing went out. ``SendUnknown``, or any other exception, leaves
    the send unknown.
    """

    @property
    def platform(self) -> DeliveryPlatform: ...

    @property
    def scope(self) -> CustomerScope:
        """The scope whose account and credential the sender uses."""
        ...

    @property
    def actor(self) -> Actor: ...

    def send(self, *, to: str, subject: str, body: str) -> str: ...


def split_message(content: str) -> tuple[str, str]:
    """A draft's subject and body: a first line ``Subject: ...``, a blank line, then the body.

    Raises ``DeliveryBlocked`` for any other shape, so a draft is sent exactly
    as it was approved or not at all.
    """
    if "\r" in content:
        raise DeliveryBlocked("the draft has CR line endings; revise it to plain line breaks")
    first, separator, body = content.partition("\n\n")
    if not separator or not first.startswith("Subject: ") or "\n" in first:
        raise DeliveryBlocked("the draft does not start with a Subject line and a blank line")
    subject = first.removeprefix("Subject: ")
    if not subject.strip() or "\r" in subject:
        raise DeliveryBlocked("the draft's Subject line is empty or malformed")
    if not body.strip():
        raise DeliveryBlocked("the draft has no body")
    return subject, body


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
    customer. Raises ``DeliveryBlocked`` unless the scope delivers on the
    target's platform and an adapter exists for it, the requested version is
    the latest one, no reviewer rejected that version, and both a content
    approval and a delivery approval for this recipient on that platform apply
    to exactly that version now.
    """
    approvals = list(approvals)
    ensure_in_scope(scope, latest)
    for approval in approvals:
        ensure_in_scope(scope, approval)
    mode, platform = scope.execution.delivery, scope.execution.platform
    if mode == "disabled":
        raise DeliveryBlocked(f"scope {scope.customer_id!r} allows 'disabled' delivery")
    if mode == "live" and platform == "simulated":
        raise DeliveryBlocked(
            f"scope {scope.customer_id!r} is live but names no platform: set execution.platform"
        )
    if target.platform != platform:
        raise DeliveryBlocked(
            f"scope {scope.customer_id!r} delivers on {platform!r}, not {target.platform!r}"
        )
    if mode == "live" and platform not in LIVE_PLATFORMS:
        raise DeliveryBlocked(f"no {platform} delivery adapter exists")
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
