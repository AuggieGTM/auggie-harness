"""An acquisition adapter that replays recorded responses. It never touches the network."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import HttpUrl

from auggie.acquisition import Acquired
from auggie.contracts import Actor, Contract, SourceEvidence, SubjectRef, sha256_digest
from auggie.contracts.common import Text, UtcDatetime
from auggie.contracts.evidence import RetrievalStatus


class RecordedResponse(Contract):
    """What a source returned when it was fetched, and when."""

    url: HttpUrl
    status: RetrievalStatus
    observed_at: UtcDatetime
    published_at: UtcDatetime | None = None
    excerpt: Text | None = None
    failure_reason: Text | None = None


class FixtureAcquisition:
    """Turns recorded responses into evidence. A recording's snapshot is its excerpt."""

    actor = Actor(kind="service", id="fixture-acquisition", version="0")

    def __init__(self, responses: Iterable[RecordedResponse]) -> None:
        self._responses = {str(response.url): response for response in responses}

    def acquire(
        self, *, evidence_id: str, customer_id: str, subject: SubjectRef, url: HttpUrl
    ) -> Acquired:
        response = self._responses.get(str(url))
        if response is None:
            raise LookupError(f"no recorded response for {url}")
        evidence = SourceEvidence(
            evidence_id=evidence_id,
            customer_id=customer_id,
            subject=subject,
            source_url=response.url,
            acquired_via=self.actor,
            status=response.status,
            observed_at=response.observed_at,
            published_at=response.published_at,
            excerpt=response.excerpt,
            content_hash=sha256_digest(response.excerpt) if response.excerpt else None,
            failure_reason=response.failure_reason,
        )
        return Acquired(evidence)
