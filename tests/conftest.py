from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from auggie.contracts import (
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
