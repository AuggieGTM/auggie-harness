"""Proofs: the seller's customer stories, usable only while a read of their page holds them
word for word (ADR 0026).

A proof is a vendor claim. It is checked against the newest recorded read of its
page, which is evidence about the seller, and never restated: a draft quotes the
claim exactly as the strategy gives it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from auggie.acquisition.treg import MAX_CHARACTERS
from auggie.acquisition.validation import recorded_validation
from auggie.contracts import (
    CustomerScope,
    Proof,
    SourceEvidence,
    Strategy,
    SubjectRef,
    ensure_in_scope,
)
from auggie.signals import says
from auggie.store import Store


def seller(scope: CustomerScope) -> SubjectRef:
    """The subject of evidence about the seller's own pages."""
    return SubjectRef(kind="seller", id=scope.customer_id)


@dataclass(frozen=True)
class ProofCheck:
    proof_id: str
    proof: Proof
    read: SourceEvidence | None
    """The newest recorded read of the proof's page, if any."""
    problem: str | None
    """Why a draft cannot quote the proof; None when it can."""

    @property
    def usable(self) -> bool:
        return self.problem is None


def page_problem(
    read: SourceEvidence, *, customer_id: str, url: str, claim: str, customer: str
) -> str | None:
    """Why ``read`` cannot support a proof: not a read of this seller's own page at ``url``, or
    without the claim and the customer's name word for word. None when it can."""
    if read.subject != SubjectRef(kind="seller", id=customer_id):
        return f"{read.evidence_id} is not a read of the seller's own page"
    if str(read.source_url) != url:
        return f"{read.evidence_id} is a read of another page"
    text = read.excerpt or ""
    if not says(text, claim):
        return (
            "the newest read of its page does not hold the claim word for word "
            f"(a read keeps the page's first {MAX_CHARACTERS:,} characters)"
        )
    if not says(text, customer):
        return f"the newest read of its page does not name {customer}"
    return None


def check_proofs(store: Store, strategy: Strategy, at: datetime) -> dict[str, ProofCheck]:
    """Each proof of ``strategy``, judged from the newest read of its page as of ``at``, and
    that read's recorded validation. A newer read that failed, was rejected or was never
    checked makes a proof unusable; an older one never stands in for it."""
    ensure_in_scope(store.scope, strategy)
    reads = store.list_evidence(subject=seller(store.scope))
    validation = recorded_validation(store)
    max_age = timedelta(days=strategy.proofs.max_age_days)
    checks = {}
    for proof_id, proof in strategy.proofs.by_id.items():
        same = [
            (n, e)
            for n, e in enumerate(reads)
            if str(e.source_url) == str(proof.url) and e.observed_at <= at
        ]
        newest = max(same, key=lambda pair: (pair[1].observed_at, pair[0]))[1] if same else None
        problem = _problem(store, proof, newest, validation, at, max_age)
        checks[proof_id] = ProofCheck(proof_id, proof, newest, problem)
    return checks


def _problem(
    store: Store,
    proof: Proof,
    read: SourceEvidence | None,
    validation: dict[str, str],
    at: datetime,
    max_age: timedelta,
) -> str | None:
    if read is None:
        return "its page has not been read; read it with proofs --read"
    if not read.usable:
        return f"the newest read of its page failed: {read.failure_reason}"
    verdict = validation.get(read.evidence_id)
    if verdict is None:
        return f"the newest read of its page ({read.evidence_id}) was never checked"
    if verdict != "admitted":
        reason = verdict.partition(": ")[2]
        return "the newest read of its page was rejected" + (f" ({reason})" if reason else "")
    if at - read.observed_at > max_age:
        return (
            f"its page was last read on {read.observed_at:%Y-%m-%d}, more than "
            f"{max_age.days} days before {at:%Y-%m-%d}"
        )
    return page_problem(
        read,
        customer_id=store.scope.customer_id,
        url=str(proof.url),
        claim=proof.claim,
        customer=proof.customer,
    )
