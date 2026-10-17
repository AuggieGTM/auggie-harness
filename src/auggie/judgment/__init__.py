"""Judge stage: qualify an account against an exact strategy revision, citing evidence.

Engines sit behind ``Qualifier`` so they can be swapped or combined
(ADR 0009, Open). Only deterministic rules exist so far.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from auggie.acquisition.validation import Verdict
from auggie.contracts import Actor, QualificationDecision
from auggie.strategy import LoadedStrategy


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
    ) -> QualificationDecision:
        """Judge from ``sources`` (one validation verdict per strategy source kind).

        Only admitted evidence may support a finding. A source that was not
        admitted leaves its criteria unknown; it is never a negative finding.
        """
        ...
