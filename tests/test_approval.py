from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auggie.contracts import ArtifactRef, DeliveryTarget, HumanApproval, sha256_digest
from tests.conftest import Scenario, variant

AFTER_REVIEW = datetime(2026, 10, 15, 16, 0, tzinfo=UTC)


@pytest.fixture
def content(scenario: Scenario) -> HumanApproval:
    return scenario.approval_by_id("apr-northstar-content")


@pytest.fixture
def delivery(scenario: Scenario) -> HumanApproval:
    return scenario.approval_by_id("apr-northstar-delivery")


@pytest.fixture
def target(delivery: HumanApproval) -> DeliveryTarget:
    assert delivery.target is not None
    return delivery.target


def test_reviewer_must_be_human(content: HumanApproval) -> None:
    data = variant(content, reviewer={"kind": "model", "id": "self-reviewer"})
    with pytest.raises(ValidationError, match="must come from a human reviewer"):
        HumanApproval.model_validate(data)


def test_delivery_approval_requires_target(delivery: HumanApproval) -> None:
    with pytest.raises(ValidationError, match="delivery approval needs a target"):
        HumanApproval.model_validate(variant(delivery, target=None))


def test_content_approval_has_no_target(content: HumanApproval, target: DeliveryTarget) -> None:
    data = variant(content, target=target.model_dump(mode="json"))
    with pytest.raises(ValidationError, match="only delivery has one"):
        HumanApproval.model_validate(data)


def test_only_drafts_can_be_approved_for_delivery(delivery: HumanApproval) -> None:
    brief = variant(delivery.artifact, kind="brief")
    with pytest.raises(ValidationError, match="only drafts can be approved for delivery"):
        HumanApproval.model_validate(variant(delivery, artifact=brief))


def test_expiry_must_follow_decision(delivery: HumanApproval) -> None:
    data = variant(delivery, expires_at="2026-10-15T15:00:00Z")
    with pytest.raises(ValidationError, match="expires_at must be later"):
        HumanApproval.model_validate(data)


def test_approval_authorizes_the_exact_artifact(content: HumanApproval) -> None:
    assert content.authorizes(artifact=content.artifact, scope="content", at=AFTER_REVIEW)


def test_editing_the_artifact_invalidates_approval(content: HumanApproval) -> None:
    edited = ArtifactRef.model_validate(
        variant(content.artifact, content_hash=sha256_digest("edited draft"))
    )
    assert not content.authorizes(artifact=edited, scope="content", at=AFTER_REVIEW)


def test_content_approval_does_not_authorize_delivery(
    content: HumanApproval, target: DeliveryTarget
) -> None:
    assert not content.authorizes(
        artifact=content.artifact, scope="delivery", target=target, at=AFTER_REVIEW
    )


def test_delivery_approval_is_bound_to_its_target(
    delivery: HumanApproval, target: DeliveryTarget
) -> None:
    assert delivery.authorizes(
        artifact=delivery.artifact, scope="delivery", target=target, at=AFTER_REVIEW
    )
    elsewhere = DeliveryTarget(platform="simulated", recipient_id="per-someone-else")
    assert not delivery.authorizes(
        artifact=delivery.artifact, scope="delivery", target=elsewhere, at=AFTER_REVIEW
    )


def test_expired_approval_does_not_authorize(
    delivery: HumanApproval, target: DeliveryTarget
) -> None:
    later = datetime(2026, 10, 23, tzinfo=UTC)
    assert not delivery.authorizes(
        artifact=delivery.artifact, scope="delivery", target=target, at=later
    )


def test_revoked_approval_does_not_authorize(content: HumanApproval) -> None:
    revoked = HumanApproval.model_validate(variant(content, revoked_at="2026-10-15T15:30:00Z"))
    assert not revoked.authorizes(artifact=content.artifact, scope="content", at=AFTER_REVIEW)


def test_approval_does_not_apply_before_it_was_given(content: HumanApproval) -> None:
    before = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    assert not content.authorizes(artifact=content.artifact, scope="content", at=before)


def test_rejection_never_authorizes(content: HumanApproval) -> None:
    rejected = HumanApproval.model_validate(variant(content, decision="rejected"))
    assert not rejected.authorizes(artifact=content.artifact, scope="content", at=AFTER_REVIEW)


def test_authorization_time_must_be_aware(content: HumanApproval) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        content.authorizes(artifact=content.artifact, scope="content", at=datetime(2026, 10, 16))
