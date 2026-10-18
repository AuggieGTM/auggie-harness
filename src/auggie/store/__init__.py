"""The typed Store API: the only path to a customer's operational records (ADR 0003).

A ``Store`` is bound to one ``CustomerScope``. It refuses records from any other
customer, keeps every record immutable, and checks lineage when a record is
written, so what it holds can always be traced back to evidence. Every
implementation runs the same contract tests in ``tests/store/``.
"""

from __future__ import annotations

from typing import NamedTuple, Protocol

from auggie.contracts import (
    Artifact,
    ArtifactRef,
    CareersSnapshot,
    CustomerScope,
    HumanApproval,
    OperationalEvent,
    Person,
    QualificationDecision,
    SourceEvidence,
    SubjectRef,
)
from auggie.contracts.events import EventType


class StoreError(Exception):
    """Base class for Store failures."""


class RecordNotFound(StoreError):
    """No record with that identity exists in this scope."""


class RecordConflict(StoreError):
    """A different record already exists under the same identity."""


class IntegrityError(StoreError):
    """A record refers to something missing or contradicts what is stored."""


class Appended(NamedTuple):
    """The stored event, and whether this call created it."""

    event: OperationalEvent
    created: bool


class Store(Protocol):
    """Typed operations on one customer's records.

    Writes are idempotent: writing an identical record again does nothing, and
    writing a different record under an existing identity raises
    ``RecordConflict``. Lists return records in the order they were written.
    """

    @property
    def scope(self) -> CustomerScope: ...

    def put_person(self, person: Person) -> None:
        """Store a person. A person is never edited: a new address needs a new ``person_id``."""
        ...

    def get_person(self, person_id: str) -> Person: ...

    def put_careers_snapshot(self, snapshot: CareersSnapshot) -> None:
        """Store one read attempt of an account's listing, failed ones included (ADR 0024)."""
        ...

    def get_careers_snapshot(self, snapshot_id: str) -> CareersSnapshot: ...

    def list_careers_snapshots(self, account_id: str) -> tuple[CareersSnapshot, ...]:
        """An account's snapshots, in the order they were written."""
        ...

    def put_evidence(self, record: SourceEvidence) -> None: ...

    def get_evidence(self, evidence_id: str) -> SourceEvidence: ...

    def list_evidence(self, *, subject: SubjectRef | None = None) -> tuple[SourceEvidence, ...]: ...

    def put_qualification(self, decision: QualificationDecision) -> None:
        """Store a decision. Every citation must resolve to usable evidence in this store, and
        every snapshot citation to a usable snapshot of the same account.

        Whether a snapshot was the run's own read or its baseline is known only to the run, so
        research checks that before it writes (ADR 0024); this method does not.
        """
        ...

    def get_qualification(self, decision_id: str) -> QualificationDecision: ...

    def put_artifact(self, artifact: Artifact) -> None:
        """Store one artifact version.

        Its qualification must exist and its evidence must be usable. A new
        version must supersede the current latest one and keep its kind,
        account and recipient.
        """
        ...

    def get_artifact(self, ref: ArtifactRef) -> Artifact:
        """Return exactly the version ``ref`` names."""
        ...

    def latest_artifact(self, artifact_id: str) -> Artifact: ...

    def put_approval(self, approval: HumanApproval) -> None:
        """Store an approval of an exact artifact version that this store holds."""
        ...

    def get_approval(self, approval_id: str) -> HumanApproval: ...

    def list_approvals(self, artifact_id: str) -> tuple[HumanApproval, ...]:
        """Approvals of any version of ``artifact_id``."""
        ...

    def append_event(self, event: OperationalEvent) -> Appended:
        """Append an event once per idempotency key.

        Appending the same key again returns the stored event with
        ``created=False``. The same key with a different type or refs raises
        ``RecordConflict``. Refs to stored record kinds must resolve.
        """
        ...

    def list_events(
        self, *, type: EventType | None = None, run_id: str | None = None
    ) -> tuple[OperationalEvent, ...]: ...


class Backend(Protocol):
    """Where stores live. Each scope gets its own data namespace (ADR 0004)."""

    def open(self, scope: CustomerScope) -> Store:
        """Return the store for ``scope``.

        Refuses a scope whose customer ID or namespaces collide with a scope
        already opened on this backend.
        """
        ...


__all__ = [
    "Appended",
    "Backend",
    "IntegrityError",
    "RecordConflict",
    "RecordNotFound",
    "Store",
    "StoreError",
]
