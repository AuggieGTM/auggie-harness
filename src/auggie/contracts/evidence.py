"""Source evidence: what a source said, when, and how it was obtained."""

from __future__ import annotations

from typing import Literal

from pydantic import HttpUrl, model_validator

from auggie.contracts.common import Actor, Contract, Digest, RecordId, Slug, Text, UtcDatetime

RetrievalStatus = Literal["retrieved", "not_found", "failed", "blocked"]


class SubjectRef(Contract):
    """The account or person a piece of evidence is about."""

    kind: Literal["account", "person"]
    id: RecordId


class SourceEvidence(Contract):
    """A dated, attributable observation from a source. Never an inference.

    Evidence records what a source said. Interpretation belongs in a
    ``Finding``. A retrieval that failed is still recorded, so the gap is
    visible, but it carries no content and must not be read as a negative
    finding.
    """

    schema_version: Literal[1] = 1
    evidence_id: RecordId
    customer_id: Slug
    subject: SubjectRef
    source_url: HttpUrl
    final_url: HttpUrl | None = None
    acquired_via: Actor
    provider_request_id: str | None = None
    status: RetrievalStatus
    observed_at: UtcDatetime
    published_at: UtcDatetime | None = None
    excerpt: Text | None = None
    content_hash: Digest | None = None
    failure_reason: Text | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> SourceEvidence:
        if self.acquired_via.kind not in ("service", "human"):
            raise ValueError("evidence is acquired by a service or a human, not a rule or model")
        if self.status == "retrieved":
            if self.excerpt is None or self.content_hash is None:
                raise ValueError("retrieved evidence needs an excerpt and a content_hash")
            if self.failure_reason is not None:
                raise ValueError("retrieved evidence cannot carry a failure_reason")
        else:
            if self.excerpt is not None or self.content_hash is not None:
                raise ValueError(f"{self.status} evidence cannot carry content")
            if self.failure_reason is None:
                raise ValueError(f"{self.status} evidence must record a failure_reason")
        if self.published_at is not None and self.published_at > self.observed_at:
            raise ValueError("published_at cannot be later than observed_at")
        return self

    @property
    def usable(self) -> bool:
        """Whether this evidence may support a finding."""
        return self.status == "retrieved"
