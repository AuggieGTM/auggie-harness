"""Write-time checks every Store implementation applies. Pure: callers fetch the records."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from auggie.contracts import (
    Artifact,
    CareersSnapshot,
    CustomerScope,
    HumanApproval,
    OperationalEvent,
    Person,
    QualificationDecision,
    RecordRef,
    SourceEvidence,
    ensure_in_scope,
    verify_citations,
    verify_cited_evidence,
    verify_cited_snapshots,
)
from auggie.proofs import page_problem
from auggie.store import IntegrityError

STORED_KINDS = frozenset({"person", "evidence", "careers", "qualification", "artifact", "approval"})
"""Record kinds the Store holds. Event refs to these must resolve; others name outside records."""


def literal(value: object, what: str) -> str:
    """Return ``value`` if it is a plain string.

    IDs and filters reach a database as literal values, never as query
    objects, whatever a caller passes despite the type hints.
    """
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a string, not {type(value).__name__}")
    return value


def check_qualification(
    scope: CustomerScope,
    decision: QualificationDecision,
    cited: Iterable[SourceEvidence],
    snapshots: Iterable[CareersSnapshot] = (),
) -> None:
    """Cited evidence and snapshots exist, are usable and are about this account. Which read a
    run made is known only to the run; research checks that before writing."""
    ensure_in_scope(scope, decision)
    verify_citations(decision, cited, snapshots)


def check_artifact(
    scope: CustomerScope,
    artifact: Artifact,
    *,
    previous: Artifact | None,
    qualification: QualificationDecision | None,
    cited: Iterable[SourceEvidence],
    snapshots: Iterable[CareersSnapshot] = (),
    recipient: Person | None = None,
) -> None:
    """Check lineage, and that ``artifact`` is the next version after ``previous``.

    ``recipient`` is the stored person the draft is for, if one is stored.
    """
    ensure_in_scope(scope, artifact)
    if recipient is not None and recipient.account_id != artifact.account_id:
        raise IntegrityError("the draft's recipient belongs to another account")
    if qualification is None:
        raise IntegrityError(f"qualification {artifact.qualification_id!r} is not stored")
    if qualification.account_id != artifact.account_id:
        raise IntegrityError("the artifact and its qualification are about different accounts")
    uncited = sorted(set(artifact.snapshot_ids) - qualification.cited_snapshot_ids)
    if uncited:
        raise IntegrityError(
            f"the artifact cites snapshots its qualification does not: {', '.join(uncited)}"
        )
    verify_cited_evidence(
        customer_id=artifact.customer_id,
        account_id=artifact.account_id,
        evidence_ids=artifact.evidence_ids,
        evidence=cited,
    )
    verify_cited_snapshots(
        customer_id=artifact.customer_id,
        account_id=artifact.account_id,
        snapshot_ids=artifact.snapshot_ids,
        snapshots=snapshots,
    )
    _check_quoted_proof(artifact, cited)
    if previous is None:
        if artifact.supersedes is not None:
            raise IntegrityError("the first version of an artifact cannot supersede another")
        return
    if artifact.supersedes != previous.ref.content_hash:
        raise IntegrityError(
            f"a new version of {artifact.ref.artifact_id!r} must supersede the latest one"
        )
    changed = [
        name
        for name, before, after in (
            ("kind", previous.ref.kind, artifact.ref.kind),
            ("account_id", previous.account_id, artifact.account_id),
            ("recipient_id", previous.recipient_id, artifact.recipient_id),
        )
        if before != after
    ]
    if changed:
        raise IntegrityError(f"a new version cannot change {', '.join(changed)}")


def _check_quoted_proof(artifact: Artifact, cited: Iterable[SourceEvidence]) -> None:
    """A proof the draft quotes rests on a read of the seller's own page, at the proof's URL,
    that holds its claim and its customer word for word (ADR 0026). Whether that read is the
    newest, admitted and recent enough is known from the strategy; research checks it."""
    proof = artifact.point_of_view.proof if artifact.point_of_view else None
    if proof is None:
        return
    read = next((e for e in cited if e.evidence_id == proof.evidence_id), None)
    if read is None:
        raise IntegrityError(f"the read of proof {proof.proof_id!r} is not stored")
    problem = page_problem(
        read,
        customer_id=artifact.customer_id,
        url=str(proof.url),
        claim=proof.claim,
        customer=proof.customer,
    )
    if problem is not None:
        raise IntegrityError(f"proof {proof.proof_id!r}: {problem}")


def check_approval(
    scope: CustomerScope, approval: HumanApproval, artifact: Artifact | None
) -> None:
    """Check that ``approval`` binds to a stored version (``artifact``) and fits it."""
    ensure_in_scope(scope, approval)
    if artifact is None:
        raise IntegrityError(
            f"artifact {approval.artifact.artifact_id!r} has no version "
            f"{approval.artifact.content_hash}"
        )
    if approval.target is not None and approval.target.recipient_id != artifact.recipient_id:
        raise IntegrityError("a delivery approval must target the draft's own recipient")


def check_event(
    scope: CustomerScope, event: OperationalEvent, exists: Callable[[RecordRef], bool]
) -> None:
    ensure_in_scope(scope, event)
    missing = [
        f"{ref.kind}:{ref.id}" for ref in event.refs if ref.kind in STORED_KINDS and not exists(ref)
    ]
    if missing:
        raise IntegrityError(f"{event.type} refers to records not stored: {', '.join(missing)}")
