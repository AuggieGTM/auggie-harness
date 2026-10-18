"""The Store contract. Every implementation must pass this suite unchanged.

Add an implementation as a ``backend`` fixture parameter. The MongoDB runs need
``$AUGGIE_TEST_MONGODB_URI`` (see ``tests/conftest.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from auggie.contracts import (
    Artifact,
    ArtifactRef,
    CitationError,
    CustomerScope,
    HumanApproval,
    OperationalEvent,
    Person,
    QualificationDecision,
    ScopeViolation,
    SourceEvidence,
    sha256_digest,
)
from auggie.store import Backend, IntegrityError, RecordConflict, RecordNotFound, Store
from auggie.store.memory import InMemoryBackend
from tests.conftest import Scenario, mongo_backend, variant


@pytest.fixture(params=["memory", "mongodb"])
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    if request.param == "memory":
        yield InMemoryBackend()
    else:
        with mongo_backend() as mongo:
            yield mongo


@pytest.fixture
def store(backend: Backend, scenario: Scenario) -> Store:
    return backend.open(scenario.scope)


@pytest.fixture
def other_scope() -> CustomerScope:
    return CustomerScope(
        customer_id="demo-other",
        synthetic=True,
        data_namespace="auggie_demo_other",
        credential_namespace="demo-other",
        knowledge_namespace="demo-other-strategy",
    )


def load(store: Store, scenario: Scenario, draft: Artifact) -> None:
    """Write the whole Northstar fixture, records before the events that refer to them."""
    store.put_person(scenario.person)
    for evidence in scenario.evidence:
        store.put_evidence(evidence)
    store.put_qualification(scenario.qualification)
    store.put_artifact(draft)
    for approval in scenario.approvals:
        store.put_approval(approval)
    for event in scenario.events:
        store.append_event(event)


def revise(draft: Artifact, content: str, **changes: object) -> Artifact:
    ref = variant(draft.ref, content_hash=sha256_digest(content))
    return Artifact.model_validate(
        variant(draft, ref=ref, content=content, supersedes=draft.ref.content_hash, **changes)
    )


def as_other_customer(record: SourceEvidence) -> SourceEvidence:
    return SourceEvidence.model_validate(variant(record, customer_id="demo-other"))


# Round trips and idempotency


def test_the_fixture_loads_and_reads_back(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    assert store.get_person("per-jordan-avery") == scenario.person
    assert store.list_evidence() == scenario.evidence
    assert store.get_qualification("qual-northstar-001") == scenario.qualification
    assert store.get_artifact(draft.ref) == draft
    assert store.latest_artifact(draft.ref.artifact_id) == draft
    assert store.list_approvals(draft.ref.artifact_id) == scenario.approvals
    assert store.get_approval("apr-northstar-delivery") == scenario.approvals[1]
    assert store.list_events() == scenario.events


def test_writing_an_identical_record_again_changes_nothing(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    load(store, scenario, draft)
    assert len(store.list_evidence()) == len(scenario.evidence)
    assert len(store.list_approvals(draft.ref.artifact_id)) == len(scenario.approvals)
    assert len(store.list_events()) == len(scenario.events)


def test_a_different_record_under_the_same_id_conflicts(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    evidence = scenario.evidence[0]
    with pytest.raises(RecordConflict):
        store.put_person(
            Person.model_validate(variant(scenario.person, email="other@northstar.example"))
        )
    with pytest.raises(RecordConflict):
        store.put_evidence(
            SourceEvidence.model_validate(variant(evidence, provider_request_id="req-2"))
        )
    with pytest.raises(RecordConflict):
        store.put_qualification(
            QualificationDecision.model_validate(
                variant(scenario.qualification, unknowns=["Something else."])
            )
        )
    with pytest.raises(RecordConflict):
        store.put_artifact(
            Artifact.model_validate(variant(draft, composed_at="2026-10-14T17:00:00Z"))
        )
    with pytest.raises(RecordConflict):
        store.put_approval(
            HumanApproval.model_validate(variant(scenario.approvals[0], note="Edited."))
        )


def test_missing_records_are_not_found(store: Store, draft: Artifact) -> None:
    with pytest.raises(RecordNotFound):
        store.get_person("per-missing")
    with pytest.raises(RecordNotFound):
        store.get_evidence("ev-missing")
    with pytest.raises(RecordNotFound):
        store.get_qualification("qual-missing")
    with pytest.raises(RecordNotFound):
        store.get_artifact(draft.ref)
    with pytest.raises(RecordNotFound):
        store.latest_artifact(draft.ref.artifact_id)
    with pytest.raises(RecordNotFound):
        store.get_approval("apr-missing")


# Customer isolation


def test_records_from_another_customer_are_refused(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    with pytest.raises(ScopeViolation):
        store.put_evidence(as_other_customer(scenario.evidence[0]))
    load(store, scenario, draft)
    foreign = "demo-other"
    with pytest.raises(ScopeViolation):
        store.put_person(Person.model_validate(variant(scenario.person, customer_id=foreign)))
    with pytest.raises(ScopeViolation):
        store.put_qualification(
            QualificationDecision.model_validate(
                variant(scenario.qualification, customer_id=foreign)
            )
        )
    with pytest.raises(ScopeViolation):
        store.put_artifact(Artifact.model_validate(variant(draft, customer_id=foreign)))
    with pytest.raises(ScopeViolation):
        store.put_approval(
            HumanApproval.model_validate(variant(scenario.approvals[0], customer_id=foreign))
        )
    with pytest.raises(ScopeViolation):
        store.append_event(
            OperationalEvent.model_validate(
                variant(scenario.events[0], customer_id=foreign, idempotency_key="other")
            )
        )


def test_scopes_on_one_backend_never_see_each_others_records(
    backend: Backend, scenario: Scenario, draft: Artifact, other_scope: CustomerScope
) -> None:
    northstar = backend.open(scenario.scope)
    load(northstar, scenario, draft)
    other = backend.open(other_scope)
    assert other.list_evidence() == ()
    assert other.list_events() == ()
    with pytest.raises(RecordNotFound):
        other.get_qualification(scenario.qualification.decision_id)
    other.put_evidence(as_other_customer(scenario.evidence[0]))
    assert northstar.get_evidence(scenario.evidence[0].evidence_id) == scenario.evidence[0]


def test_reopening_a_scope_reaches_the_same_records(
    backend: Backend, scenario: Scenario, draft: Artifact
) -> None:
    load(backend.open(scenario.scope), scenario, draft)
    assert backend.open(scenario.scope).list_events() == scenario.events


def test_a_scope_that_shares_a_namespace_cannot_open(
    backend: Backend, scenario: Scenario, other_scope: CustomerScope
) -> None:
    backend.open(scenario.scope)
    clash = CustomerScope.model_validate(
        variant(other_scope, data_namespace=scenario.scope.data_namespace)
    )
    with pytest.raises(ScopeViolation, match="shared"):
        backend.open(clash)


def test_a_customer_cannot_be_reopened_with_a_different_scope(
    backend: Backend, scenario: Scenario
) -> None:
    backend.open(scenario.scope)
    changed = CustomerScope.model_validate(variant(scenario.scope, data_namespace="auggie_moved"))
    with pytest.raises(ScopeViolation, match="already open"):
        backend.open(changed)


def test_evidence_from_another_scope_cannot_be_cited(
    backend: Backend, scenario: Scenario, other_scope: CustomerScope
) -> None:
    other = backend.open(other_scope)
    for evidence in scenario.evidence:
        other.put_evidence(as_other_customer(evidence))
    northstar = backend.open(scenario.scope)
    with pytest.raises(CitationError, match="not found"):
        northstar.put_qualification(scenario.qualification)


# Lineage


def test_a_qualification_cannot_cite_a_failed_retrieval(store: Store, scenario: Scenario) -> None:
    for evidence in scenario.evidence:
        store.put_evidence(evidence)
    findings = [f.model_dump(mode="json") for f in scenario.qualification.findings]
    findings[0]["evidence_ids"] = ["ev-northstar-news"]
    decision = QualificationDecision.model_validate(
        variant(scenario.qualification, findings=findings)
    )
    with pytest.raises(CitationError, match="ev-northstar-news: failed"):
        store.put_qualification(decision)


def test_an_artifact_needs_its_qualification_and_usable_evidence(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    for evidence in scenario.evidence:
        store.put_evidence(evidence)
    with pytest.raises(IntegrityError, match="qualification"):
        store.put_artifact(draft)
    store.put_qualification(scenario.qualification)
    citing_failure = Artifact.model_validate(
        variant(draft, evidence_ids=["ev-northstar-careers", "ev-northstar-news"])
    )
    with pytest.raises(CitationError, match="ev-northstar-news: failed"):
        store.put_artifact(citing_failure)
    other_account = Artifact.model_validate(variant(draft, account_id="acc-elsewhere"))
    with pytest.raises(IntegrityError, match="different accounts"):
        store.put_artifact(other_account)


def test_artifact_versions_form_one_line(store: Store, scenario: Scenario, draft: Artifact) -> None:
    load(store, scenario, draft)
    v2 = revise(draft, draft.content.replace("Would that be useful?", "Worth a look?"))
    store.put_artifact(v2)
    assert store.latest_artifact(draft.ref.artifact_id) == v2
    assert store.get_artifact(draft.ref) == draft
    fork = revise(draft, draft.content + "\nP.S. A fork.\n")
    with pytest.raises(IntegrityError, match="must supersede the latest"):
        store.put_artifact(fork)
    with pytest.raises(IntegrityError, match="cannot change recipient_id"):
        store.put_artifact(revise(v2, v2.content + "\n", recipient_id="per-someone-else"))


def test_a_first_version_cannot_claim_to_supersede(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    for evidence in scenario.evidence:
        store.put_evidence(evidence)
    store.put_qualification(scenario.qualification)
    with pytest.raises(IntegrityError, match="first version"):
        store.put_artifact(revise(draft, draft.content + "\n"))


def test_an_approval_needs_the_exact_stored_version(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    unknown = variant(
        scenario.approvals[0].artifact, content_hash=sha256_digest("a version nobody stored")
    )
    with pytest.raises(IntegrityError, match="has no version"):
        store.put_approval(
            HumanApproval.model_validate(
                variant(scenario.approvals[0], approval_id="apr-x", artifact=unknown)
            )
        )


def test_a_delivery_approval_must_target_the_drafts_recipient(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    delivery = scenario.approval_by_id("apr-northstar-delivery")
    wrong = variant(
        delivery,
        approval_id="apr-wrong-recipient",
        target={"platform": "simulated", "recipient_id": "per-someone-else"},
    )
    with pytest.raises(IntegrityError, match="draft's own recipient"):
        store.put_approval(HumanApproval.model_validate(wrong))


# Events


def test_an_idempotency_key_stores_one_fact(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    original = scenario.events[2]
    retry = OperationalEvent.model_validate(
        variant(original, event_id="evt-retry", occurred_at="2026-10-14T16:30:00Z")
    )
    appended = store.append_event(retry)
    assert appended.created is False
    assert appended.event == original
    assert store.list_events().count(original) == 1
    assert len(store.list_events()) == len(scenario.events)


def test_an_idempotency_key_cannot_name_a_different_fact(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    clash = variant(
        scenario.events[2],
        event_id="evt-clash",
        refs=[{"kind": "evidence", "id": "ev-northstar-news"}],
    )
    with pytest.raises(RecordConflict, match="different fact"):
        store.append_event(OperationalEvent.model_validate(clash))


def test_event_ids_are_unique(store: Store, scenario: Scenario, draft: Artifact) -> None:
    load(store, scenario, draft)
    reused = variant(scenario.events[2], idempotency_key="evidence.recorded:again")
    with pytest.raises(RecordConflict, match="already used"):
        store.append_event(OperationalEvent.model_validate(reused))


def test_event_refs_must_resolve_to_stored_records(store: Store, scenario: Scenario) -> None:
    with pytest.raises(IntegrityError, match="person:per-jordan-avery"):
        store.append_event(scenario.events[1])
    with pytest.raises(IntegrityError, match="evidence:ev-northstar-careers"):
        store.append_event(scenario.events[2])
    # Strategy and account records live outside the Store.
    assert store.append_event(scenario.events[0]).created


def test_lists_filter_and_keep_write_order(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    recorded = store.list_events(type="evidence.recorded")
    assert [e.event_id for e in recorded] == ["evt-003", "evt-004", "evt-005"]
    run = store.list_events(run_id="run-001")
    assert [e.event_id for e in run] == [f"evt-{n:03d}" for n in range(3, 11)]
    subject = scenario.evidence[0].subject
    assert store.list_evidence(subject=subject) == scenario.evidence
    assert store.list_approvals("draft-elsewhere") == ()


def test_an_artifact_is_found_only_by_its_exact_ref(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    load(store, scenario, draft)
    as_brief = variant(draft.ref, kind="brief")
    with pytest.raises(RecordNotFound):
        store.get_artifact(ArtifactRef.model_validate(as_brief))
    approval = variant(scenario.approvals[0], approval_id="apr-brief", artifact=as_brief)
    with pytest.raises(IntegrityError, match="has no version"):
        store.put_approval(HumanApproval.model_validate(approval))


@pytest.mark.parametrize(
    "lookup",
    [
        "get_person",
        "get_evidence",
        "get_qualification",
        "latest_artifact",
        "get_approval",
        "list_approvals",
    ],
)
def test_lookups_take_literal_ids_only(
    store: Store, scenario: Scenario, draft: Artifact, lookup: str
) -> None:
    load(store, scenario, draft)
    with pytest.raises(TypeError, match="must be a string"):
        getattr(store, lookup)({"$ne": None})


@pytest.mark.parametrize("name", ["type", "run_id"])
def test_event_filters_take_literal_values_only(
    store: Store, scenario: Scenario, draft: Artifact, name: str
) -> None:
    load(store, scenario, draft)
    query_object: dict[str, Any] = {name: {"$ne": None}}  # what an unchecked caller might pass
    with pytest.raises(TypeError, match="must be a string"):
        store.list_events(**query_object)


def test_a_drafts_recipient_must_belong_to_its_account(
    store: Store, scenario: Scenario, draft: Artifact
) -> None:
    store.put_person(scenario.person)
    for evidence in scenario.evidence:
        store.put_evidence(evidence)
    store.put_qualification(scenario.qualification)
    elsewhere = Person.model_validate(
        variant(scenario.person, person_id="per-elsewhere", account_id="acc-elsewhere")
    )
    store.put_person(elsewhere)
    misaddressed = Artifact.model_validate(variant(draft, recipient_id="per-elsewhere"))
    with pytest.raises(IntegrityError, match="recipient belongs to another account"):
        store.put_artifact(misaddressed)


def test_careers_snapshots_are_stored_kept_and_scoped(store: Store, scenario: Scenario) -> None:
    from auggie.contracts import CareersSnapshot
    from tests.test_careers_contract import snapshot as make

    store.put_person(scenario.person)
    first = make(customer_id="demo-msp", account_id="acc-northstar", snapshot_id="careers-1")
    second = make(
        customer_id="demo-msp",
        account_id="acc-northstar",
        snapshot_id="careers-2",
        status="failed",
        roles=(),
        failure_reason="Timed out.",
    )
    store.put_careers_snapshot(first)
    store.put_careers_snapshot(first)  # the same record again changes nothing
    store.put_careers_snapshot(second)
    assert store.get_careers_snapshot("careers-1") == first
    assert store.list_careers_snapshots("acc-northstar") == (first, second)
    assert store.list_careers_snapshots("acc-elsewhere") == ()
    with pytest.raises(RecordConflict):
        store.put_careers_snapshot(
            CareersSnapshot.model_validate(variant(first, read_at="2026-10-19T00:00:00Z"))
        )
    with pytest.raises(ScopeViolation):
        store.put_careers_snapshot(
            CareersSnapshot.model_validate(variant(first, customer_id="demo-other"))
        )
    with pytest.raises(RecordNotFound):
        store.get_careers_snapshot("careers-missing")
    with pytest.raises(TypeError, match="must be a string"):
        store.list_careers_snapshots({"$ne": None})  # type: ignore[arg-type]
    event = OperationalEvent.model_validate(
        {
            "event_id": "evt-careers",
            "customer_id": "demo-msp",
            "type": "careers.recorded",
            "occurred_at": "2026-10-18T09:00:00Z",
            "actor": {"kind": "service", "id": "careers-reader", "version": "1"},
            "refs": [
                {"kind": "careers", "id": "careers-9"},
                {"kind": "account", "id": "acc-northstar"},
            ],
            "idempotency_key": "careers.recorded:careers-9",
        }
    )
    with pytest.raises(IntegrityError, match="careers:careers-9"):
        store.append_event(event)
