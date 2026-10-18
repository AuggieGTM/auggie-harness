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
from auggie.acquisition.validation import check_evidence
from auggie.contracts import (
    CustomerScope,
    Proof,
    SourceEvidence,
    Strategy,
    SubjectRef,
    ensure_in_scope,
)
from auggie.signals import phrase_pattern
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


def holds(text: str, claim: str) -> bool:
    """Whether ``text`` says ``claim`` word for word: case kept, any whitespace between words."""
    return phrase_pattern(claim, ignore_case=False).search(text) is not None


def check_proofs(store: Store, strategy: Strategy, at: datetime) -> dict[str, ProofCheck]:
    """Each proof of ``strategy``, judged from the newest read of its page as of ``at``. A
    newer read that failed makes a proof unusable; an older one never stands in for it."""
    ensure_in_scope(store.scope, strategy)
    subject = seller(store.scope)
    reads = store.list_evidence(subject=subject)
    max_age = timedelta(days=strategy.proofs.max_age_days)
    checks = {}
    for proof_id, proof in strategy.proofs.by_id.items():
        same = [(n, e) for n, e in enumerate(reads) if str(e.source_url) == str(proof.url)]
        same = [(n, e) for n, e in same if e.observed_at <= at]
        newest = max(same, key=lambda pair: (pair[1].observed_at, pair[0]))[1] if same else None
        checks[proof_id] = ProofCheck(
            proof_id, proof, newest, _problem(store, proof, newest, at, max_age)
        )
    return checks


def _problem(
    store: Store, proof: Proof, read: SourceEvidence | None, at: datetime, max_age: timedelta
) -> str | None:
    if read is None:
        return "its page has not been read; read it with proofs --read"
    verdict = check_evidence(read, scope=store.scope, subject=seller(store.scope))
    if not read.usable:
        return f"the newest read of its page failed: {read.failure_reason}"
    if not verdict.admitted:
        return f"the newest read of its page was rejected ({verdict.rejected_for})"
    if at - read.observed_at > max_age:
        return (
            f"its page was last read on {read.observed_at:%Y-%m-%d}, more than "
            f"{max_age.days} days before {at:%Y-%m-%d}"
        )
    if not holds(read.excerpt or "", proof.claim):
        return (
            "the newest read of its page does not hold the claim word for word "
            f"(a read keeps the page's first {MAX_CHARACTERS:,} characters)"
        )
    return None
