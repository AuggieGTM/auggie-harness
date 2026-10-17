"""What the MongoDB Store enforces in the database itself, beyond the shared contract (ADR 0015)."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, WriteError

from auggie.contracts import Artifact, CustomerScope, OperationalEvent, ScopeViolation
from auggie.store import RecordNotFound, StoreError
from auggie.store.mongodb import MongoBackend
from tests.conftest import MONGODB_URI, Scenario, mongo_backend, variant


@pytest.fixture
def backend() -> Iterator[MongoBackend]:
    with mongo_backend() as mongo:
        yield mongo


def raw(backend: MongoBackend, scope: CustomerScope) -> Database[dict[str, Any]]:
    """The scope's database, bypassing the Store, as a careless caller might."""
    return backend._client[backend.database_name(scope)]


def test_a_database_cannot_be_claimed_by_another_customer(
    backend: MongoBackend, scenario: Scenario
) -> None:
    backend.open(scenario.scope)
    # A second process, configured for a different customer on the same database.
    elsewhere = MongoBackend(backend._client, database_prefix=backend.database_prefix)
    impostor = CustomerScope.model_validate(
        variant(scenario.scope, customer_id="demo-impostor", credential_namespace="demo-impostor")
    )
    with pytest.raises(ScopeViolation, match="belongs to"):
        elsewhere.open(impostor)
    reopened = MongoBackend(backend._client, database_prefix=backend.database_prefix).open(
        scenario.scope
    )
    assert reopened.scope == scenario.scope


def test_the_database_refuses_records_of_another_customer(
    backend: MongoBackend, scenario: Scenario
) -> None:
    backend.open(scenario.scope)
    foreign = scenario.evidence[0].model_dump(mode="json") | {
        "_id": "ev-foreign",
        "customer_id": "demo-other",
        "seq": 999,
    }
    with pytest.raises(WriteError, match="Document failed validation"):
        raw(backend, scenario.scope)["evidence"].insert_one(foreign)


def test_the_database_keeps_one_event_per_idempotency_key(
    backend: MongoBackend, scenario: Scenario
) -> None:
    backend.open(scenario.scope).append_event(scenario.events[0])
    copy = scenario.events[0].model_dump(mode="json") | {"_id": "evt-copy", "seq": 999}
    copy["event_id"] = "evt-copy"
    with pytest.raises(DuplicateKeyError, match="idempotency_key"):
        raw(backend, scenario.scope)["events"].insert_one(copy)


def test_the_database_keeps_artifact_versions_in_one_line(
    backend: MongoBackend, scenario: Scenario, draft: Artifact
) -> None:
    store = backend.open(scenario.scope)
    for evidence in scenario.evidence:
        store.put_evidence(evidence)
    store.put_qualification(scenario.qualification)
    store.put_artifact(draft)
    second_first = draft.model_dump(mode="json") | {"_id": "draft-northstar-001@other", "seq": 999}
    second_first["ref"]["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(DuplicateKeyError, match="supersedes"):
        raw(backend, scenario.scope)["artifacts"].insert_one(second_first)


def test_database_names_must_fit_mongodb(backend: MongoBackend, scenario: Scenario) -> None:
    long = CustomerScope.model_validate(variant(scenario.scope, data_namespace="a" * 63))
    with pytest.raises(ValueError, match="longer than MongoDB allows"):
        backend.open(long)


def foreign_evidence(scenario: Scenario) -> dict[str, Any]:
    return scenario.evidence[0].model_dump(mode="json") | {
        "_id": "ev-foreign",
        "evidence_id": "ev-foreign",
        "customer_id": "demo-other",
        "seq": 999,
    }


def test_an_unmarked_database_that_holds_data_is_refused_untouched(
    backend: MongoBackend, scenario: Scenario
) -> None:
    database = raw(backend, scenario.scope)
    database["evidence"].insert_one(foreign_evidence(scenario))
    with pytest.raises(ScopeViolation, match="no scope marker"):
        backend.open(scenario.scope)
    assert "_scope" not in database.list_collection_names()
    assert database["evidence"].options() == {}


def test_records_of_another_customer_are_never_returned(
    backend: MongoBackend, scenario: Scenario
) -> None:
    store = backend.open(scenario.scope)
    database = raw(backend, scenario.scope)
    database.command("collMod", "evidence", validationLevel="off")  # someone bypassed the backstop
    database["evidence"].insert_one(foreign_evidence(scenario))
    with pytest.raises(ScopeViolation, match="demo-other"):
        store.get_evidence("ev-foreign")
    with pytest.raises(ScopeViolation, match="demo-other"):
        store.list_evidence()
    recorded = OperationalEvent.model_validate(
        variant(
            scenario.events[1],
            refs=[{"kind": "evidence", "id": "ev-foreign"}],
            idempotency_key="evidence.recorded:ev-foreign",
        )
    )
    with pytest.raises(ScopeViolation, match="demo-other"):
        store.append_event(recorded)
    assert store.list_events() == ()


def test_reopening_restores_the_validator_backstop(
    backend: MongoBackend, scenario: Scenario
) -> None:
    backend.open(scenario.scope)
    database = raw(backend, scenario.scope)
    database.command("collMod", "evidence", validationAction="warn", validationLevel="moderate")
    MongoBackend(backend._client, database_prefix=backend.database_prefix).open(scenario.scope)
    options = database["evidence"].options()
    assert (options["validationAction"], options["validationLevel"]) == ("error", "strict")
    with pytest.raises(WriteError, match="Document failed validation"):
        database["evidence"].insert_one(foreign_evidence(scenario))


def race_to_open(uri: str, prefix: str, scope: CustomerScope, openers: int) -> list[Exception]:
    """Open ``scope`` from ``openers`` clients at once; separate clients stand in for processes."""
    start = threading.Barrier(openers, timeout=30)
    failures: list[Exception] = []

    def open_scope() -> None:
        client: MongoClient[dict[str, Any]] = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            own = MongoBackend(client, database_prefix=prefix)
            client.admin.command("ping")
            start.wait()
            own.open(scope)
        except Exception as error:  # collected and reported by the caller
            failures.append(error)
            start.abort()  # release any opener still waiting, rather than leave it hanging
        finally:
            client.close()

    threads = [threading.Thread(target=open_scope, daemon=True) for _ in range(openers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "an opener never finished"
    return failures


def test_many_processes_can_open_a_fresh_database_at_once(
    backend: MongoBackend, scenario: Scenario, other_scope: CustomerScope
) -> None:
    for scope in (scenario.scope, other_scope):
        assert race_to_open(os.environ[MONGODB_URI], backend.database_prefix, scope, 8) == []
        assert backend.open(scope).list_events() == ()


@pytest.fixture
def other_scope() -> CustomerScope:
    return CustomerScope(
        customer_id="demo-other",
        synthetic=True,
        data_namespace="auggie_demo_other",
        credential_namespace="demo-other",
        knowledge_namespace="demo-other-strategy",
    )


def test_open_existing_reads_without_creating_or_changing_anything(
    backend: MongoBackend, scenario: Scenario
) -> None:
    fresh = MongoBackend(backend._client, database_prefix=backend.database_prefix)
    with pytest.raises(RecordNotFound, match="no store"):
        fresh.open_existing(scenario.scope)
    name = backend.database_name(scenario.scope)
    assert name not in backend._client.list_database_names()

    backend.open(scenario.scope).put_evidence(scenario.evidence[0])
    database = raw(backend, scenario.scope)
    database.command("collMod", "evidence", validationAction="warn")
    reader = MongoBackend(backend._client, database_prefix=backend.database_prefix)
    store = reader.open_existing(scenario.scope)
    assert store.list_evidence() == (scenario.evidence[0],)
    assert database["evidence"].options()["validationAction"] == "warn"
    with pytest.raises(StoreError, match="read-only"):
        store.put_evidence(scenario.evidence[1])
    with pytest.raises(StoreError, match="read-only"):
        store.append_event(scenario.events[0])


def test_open_existing_refuses_another_customers_database(
    backend: MongoBackend, scenario: Scenario
) -> None:
    backend.open(scenario.scope)
    impostor = CustomerScope.model_validate(
        variant(scenario.scope, customer_id="demo-impostor", credential_namespace="demo-impostor")
    )
    reader = MongoBackend(backend._client, database_prefix=backend.database_prefix)
    with pytest.raises(ScopeViolation, match="belongs to"):
        reader.open_existing(impostor)
