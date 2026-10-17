"""Qualification decisions: an account judged against an approved strategy revision."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import Field, model_validator

from auggie.contracts.common import Actor, Contract, RecordId, Slug, StrategyRef, Text, UtcDatetime
from auggie.contracts.evidence import SourceEvidence

Basis = Literal["source_fact", "vendor_claim", "inference", "hypothesis"]
Assessment = Literal["meets", "does_not_meet", "unknown"]
QualificationOutcome = Literal["qualified", "disqualified", "needs_review"]


class CitationError(ValueError):
    """A decision cites evidence that is missing, unusable or out of scope."""


class Finding(Contract):
    """One strategy criterion assessed against evidence.

    ``basis`` says how the conclusion relates to its sources. A source fact
    or a vendor claim restates evidence. An inference reasons from evidence.
    A hypothesis is a hunch to test and cannot settle a criterion.
    """

    criterion: Slug
    assessment: Assessment
    basis: Basis
    evidence_ids: tuple[RecordId, ...] = ()
    rationale: Text

    @model_validator(mode="after")
    def _check_support(self) -> Finding:
        if self.basis in ("source_fact", "vendor_claim") and not self.evidence_ids:
            raise ValueError(f"a {self.basis} finding must cite evidence")
        if self.assessment != "unknown":
            if self.basis == "hypothesis":
                raise ValueError("a hypothesis cannot settle a criterion; assess it as unknown")
            if not self.evidence_ids:
                raise ValueError(
                    f"assessing {self.criterion!r} as {self.assessment} needs evidence"
                )
        return self


class QualificationDecision(Contract):
    """Whether an account fits, under which strategy revision, and why."""

    schema_version: Literal[1] = 1
    decision_id: RecordId
    customer_id: Slug
    account_id: RecordId
    strategy: StrategyRef
    outcome: QualificationOutcome
    findings: Annotated[tuple[Finding, ...], Field(min_length=1)]
    disqualifiers: tuple[Slug, ...] = ()
    unknowns: tuple[Text, ...] = ()
    decided_by: Actor
    decided_at: UtcDatetime

    @model_validator(mode="after")
    def _check_outcome(self) -> QualificationDecision:
        by_criterion = {finding.criterion: finding for finding in self.findings}
        if len(by_criterion) != len(self.findings):
            raise ValueError("each criterion may be assessed only once per decision")
        for criterion in self.disqualifiers:
            finding = by_criterion.get(criterion)
            if finding is None or finding.assessment != "does_not_meet":
                raise ValueError(
                    f"disqualifier {criterion!r} needs a does_not_meet finding backed by evidence"
                )
        if self.outcome == "disqualified" and not self.disqualifiers:
            raise ValueError("a disqualified outcome must name at least one disqualifier")
        if self.outcome == "qualified":
            if self.disqualifiers:
                raise ValueError("a qualified outcome cannot have disqualifiers")
            if not any(finding.assessment == "meets" for finding in self.findings):
                raise ValueError("a qualified outcome needs at least one criterion that is met")
        return self

    @property
    def cited_evidence_ids(self) -> frozenset[str]:
        return frozenset(eid for finding in self.findings for eid in finding.evidence_ids)


def verify_citations(decision: QualificationDecision, evidence: Iterable[SourceEvidence]) -> None:
    """Raise ``CitationError`` unless every cited evidence record exists and may support it.

    Cited evidence must belong to the same customer, have been retrieved, and
    (when about an account) be about the account being qualified.
    """
    verify_cited_evidence(
        customer_id=decision.customer_id,
        account_id=decision.account_id,
        evidence_ids=decision.cited_evidence_ids,
        evidence=evidence,
    )


def verify_cited_evidence(
    *,
    customer_id: str,
    account_id: str,
    evidence_ids: Iterable[str],
    evidence: Iterable[SourceEvidence],
) -> None:
    """Raise ``CitationError`` unless each ID names usable evidence about this account."""
    known = {record.evidence_id: record for record in evidence}
    problems: list[str] = []
    for evidence_id in sorted(set(evidence_ids)):
        record = known.get(evidence_id)
        if record is None:
            problems.append(f"{evidence_id}: not found")
        elif record.customer_id != customer_id:
            problems.append(f"{evidence_id}: belongs to another customer")
        elif not record.usable:
            problems.append(f"{evidence_id}: {record.status}, cannot support a finding")
        elif record.subject.kind == "account" and record.subject.id != account_id:
            problems.append(f"{evidence_id}: about a different account")
    if problems:
        raise CitationError("; ".join(problems))
