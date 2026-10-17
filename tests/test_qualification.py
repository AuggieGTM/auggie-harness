from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from auggie.contracts import CitationError, Finding, QualificationDecision, verify_citations
from tests.conftest import Scenario, variant

CAREERS = "ev-northstar-careers"


def finding(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "criterion": "active-it-hiring",
        "assessment": "meets",
        "basis": "source_fact",
        "evidence_ids": [CAREERS],
        "rationale": "An open IT role.",
    }
    data.update(changes)
    return data


def decision(scenario: Scenario, **changes: Any) -> QualificationDecision:
    return QualificationDecision.model_validate(variant(scenario.qualification, **changes))


def test_source_facts_must_cite_evidence() -> None:
    with pytest.raises(ValidationError, match="source_fact finding must cite evidence"):
        Finding.model_validate(finding(evidence_ids=[]))


def test_inference_without_evidence_cannot_settle_a_criterion() -> None:
    with pytest.raises(ValidationError, match="needs evidence"):
        Finding.model_validate(finding(basis="inference", evidence_ids=[]))


def test_hypothesis_cannot_settle_a_criterion() -> None:
    with pytest.raises(ValidationError, match="hypothesis cannot settle"):
        Finding.model_validate(finding(basis="hypothesis"))


def test_unknown_hypothesis_needs_no_evidence() -> None:
    Finding.model_validate(finding(assessment="unknown", basis="hypothesis", evidence_ids=[]))


def test_criteria_are_assessed_once(scenario: Scenario) -> None:
    with pytest.raises(ValidationError, match="assessed only once"):
        decision(scenario, findings=[finding(), finding()])


def test_disqualified_needs_a_named_disqualifier(scenario: Scenario) -> None:
    with pytest.raises(ValidationError, match="must name at least one disqualifier"):
        decision(scenario, outcome="disqualified")


def test_disqualifier_must_be_an_evidenced_miss(scenario: Scenario) -> None:
    unknown = finding(assessment="unknown", basis="hypothesis", evidence_ids=[])
    with pytest.raises(ValidationError, match="needs a does_not_meet finding"):
        decision(
            scenario, outcome="disqualified", findings=[unknown], disqualifiers=["active-it-hiring"]
        )


def test_evidenced_miss_can_disqualify(scenario: Scenario) -> None:
    miss = finding(assessment="does_not_meet", rationale="No IT roles; staff of 12.")
    result = decision(
        scenario, outcome="disqualified", findings=[miss], disqualifiers=["active-it-hiring"]
    )
    assert result.outcome == "disqualified"


def test_qualified_needs_a_met_criterion(scenario: Scenario) -> None:
    unknown = finding(assessment="unknown", basis="hypothesis", evidence_ids=[])
    with pytest.raises(ValidationError, match="at least one criterion that is met"):
        decision(scenario, findings=[unknown])


def test_fixture_citations_verify(scenario: Scenario) -> None:
    verify_citations(scenario.qualification, scenario.evidence)


def test_missing_evidence_is_reported(scenario: Scenario) -> None:
    with pytest.raises(CitationError, match="ev-northstar-careers: not found"):
        verify_citations(scenario.qualification, scenario.evidence[1:])


def test_failed_retrieval_cannot_support_a_finding(scenario: Scenario) -> None:
    cites_failure = decision(
        scenario,
        findings=[finding(evidence_ids=["ev-northstar-news"])],
    )
    with pytest.raises(CitationError, match="failed, cannot support a finding"):
        verify_citations(cites_failure, scenario.evidence)


def test_evidence_from_another_customer_is_rejected(scenario: Scenario) -> None:
    other = decision(scenario, customer_id="demo-other")
    with pytest.raises(CitationError, match="belongs to another customer"):
        verify_citations(other, scenario.evidence)


def test_evidence_about_another_account_is_rejected(scenario: Scenario) -> None:
    other = decision(scenario, account_id="acc-someone-else")
    with pytest.raises(CitationError, match="about a different account"):
        verify_citations(other, scenario.evidence)
