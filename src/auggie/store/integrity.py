"""Write-time checks every Store implementation applies. Pure: callers fetch the records."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from auggie.contracts import (
    Artifact,
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
)
from auggie.store import IntegrityError

STORED_KINDS = frozenset({"person", "evidence", "qualification", "artifact", "approval"})
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
    scope: CustomerScope, decision: QualificationDecision, cited: Iterable[SourceEvidence]
) -> None:
    ensure_in_scope(scope, decision)
    verify_citations(decision, cited)


def check_artifact(
    scope: CustomerScope,
    artifact: Artifact,
    *,
    previous: Artifact | None,
    qualification: QualificationDecision | None,
    cited: Iterable[SourceEvidence],
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
    verify_cited_evidence(
        customer_id=artifact.customer_id,
        account_id=artifact.account_id,
        evidence_ids=artifact.evidence_ids,
        evidence=cited,
    )
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
