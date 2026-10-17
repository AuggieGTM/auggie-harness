from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from auggie.contracts import (
    Actor,
    Artifact,
    ArtifactRef,
    Contract,
    CustomerScope,
    HumanApproval,
    OperationalEvent,
    QualificationDecision,
    SourceEvidence,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "synthetic"
NORTHSTAR = EXAMPLES / "northstar-msp"


class DraftFixture(Contract):
    artifact: ArtifactRef
    file: str
    recipient_id: str


class Scenario(Contract):
    """Fixture format for a synthetic walk through the loop. Test-only."""

    note: str
    scope: CustomerScope
    evidence: tuple[SourceEvidence, ...]
    qualification: QualificationDecision
    draft: DraftFixture
    approvals: tuple[HumanApproval, ...]
    events: tuple[OperationalEvent, ...]

    def evidence_by_id(self, evidence_id: str) -> SourceEvidence:
        return next(e for e in self.evidence if e.evidence_id == evidence_id)

    def approval_by_id(self, approval_id: str) -> HumanApproval:
        return next(a for a in self.approvals if a.approval_id == approval_id)


def variant(model: BaseModel, **changes: Any) -> dict[str, Any]:
    """JSON-mode dump of ``model`` with top-level ``changes`` applied, for re-validation."""
    data = model.model_dump(mode="json", exclude_none=True)
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


@pytest.fixture(scope="session")
def scenario() -> Scenario:
    return Scenario.model_validate_json((NORTHSTAR / "scenario.json").read_text(encoding="utf-8"))


@pytest.fixture
def draft(scenario: Scenario) -> Artifact:
    """The Northstar draft as an ``Artifact``, built from the fixture's own records."""
    return Artifact(
        customer_id=scenario.scope.customer_id,
        ref=scenario.draft.artifact,
        content=(NORTHSTAR / scenario.draft.file).read_text(encoding="utf-8"),
        account_id=scenario.qualification.account_id,
        recipient_id=scenario.draft.recipient_id,
        qualification_id=scenario.qualification.decision_id,
        evidence_ids=("ev-northstar-careers", "ev-northstar-technology"),
        strategy=scenario.qualification.strategy,
        composed_by=Actor(kind="service", id="fixture-composer", version="0"),
        composed_at=datetime(2026, 10, 14, 16, 10, tzinfo=UTC),
    )
