"""Acquisition adapters that replay recorded responses. They never touch the network."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Annotated

from pydantic import Field, HttpUrl, JsonValue

from auggie.acquisition import Acquired
from auggie.acquisition.careers import snapshot_of
from auggie.contracts import (
    Actor,
    CareersListing,
    CareersSnapshot,
    Contract,
    SourceEvidence,
    SubjectRef,
    sha256_digest,
)
from auggie.contracts.common import Text, UtcDatetime
from auggie.contracts.evidence import RetrievalStatus
from auggie.https import HttpResponse


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


class RecordedListing(Contract):
    """What a listing service replied when a listing was read, and when (ADR 0024).

    ``body`` is the reply's JSON, or a string sent as-is.
    """

    listing: CareersListing
    read_at: UtcDatetime
    status: Annotated[int, Field(ge=100, le=599)]
    body: JsonValue


class RecordedListings:
    """Turns recorded listing replies into snapshots with the readers' own parsers. Each
    listing replays its newest recorded reply; older ones stand for earlier reads."""

    actor = Actor(kind="service", id="fixture-careers", version="0")

    def __init__(self, recordings: Iterable[RecordedListing]) -> None:
        self._recordings: dict[CareersListing, RecordedListing] = {}
        for recording in recordings:
            known = self._recordings.get(recording.listing)
            if known is not None and known.read_at == recording.read_at:
                raise ValueError(f"two recordings of {recording.listing.url} at one time")
            if known is None or known.read_at < recording.read_at:
                self._recordings[recording.listing] = recording

    def read(
        self, *, listing: CareersListing, customer_id: str, account_id: str, snapshot_id: str
    ) -> CareersSnapshot:
        recording = self._recordings.get(listing)
        if recording is None:
            raise LookupError(f"no recorded reply for {listing.url}")
        body = recording.body
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        return snapshot_of(
            customer_id=customer_id,
            listing=listing,
            account_id=account_id,
            snapshot_id=snapshot_id,
            read_at=recording.read_at,
            response=HttpResponse(recording.status, {}, raw),
            actor=self.actor,
        )
