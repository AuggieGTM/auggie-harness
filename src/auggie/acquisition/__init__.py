"""Observe stage: acquire sources about an account and record every attempt as evidence."""

from __future__ import annotations

from typing import Protocol

from pydantic import HttpUrl

from auggie.contracts import Actor, SourceEvidence, SubjectRef


class Acquisition(Protocol):
    """An acquisition adapter. Fixture replay now; Treg later (ADR 0008).

    A failed retrieval is still returned as evidence, with its reason and no
    content, so the gap stays visible.
    """

    @property
    def actor(self) -> Actor: ...

    def acquire(
        self, *, evidence_id: str, customer_id: str, subject: SubjectRef, url: HttpUrl
    ) -> SourceEvidence: ...
