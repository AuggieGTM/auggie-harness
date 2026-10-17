"""A run's spend limit (ADR 0016): reserve each paid call's ceiling first, settle to its charge.

Amounts are integers in micro-USD (millionths of a US dollar), as Treg reports them.
"""

from __future__ import annotations

from dataclasses import dataclass

from auggie.contracts import CustomerScope


class SpendRefused(Exception):
    """A paid call could take the run past its limit. Nothing was sent."""


@dataclass
class Reservation:
    ceiling_micro_usd: int
    charged_micro_usd: int | None = None

    @property
    def settled(self) -> bool:
        return self.charged_micro_usd is not None


class RunBudget:
    """Tracks one run's paid calls. Lives in the process that runs them; it is not a scheduler."""

    def __init__(self, limit_micro_usd: int) -> None:
        if limit_micro_usd < 0:
            raise ValueError("a spend limit cannot be negative")
        self._limit = limit_micro_usd
        self._reservations: list[Reservation] = []

    @classmethod
    def for_scope(cls, scope: CustomerScope) -> RunBudget:
        return cls(scope.execution.max_run_spend_micro_usd)

    @property
    def limit_micro_usd(self) -> int:
        return self._limit

    @property
    def committed_micro_usd(self) -> int:
        """Settled charges, plus the full ceiling of every call not yet settled."""
        return sum(
            r.ceiling_micro_usd if r.charged_micro_usd is None else r.charged_micro_usd
            for r in self._reservations
        )

    @property
    def spent_micro_usd(self) -> int:
        return sum(r.charged_micro_usd or 0 for r in self._reservations)

    def reserve(self, ceiling_micro_usd: int) -> Reservation:
        """Hold ``ceiling_micro_usd`` for one call, or refuse if the run could exceed its limit."""
        if ceiling_micro_usd <= 0:
            raise ValueError("a paid call needs a positive ceiling")
        if self.committed_micro_usd + ceiling_micro_usd > self._limit:
            raise SpendRefused(
                f"a call costing up to {ceiling_micro_usd} micro-USD could take this run past "
                f"its limit of {self._limit} ({self.committed_micro_usd} already committed)"
            )
        reservation = Reservation(ceiling_micro_usd)
        self._reservations.append(reservation)
        return reservation

    def settle(self, reservation: Reservation, charged_micro_usd: int | None) -> int:
        """Record what the call cost. An unreported charge counts as the full ceiling."""
        if not any(r is reservation for r in self._reservations):
            raise ValueError("this reservation belongs to another budget")
        if reservation.settled:
            raise ValueError("this reservation is already settled")
        if charged_micro_usd is not None and charged_micro_usd < 0:
            raise ValueError("a charge cannot be negative")
        charged = reservation.ceiling_micro_usd if charged_micro_usd is None else charged_micro_usd
        reservation.charged_micro_usd = charged
        return charged
