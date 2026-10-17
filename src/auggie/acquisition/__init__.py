"""Observe stage: acquire sources about an account and record every attempt as evidence."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple, Protocol

from pydantic import HttpUrl

from auggie.contracts import Actor, SourceEvidence, SubjectRef
from auggie.contracts.events import AttributeValue


class Acquired(NamedTuple):
    """What an attempt produced: the evidence, and operational detail for its event.

    ``attributes`` holds facts such as a provider call ID or the settled cost.
    Never credentials, and never the source's content.
    """

    evidence: SourceEvidence
    attributes: Mapping[str, AttributeValue] = MappingProxyType({})


class Acquisition(Protocol):
    """An acquisition adapter. Fixture replay now; Treg later (ADR 0008).

    A failed retrieval is still returned as evidence, with its reason and no
    content, so the gap stays visible.
    """

    @property
    def actor(self) -> Actor: ...

    def acquire(
        self, *, evidence_id: str, customer_id: str, subject: SubjectRef, url: HttpUrl
    ) -> Acquired: ...
