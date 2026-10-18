"""MongoDB Store (ADR 0015): one database per scope, reached only through this module.

Documents are the contracts' JSON form plus ``_id`` and ``seq``. Unique indexes
hold the invariants that must survive a careless caller: one event per
idempotency key, one stored copy per artifact version, and a single line of
versions. Every read validates the contract again.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, DuplicateKeyError, OperationFailure

from auggie.contracts import (
    Artifact,
    ArtifactRef,
    CustomerScope,
    HumanApproval,
    OperationalEvent,
    Person,
    QualificationDecision,
    RecordRef,
    ScopeViolation,
    SourceEvidence,
    SubjectRef,
    check_isolated,
    ensure_in_scope,
)
from auggie.contracts.events import EventType
from auggie.store import Appended, IntegrityError, RecordConflict, RecordNotFound, StoreError
from auggie.store.integrity import (
    check_approval,
    check_artifact,
    check_event,
    check_qualification,
    literal,
)

Document = dict[str, Any]

NAMESPACE_EXISTS = 48
"""MongoDB's error code when a collection is created twice with different options."""

RECORD_COLLECTIONS = ("people", "evidence", "qualifications", "artifacts", "approvals", "events")

INDEXES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    # (collection, fields, unique)
    ("people", ("seq",), True),
    ("evidence", ("seq",), True),
    ("evidence", ("subject.kind", "subject.id"), False),
    ("qualifications", ("seq",), True),
    ("artifacts", ("seq",), True),
    ("artifacts", ("ref.artifact_id", "ref.content_hash"), True),
    ("artifacts", ("ref.artifact_id", "supersedes"), True),
    ("approvals", ("seq",), True),
    ("approvals", ("artifact.artifact_id",), False),
    ("events", ("seq",), True),
    ("events", ("idempotency_key",), True),
    ("events", ("event_id",), True),
    ("events", ("type", "seq"), False),
)


def _validator(scope: CustomerScope) -> Document:
    """Every record names this database's customer and its schema version (ADR 0015)."""
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["customer_id", "schema_version", "seq"],
            "properties": {
                "customer_id": {"enum": [scope.customer_id]},
                "schema_version": {"bsonType": ["int", "long"]},
                "seq": {"bsonType": ["int", "long"]},
            },
        }
    }


def _identity(scope: CustomerScope) -> Document:
    return {
        "customer_id": scope.customer_id,
        "data_namespace": scope.data_namespace,
        "credential_namespace": scope.credential_namespace,
        "knowledge_namespace": scope.knowledge_namespace,
    }


class MongoBackend:
    """Opens one database per scope on ``client``.

    ``database_prefix`` keeps test and demo runs apart from real data on the
    same server.
    """

    def __init__(self, client: MongoClient[Document], *, database_prefix: str = "") -> None:
        self._client = client
        self._prefix = database_prefix
        self._scopes: dict[str, CustomerScope] = {}

    @property
    def database_prefix(self) -> str:
        return self._prefix

    def database_name(self, scope: CustomerScope) -> str:
        name = self._prefix + scope.data_namespace
        if len(name.encode("utf-8")) > 63:
            raise ValueError(f"database name {name!r} is longer than MongoDB allows")
        return name

    def _check(self, scope: CustomerScope) -> None:
        known = self._scopes.get(scope.customer_id)
        if known is None:
            check_isolated([*self._scopes.values(), scope])
        elif known != scope:
            raise ScopeViolation(
                f"customer {scope.customer_id!r} is already open with another scope"
            )

    def open(self, scope: CustomerScope) -> MongoStore:
        self._check(scope)
        database = self._client[self.database_name(scope)]
        _claim(database, scope)
        _prepare(database, scope)
        self._scopes[scope.customer_id] = scope
        return MongoStore(scope, database)

    def open_existing(self, scope: CustomerScope) -> MongoStore:
        """Open a scope's existing store to read it. Claims, creates and changes nothing.

        Needs only read access. Raises ``RecordNotFound`` if no store was ever
        opened for this scope here, so a mistyped name cannot look like an
        empty store.
        """
        self._check(scope)
        database = self._client[self.database_name(scope)]
        stored = database["_scope"].find_one({"_id": "scope"}, {"_id": False})
        if stored is None:
            raise RecordNotFound(f"no store for {scope.customer_id!r} in {database.name!r}")
        if stored != _identity(scope):
            raise ScopeViolation(f"database {database.name!r} belongs to {stored}")
        self._scopes[scope.customer_id] = scope
        return MongoStore(scope, database, writable=False)


def _claim(database: Database[Document], scope: CustomerScope) -> None:
    """Mark the database as this scope's, or refuse if it belongs to another.

    An unmarked database is claimed only while it is empty, so data already
    there is never adopted silently. Collections are listed before the marker
    is read: whoever creates collections writes the marker first.
    """
    marker = database["_scope"]
    identity = _identity(scope)
    existing = set(database.list_collection_names()) - {"_scope"}
    stored = marker.find_one({"_id": "scope"}, {"_id": False})
    if stored is None:
        if existing:
            raise ScopeViolation(
                f"database {database.name!r} holds data but no scope marker; refusing to claim it"
            )
        try:
            marker.insert_one({"_id": "scope", **identity})
            return
        except DuplicateKeyError:
            stored = marker.find_one({"_id": "scope"}, {"_id": False})
    if stored != identity:
        raise ScopeViolation(f"database {database.name!r} belongs to {stored}, not {identity}")


def _prepare(database: Database[Document], scope: CustomerScope) -> None:
    validator = _validator(scope)
    for name in RECORD_COLLECTIONS:
        options: dict[str, Any] = {
            "validator": validator,
            "validationLevel": "strict",
            "validationAction": "error",
        }
        try:
            database.create_collection(name, **options)
        except CollectionInvalid:
            database.command("collMod", name, **options)
        except OperationFailure as error:
            # Another process created it between PyMongo's existence check and ours.
            if error.code != NAMESPACE_EXISTS:
                raise
            database.command("collMod", name, **options)
    for name, fields, unique in INDEXES:
        database[name].create_index([(field, ASCENDING) for field in fields], unique=unique)


def _load[M: BaseModel](model: type[M], document: Mapping[str, Any]) -> M:
    return model.model_validate({k: v for k, v in document.items() if k not in ("_id", "seq")})


def _artifact_key(ref: ArtifactRef) -> str:
    return f"{ref.artifact_id}@{ref.content_hash}"


class MongoStore:
    """A ``Store`` over one scope's database. Open it through ``MongoBackend``."""

    def __init__(
        self, scope: CustomerScope, database: Database[Document], *, writable: bool = True
    ) -> None:
        self._scope = scope
        self._db = database
        self._writable = writable

    def _writing(self) -> None:
        if not self._writable:
            raise StoreError(f"the store for {self._scope.customer_id!r} was opened read-only")

    @property
    def scope(self) -> CustomerScope:
        return self._scope

    def _next_seq(self) -> int:
        counter = self._db["_counters"].find_one_and_update(
            {"_id": "seq"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if counter is None:  # Unreachable with upsert=True; keeps the type honest.
            raise RuntimeError("the write-order counter was not created")
        return int(counter["value"])

    def _collection(self, name: str) -> Collection[Document]:
        return self._db[name]

    def _insert(self, name: str, key: str, record: BaseModel) -> bool:
        """Insert ``record`` under ``key``; an identical stored record is a no-op (False)."""
        document = record.model_dump(mode="json") | {"_id": key, "seq": self._next_seq()}
        try:
            self._collection(name).insert_one(document)
        except DuplicateKeyError:
            stored = self._collection(name).find_one({"_id": key})
            if stored is not None and _load(type(record), stored) == record:
                return False
            raise RecordConflict(
                f"a different {type(record).__name__} {key!r} is already stored"
            ) from None
        return True

    def _owned[M: BaseModel](self, model: type[M], document: Mapping[str, Any]) -> M:
        """Load a stored record, refusing one that names another customer.

        The validators keep such records out; this catches any written around them.
        """
        record = _load(model, document)
        owner = getattr(record, "customer_id", None)
        if owner != self._scope.customer_id:
            raise ScopeViolation(
                f"{self._db.name}.{model.__name__} holds a record of {owner!r}, "
                f"not {self._scope.customer_id!r}"
            )
        return record

    def _find_one[M: BaseModel](self, name: str, model: type[M], query: Document) -> M | None:
        document = self._collection(name).find_one(query)
        return None if document is None else self._owned(model, document)

    def _find[M: BaseModel](self, name: str, model: type[M], query: Document) -> tuple[M, ...]:
        cursor = self._collection(name).find(query).sort("seq", ASCENDING)
        return tuple(self._owned(model, document) for document in cursor)

    def _find_evidence(self, evidence_ids: Iterable[str]) -> tuple[SourceEvidence, ...]:
        return self._find("evidence", SourceEvidence, {"_id": {"$in": list(evidence_ids)}})

    # People

    def put_person(self, person: Person) -> None:
        self._writing()
        ensure_in_scope(self._scope, person)
        self._insert("people", person.person_id, person)

    def get_person(self, person_id: str) -> Person:
        person = self._find_one("people", Person, {"_id": literal(person_id, "person_id")})
        if person is None:
            raise RecordNotFound(f"person {person_id!r}")
        return person

    # Evidence

    def put_evidence(self, record: SourceEvidence) -> None:
        self._writing()
        ensure_in_scope(self._scope, record)
        self._insert("evidence", record.evidence_id, record)

    def get_evidence(self, evidence_id: str) -> SourceEvidence:
        record = self._find_one(
            "evidence", SourceEvidence, {"_id": literal(evidence_id, "evidence_id")}
        )
        if record is None:
            raise RecordNotFound(f"evidence {evidence_id!r}")
        return record

    def list_evidence(self, *, subject: SubjectRef | None = None) -> tuple[SourceEvidence, ...]:
        query: Document = {}
        if subject is not None:
            query = {"subject.kind": subject.kind, "subject.id": subject.id}
        return self._find("evidence", SourceEvidence, query)

    # Qualifications

    def put_qualification(self, decision: QualificationDecision) -> None:
        self._writing()
        check_qualification(self._scope, decision, self._find_evidence(decision.cited_evidence_ids))
        self._insert("qualifications", decision.decision_id, decision)

    def get_qualification(self, decision_id: str) -> QualificationDecision:
        decision = self._find_one(
            "qualifications", QualificationDecision, {"_id": literal(decision_id, "decision_id")}
        )
        if decision is None:
            raise RecordNotFound(f"qualification {decision_id!r}")
        return decision

    # Artifacts

    def put_artifact(self, artifact: Artifact) -> None:
        self._writing()
        ensure_in_scope(self._scope, artifact)
        key = _artifact_key(artifact.ref)
        stored = self._find_one("artifacts", Artifact, {"_id": key})
        if stored is not None:
            if stored != artifact:
                raise RecordConflict(f"a different version {artifact.ref.content_hash} exists")
            return
        previous = self._latest(artifact.ref.artifact_id)
        check_artifact(
            self._scope,
            artifact,
            previous=previous,
            qualification=self._find_one(
                "qualifications", QualificationDecision, {"_id": artifact.qualification_id}
            ),
            cited=self._find_evidence(artifact.evidence_ids),
            recipient=(
                self._find_one("people", Person, {"_id": artifact.recipient_id})
                if artifact.recipient_id is not None
                else None
            ),
        )
        try:
            self._insert("artifacts", key, artifact)
        except RecordConflict:
            # Lost a race on the version line: another writer added a version first.
            raise IntegrityError(
                f"a new version of {artifact.ref.artifact_id!r} must supersede the latest one"
            ) from None

    def _latest(self, artifact_id: str) -> Artifact | None:
        cursor = (
            self._collection("artifacts")
            .find({"ref.artifact_id": artifact_id})
            .sort("seq", DESCENDING)
            .limit(1)
        )
        documents = list(cursor)
        return self._owned(Artifact, documents[0]) if documents else None

    def get_artifact(self, ref: ArtifactRef) -> Artifact:
        artifact = self._find_one("artifacts", Artifact, {"_id": _artifact_key(ref)})
        if artifact is None or artifact.ref != ref:
            raise RecordNotFound(f"artifact {ref.artifact_id!r} version {ref.content_hash}")
        return artifact

    def latest_artifact(self, artifact_id: str) -> Artifact:
        artifact = self._latest(literal(artifact_id, "artifact_id"))
        if artifact is None:
            raise RecordNotFound(f"artifact {artifact_id!r}")
        return artifact

    # Approvals

    def put_approval(self, approval: HumanApproval) -> None:
        self._writing()
        try:
            artifact: Artifact | None = self.get_artifact(approval.artifact)
        except RecordNotFound:
            artifact = None
        check_approval(self._scope, approval, artifact)
        self._insert("approvals", approval.approval_id, approval)

    def get_approval(self, approval_id: str) -> HumanApproval:
        approval = self._find_one(
            "approvals", HumanApproval, {"_id": literal(approval_id, "approval_id")}
        )
        if approval is None:
            raise RecordNotFound(f"approval {approval_id!r}")
        return approval

    def list_approvals(self, artifact_id: str) -> tuple[HumanApproval, ...]:
        query = {"artifact.artifact_id": literal(artifact_id, "artifact_id")}
        return self._find("approvals", HumanApproval, query)

    # Events

    def _exists(self, ref: RecordRef) -> bool:
        """Whether ``ref`` names a stored record of this customer. Refuses a foreign one."""
        lookups: dict[str, tuple[str, type[BaseModel], Document]] = {
            "person": ("people", Person, {"_id": ref.id}),
            "evidence": ("evidence", SourceEvidence, {"_id": ref.id}),
            "qualification": ("qualifications", QualificationDecision, {"_id": ref.id}),
            "artifact": ("artifacts", Artifact, {"ref.artifact_id": ref.id}),
            "approval": ("approvals", HumanApproval, {"_id": ref.id}),
        }
        lookup = lookups.get(ref.kind)
        return lookup is not None and self._find_one(*lookup) is not None

    def _by_key(self, event: OperationalEvent) -> Appended | None:
        stored = self._find_one(
            "events", OperationalEvent, {"idempotency_key": event.idempotency_key}
        )
        if stored is None:
            return None
        if (stored.type, stored.refs) != (event.type, event.refs):
            raise RecordConflict(
                f"idempotency key {event.idempotency_key!r} already names a different fact"
            )
        return Appended(stored, created=False)

    def append_event(self, event: OperationalEvent) -> Appended:
        self._writing()
        ensure_in_scope(self._scope, event)
        existing = self._by_key(event)
        if existing is not None:
            return existing
        if self._collection("events").find_one({"_id": event.event_id}) is not None:
            raise RecordConflict(f"event id {event.event_id!r} is already used")
        check_event(self._scope, event, self._exists)
        document = event.model_dump(mode="json") | {"_id": event.event_id, "seq": self._next_seq()}
        try:
            self._collection("events").insert_one(document)
        except DuplicateKeyError:
            # Another writer appended the same fact first.
            existing = self._by_key(event)
            if existing is None:
                raise RecordConflict(f"event id {event.event_id!r} is already used") from None
            return existing
        return Appended(_load(OperationalEvent, document), created=True)

    def list_events(
        self, *, type: EventType | None = None, run_id: str | None = None
    ) -> tuple[OperationalEvent, ...]:
        query: Document = {}
        if type is not None:
            query["type"] = literal(type, "type")
        if run_id is not None:
            query["run_id"] = literal(run_id, "run_id")
        return self._find("events", OperationalEvent, query)
