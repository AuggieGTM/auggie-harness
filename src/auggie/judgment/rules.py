"""Deterministic qualification rules over admitted evidence excerpts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta

from auggie.acquisition.validation import Verdict
from auggie.contracts import (
    Actor,
    FamilyOpenings,
    Finding,
    HeadcountRange,
    JobPosting,
    ListingPattern,
    Mentions,
    QualificationDecision,
    ScopeViolation,
    StackMention,
    Strategy,
)
from auggie.contracts.qualification import QualificationOutcome
from auggie.contracts.strategy import AnyCriterion
from auggie.signals import stack_matches
from auggie.strategy import LoadedStrategy

HEADCOUNT = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+) (?:staff|employees|people)\b")
OPEN_ROLE = re.compile(r"\bhiring (?:an? )?([A-Z][\w-]*(?: [A-Z][\w-]*)*)")


def _unknown(criterion_id: str, rationale: str) -> Finding:
    return Finding(
        criterion=criterion_id, assessment="unknown", basis="hypothesis", rationale=rationale
    )


def _not_admitted(label: str, verdict: Verdict | None) -> str:
    if verdict is None:
        return f"No {label} was checked."
    if not verdict.evidence.usable:
        return f"The {label} could not be retrieved ({verdict.evidence.status})."
    return f"The {label} was rejected by validation ({verdict.rejected_for})."


def _recent(published_at: datetime | None, at: datetime, max_age_days: int) -> bool:
    """Published at or before ``at``, and no more than ``max_age_days`` earlier."""
    if published_at is None:
        return False
    return timedelta(0) <= at - published_at <= timedelta(days=max_age_days)


def _headcount(criterion: HeadcountRange, label: str, evidence_id: str, text: str) -> Finding:
    match = HEADCOUNT.search(text)
    if match is None:
        return _unknown(criterion.id, f"The {label} does not state a headcount.")
    count = int(match.group(1).replace(",", ""))
    return Finding(
        criterion=criterion.id,
        assessment="meets" if criterion.min <= count <= criterion.max else "does_not_meet",
        basis="source_fact",
        evidence_ids=(evidence_id,),
        rationale=f"The {label} states {match.group(0)}.",
    )


def _job_posting(criterion: JobPosting, label: str, verdict: Verdict, at: datetime) -> Finding:
    evidence = verdict.evidence
    titles = [
        title
        for title in OPEN_ROLE.findall(evidence.excerpt or "")
        if any(re.search(rf"\b{re.escape(k)}\b", title) for k in criterion.title_keywords)
    ]
    if not titles:
        return _unknown(criterion.id, f"The {label} shows no matching open role.")
    if not _recent(evidence.published_at, at, criterion.max_age_days):
        return _unknown(
            criterion.id,
            f"The {titles[0]} role is not dated within the last {criterion.max_age_days} days.",
        )
    return Finding(
        criterion=criterion.id,
        assessment="meets",
        basis="source_fact",
        evidence_ids=(evidence.evidence_id,),
        rationale=(
            f"An open {titles[0]} role, published within the last {criterion.max_age_days} days."
        ),
    )


def _mentions(criterion: Mentions, label: str, verdict: Verdict, at: datetime) -> Finding:
    evidence = verdict.evidence
    text = (evidence.excerpt or "").casefold()
    if not all(phrase.casefold() in text for phrase in criterion.phrases):
        return _unknown(criterion.id, f"The {label} does not mention it.")
    if criterion.max_age_days is not None and not _recent(
        evidence.published_at, at, criterion.max_age_days
    ):
        return _unknown(
            criterion.id,
            f"The {label} is not dated within the last {criterion.max_age_days} days.",
        )
    return Finding(
        criterion=criterion.id,
        assessment="meets",
        basis=criterion.basis,
        evidence_ids=(evidence.evidence_id,),
        rationale=criterion.rationale,
    )


def _stack(criterion: StackMention, strategy: Strategy, label: str, verdict: Verdict) -> Finding:
    """A page naming the seller's technology, quoted. Inference: a name on a page is not proof
    the account runs it (ADR 0023). Not found is unknown: the excerpt may not be the whole page."""
    assert strategy.signals.stack is not None  # the strategy contract guarantees it
    term = strategy.signals.stack.terms[criterion.term]
    found = stack_matches(verdict.evidence.excerpt or "", term)
    if not found:
        return _unknown(criterion.id, f"The {label}'s stored text does not name {term.label}.")
    match = found[0]
    near = f' near "{match.near}"' if match.near else ""
    return Finding(
        criterion=criterion.id,
        assessment="meets",
        basis="inference",
        evidence_ids=(verdict.evidence.evidence_id,),
        rationale=f'The {label} names {term.label}{near}: "{match.quote}"',
    )


def assess(
    criterion: AnyCriterion,
    strategy: Strategy,
    sources: Mapping[str, Verdict],
    at: datetime,
) -> Finding:
    if isinstance(criterion, FamilyOpenings | ListingPattern):
        return _unknown(criterion.id, "No careers listing was read for this account.")
    label = strategy.sources[criterion.source].label
    verdict = sources.get(criterion.source)
    if verdict is None or not verdict.admitted:
        return _unknown(criterion.id, _not_admitted(label, verdict))
    match criterion:
        case StackMention():
            return _stack(criterion, strategy, label, verdict)
        case HeadcountRange():
            return _headcount(
                criterion, label, verdict.evidence.evidence_id, verdict.evidence.excerpt or ""
            )
        case JobPosting():
            return _job_posting(criterion, label, verdict, at)
        case Mentions():
            return _mentions(criterion, label, verdict, at)


class RulesQualifier:
    """Qualifies with the strategy's own rules. Same inputs, same decision."""

    actor = Actor(kind="rule", id="fixture-icp-rules", version="0")

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
        rules = strategy.strategy
        if rules.customer_id != customer_id:
            raise ScopeViolation(f"strategy belongs to {rules.customer_id!r}, not {customer_id!r}")
        findings = tuple(assess(criterion, rules, sources, at) for criterion in rules.criteria)
        assessed = {finding.criterion: finding.assessment for finding in findings}
        disqualifiers = tuple(
            criterion.id
            for criterion in rules.criteria
            if isinstance(criterion, HeadcountRange)
            and criterion.disqualifying
            and assessed[criterion.id] == "does_not_meet"
        )
        outcome: QualificationOutcome
        if disqualifiers:
            outcome = "disqualified"
        elif all(assessed[name] == "meets" for name in rules.required):
            outcome = "qualified"
        else:
            outcome = "needs_review"
        return QualificationDecision(
            decision_id=decision_id,
            customer_id=customer_id,
            account_id=account_id,
            strategy=strategy.ref,
            outcome=outcome,
            findings=findings,
            disqualifiers=disqualifiers,
            unknowns=tuple(
                rules.criterion(f.criterion).question for f in findings if f.assessment == "unknown"
            ),
            decided_by=self.actor,
            decided_at=at,
        )
