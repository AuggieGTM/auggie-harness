from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auggie.contracts import SourceEvidence
from tests.conftest import Scenario, variant


@pytest.fixture
def retrieved(scenario: Scenario) -> SourceEvidence:
    return scenario.evidence_by_id("ev-northstar-careers")


@pytest.fixture
def failed(scenario: Scenario) -> SourceEvidence:
    return scenario.evidence_by_id("ev-northstar-news")


def test_only_retrieved_evidence_is_usable(
    retrieved: SourceEvidence, failed: SourceEvidence
) -> None:
    assert retrieved.usable
    assert not failed.usable


def test_retrieved_evidence_needs_excerpt_and_hash(retrieved: SourceEvidence) -> None:
    with pytest.raises(ValidationError, match="needs an excerpt and a content_hash"):
        SourceEvidence.model_validate(variant(retrieved, excerpt=None))


def test_failed_evidence_cannot_carry_content(retrieved: SourceEvidence) -> None:
    data = variant(retrieved, status="failed", failure_reason="Timed out.")
    with pytest.raises(ValidationError, match="failed evidence cannot carry content"):
        SourceEvidence.model_validate(data)


def test_failed_evidence_must_say_why(failed: SourceEvidence) -> None:
    with pytest.raises(ValidationError, match="must record a failure_reason"):
        SourceEvidence.model_validate(variant(failed, failure_reason=None))


def test_models_cannot_produce_evidence(retrieved: SourceEvidence) -> None:
    data = variant(retrieved, acquired_via={"kind": "model", "id": "any-model"})
    with pytest.raises(ValidationError, match="not a rule or model"):
        SourceEvidence.model_validate(data)


def test_publication_cannot_follow_observation(retrieved: SourceEvidence) -> None:
    data = variant(retrieved, published_at="2026-10-15T00:00:00Z")
    with pytest.raises(ValidationError, match="published_at cannot be later"):
        SourceEvidence.model_validate(data)


def test_naive_timestamps_are_rejected(retrieved: SourceEvidence) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        SourceEvidence.model_validate(variant(retrieved, observed_at="2026-10-14T16:00:00"))


def test_timestamps_are_normalized_to_utc(retrieved: SourceEvidence) -> None:
    record = SourceEvidence.model_validate(
        variant(retrieved, observed_at="2026-10-14T12:00:00-04:00")
    )
    assert record.observed_at == datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
    assert record.observed_at.tzinfo is UTC
