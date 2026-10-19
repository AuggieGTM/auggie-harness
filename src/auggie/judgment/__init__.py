"""Judge stage: qualify an account against an exact strategy revision, citing evidence.

Engines sit behind ``Qualifier`` so they can be swapped or combined.
Judgment uses deterministic rules (ADR 0031).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from auggie.acquisition.validation import Verdict
from auggie.contracts import Actor, CareersSnapshot, QualificationDecision
from auggie.contracts.strategy import Hiring
from auggie.signals.tally import Reading, Role, choose_baseline
from auggie.strategy import LoadedStrategy


def reading(snapshot: CareersSnapshot, order: int = 0) -> Reading:
    """A snapshot as the tally counts it."""
    return Reading(
        reading_id=snapshot.snapshot_id,
        listing=snapshot.listing_key,
        read_at=snapshot.read_at,
        status=snapshot.status,
        roles=tuple(Role(r.role_id, r.title, str(r.url), r.posted_at) for r in snapshot.roles),
        order=order,
    )


@dataclass(frozen=True)
class ListingReadings:
    """This run's read of an account's listing, and the account's earlier snapshots in the
    order the Store recorded them (ADR 0024)."""

    current: CareersSnapshot
    earlier: tuple[CareersSnapshot, ...] = ()

    def readings(self) -> tuple[Reading, tuple[Reading, ...]]:
        """The current reading, and the earlier ones, ordered as recorded."""
        earlier = tuple(reading(s, n) for n, s in enumerate(self.earlier))
        return reading(self.current, len(earlier)), earlier

    def baseline(self, hiring: Hiring) -> str | None:
        """The snapshot new and more openings compare this run's read with, if any."""
        current, earlier = self.readings()
        chosen = choose_baseline(current, earlier, hiring)
        return chosen.reading_id if chosen is not None else None


class Qualifier(Protocol):
    @property
    def actor(self) -> Actor: ...

    def qualify(
        self,
        *,
        decision_id: str,
        customer_id: str,
        account_id: str,
        strategy: LoadedStrategy,
        sources: Mapping[str, Verdict],
        at: datetime,
        listing: ListingReadings | None = None,
    ) -> QualificationDecision:
        """Judge from ``sources`` (one validation verdict per strategy source kind) and the
        account's ``listing``, if it was read.

        Only admitted evidence and usable snapshots may support a finding. A source that
        was not admitted, or a listing that was not read, leaves its criteria unknown; it is
        never a negative finding.
        """
        ...
