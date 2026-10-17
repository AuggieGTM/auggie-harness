"""In-memory reference Store. For tests and synthetic runs; nothing survives the process.

Records are kept as JSON and validated again on every read, so this behaves
like a database round trip rather than handing back the caller's objects.
"""

from __future__ import annotations

from collections.abc import Container, Iterable
from dataclasses import dataclass, field

from pydantic import BaseModel

from auggie.contracts import (
    Artifact,
    ArtifactRef,
    CustomerScope,
    HumanApproval,
    OperationalEvent,
    QualificationDecision,
    RecordRef,
    ScopeViolation,
    SourceEvidence,
    SubjectRef,
    check_isolated,
    ensure_in_scope,
)
from auggie.contracts.events import EventType
from auggie.store import Appended, RecordConflict, RecordNotFound
from auggie.store.integrity import (
    check_approval,
    check_artifact,
    check_event,
    check_qualification,
    literal,
)


@dataclass
class _Namespace:
    """One scope's data, like one database per customer."""

    evidence: dict[str, str] = field(default_factory=dict)
    qualifications: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, list[str]] = field(default_factory=dict)
    approvals: dict[str, str] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    event_keys: dict[str, int] = field(default_factory=dict)
    event_ids: dict[str, int] = field(default_factory=dict)


class InMemoryBackend:
    """Holds one isolated namespace per scope opened on it."""

    def __init__(self) -> None:
        self._scopes: dict[str, CustomerScope] = {}
        self._namespaces: dict[str, _Namespace] = {}

    def open(self, scope: CustomerScope) -> InMemoryStore:
        known = self._scopes.get(scope.customer_id)
        if known is None:
            check_isolated([*self._scopes.values(), scope])
            self._scopes[scope.customer_id] = scope
        elif known != scope:
            raise ScopeViolation(
                f"customer {scope.customer_id!r} is already open with another scope"
            )
        namespace = self._namespaces.setdefault(scope.data_namespace, _Namespace())
        return InMemoryStore(scope, namespace)


def _load[M: BaseModel](model: type[M], data: str) -> M:
    return model.model_validate_json(data)


def _put(table: dict[str, str], key: str, record: BaseModel) -> bool:
    """Store ``record`` under ``key`` unless an identical one is there. Returns whether it wrote."""
    stored = table.get(key)
    if stored is None:
        table[key] = record.model_dump_json()
        return True
    if _load(type(record), stored) != record:
        raise RecordConflict(f"a different {type(record).__name__} {key!r} is already stored")
    return False


class InMemoryStore:
    """A ``Store`` over one scope's in-memory namespace. Open it through ``InMemoryBackend``."""

    def __init__(self, scope: CustomerScope, namespace: _Namespace) -> None:
        self._scope = scope
        self._data = namespace

    @property
    def scope(self) -> CustomerScope:
        return self._scope

    # Evidence

    def put_evidence(self, record: SourceEvidence) -> None:
        ensure_in_scope(self._scope, record)
        _put(self._data.evidence, record.evidence_id, record)

    def get_evidence(self, evidence_id: str) -> SourceEvidence:
        stored = self._data.evidence.get(literal(evidence_id, "evidence_id"))
        if stored is None:
            raise RecordNotFound(f"evidence {evidence_id!r}")
        return _load(SourceEvidence, stored)

    def list_evidence(self, *, subject: SubjectRef | None = None) -> tuple[SourceEvidence, ...]:
        records = (_load(SourceEvidence, data) for data in self._data.evidence.values())
        return tuple(r for r in records if subject is None or r.subject == subject)

    def _find_evidence(self, evidence_ids: Iterable[str]) -> list[SourceEvidence]:
        return [
            _load(SourceEvidence, self._data.evidence[eid])
            for eid in evidence_ids
            if eid in self._data.evidence
        ]

    # Qualifications

    def put_qualification(self, decision: QualificationDecision) -> None:
        check_qualification(self._scope, decision, self._find_evidence(decision.cited_evidence_ids))
        _put(self._data.qualifications, decision.decision_id, decision)

    def get_qualification(self, decision_id: str) -> QualificationDecision:
        stored = self._data.qualifications.get(literal(decision_id, "decision_id"))
        if stored is None:
            raise RecordNotFound(f"qualification {decision_id!r}")
        return _load(QualificationDecision, stored)

    # Artifacts

    def put_artifact(self, artifact: Artifact) -> None:
        ensure_in_scope(self._scope, artifact)
        versions = self._data.artifacts.get(artifact.ref.artifact_id, [])
        for data in versions:
            stored = _load(Artifact, data)
            if stored.ref == artifact.ref:
                if stored != artifact:
                    raise RecordConflict(f"a different version {artifact.ref.content_hash} exists")
                return
        qualification = self._data.qualifications.get(artifact.qualification_id)
        check_artifact(
            self._scope,
            artifact,
            previous=_load(Artifact, versions[-1]) if versions else None,
            qualification=_load(QualificationDecision, qualification) if qualification else None,
            cited=self._find_evidence(artifact.evidence_ids),
        )
        self._data.artifacts.setdefault(artifact.ref.artifact_id, []).append(
            artifact.model_dump_json()
        )

    def get_artifact(self, ref: ArtifactRef) -> Artifact:
        for data in self._data.artifacts.get(ref.artifact_id, []):
            artifact = _load(Artifact, data)
            if artifact.ref == ref:
                return artifact
        raise RecordNotFound(f"artifact {ref.artifact_id!r} version {ref.content_hash}")

    def latest_artifact(self, artifact_id: str) -> Artifact:
        versions = self._data.artifacts.get(literal(artifact_id, "artifact_id"))
        if not versions:
            raise RecordNotFound(f"artifact {artifact_id!r}")
        return _load(Artifact, versions[-1])

    # Approvals

    def put_approval(self, approval: HumanApproval) -> None:
        try:
            artifact: Artifact | None = self.get_artifact(approval.artifact)
        except RecordNotFound:
            artifact = None
        check_approval(self._scope, approval, artifact)
        _put(self._data.approvals, approval.approval_id, approval)

    def get_approval(self, approval_id: str) -> HumanApproval:
        stored = self._data.approvals.get(literal(approval_id, "approval_id"))
        if stored is None:
            raise RecordNotFound(f"approval {approval_id!r}")
        return _load(HumanApproval, stored)

    def list_approvals(self, artifact_id: str) -> tuple[HumanApproval, ...]:
        literal(artifact_id, "artifact_id")
        approvals = (_load(HumanApproval, data) for data in self._data.approvals.values())
        return tuple(a for a in approvals if a.artifact.artifact_id == artifact_id)

    # Events

    def _exists(self, ref: RecordRef) -> bool:
        tables: dict[str, Container[str]] = {
            "evidence": self._data.evidence,
            "qualification": self._data.qualifications,
            "artifact": self._data.artifacts,
            "approval": self._data.approvals,
        }
        return ref.id in tables.get(ref.kind, ())

    def append_event(self, event: OperationalEvent) -> Appended:
        ensure_in_scope(self._scope, event)
        index = self._data.event_keys.get(event.idempotency_key)
        if index is not None:
            stored = _load(OperationalEvent, self._data.events[index])
            if (stored.type, stored.refs) != (event.type, event.refs):
                raise RecordConflict(
                    f"idempotency key {event.idempotency_key!r} already names a different fact"
                )
            return Appended(stored, created=False)
        if event.event_id in self._data.event_ids:
            raise RecordConflict(f"event id {event.event_id!r} is already used")
        check_event(self._scope, event, self._exists)
        self._data.event_keys[event.idempotency_key] = len(self._data.events)
        self._data.event_ids[event.event_id] = len(self._data.events)
        self._data.events.append(event.model_dump_json())
        return Appended(_load(OperationalEvent, self._data.events[-1]), created=True)

    def list_events(
        self, *, type: EventType | None = None, run_id: str | None = None
    ) -> tuple[OperationalEvent, ...]:
        if type is not None:
            literal(type, "type")
        if run_id is not None:
            literal(run_id, "run_id")
        events = (_load(OperationalEvent, data) for data in self._data.events)
        return tuple(
            e
            for e in events
            if (type is None or e.type == type) and (run_id is None or e.run_id == run_id)
        )
