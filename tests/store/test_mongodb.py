"""What the MongoDB Store enforces in the database itself, beyond the shared contract (ADR 0015)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, WriteError

from auggie.contracts import Artifact, CustomerScope, ScopeViolation
from auggie.store.mongodb import MongoBackend
from tests.conftest import Scenario, mongo_backend, variant


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
