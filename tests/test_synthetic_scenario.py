"""The synthetic Northstar scenario walks the first planned vertical slice.

Research -> validate evidence -> qualify -> draft -> human approval ->
simulated delivery -> record outcome. These tests check that the fixture is
internally consistent, so it can later drive the real workflow.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from auggie.contracts import (
    STAGES,
    DeliveryTarget,
    ensure_in_scope,
    sha256_digest,
    verify_citations,
)
from auggie.contracts.scope import CustomerBound
from tests.conftest import EXAMPLES, NORTHSTAR, Scenario

RESERVED_HOST = re.compile(
    r"(^|\.)(example|test|invalid|localhost)$|^example\.(com|net|org)$", re.IGNORECASE
)


def test_scenario_is_synthetic_and_cannot_deliver_live(scenario: Scenario) -> None:
    assert scenario.scope.synthetic
    assert scenario.scope.execution.delivery == "simulated"
    assert not scenario.scope.execution.external_providers


def test_every_record_is_in_scope(scenario: Scenario) -> None:
    records: tuple[CustomerBound, ...] = (
        *scenario.evidence,
        scenario.qualification,
        *scenario.approvals,
        *scenario.events,
    )
    for record in records:
        ensure_in_scope(scenario.scope, record)


def test_qualification_cites_only_usable_evidence(scenario: Scenario) -> None:
    verify_citations(scenario.qualification, scenario.evidence)


def test_failed_retrieval_is_kept_visible_as_an_unknown(scenario: Scenario) -> None:
    failed = [e for e in scenario.evidence if not e.usable]
    assert [e.evidence_id for e in failed] == ["ev-northstar-news"]
    assert failed[0].evidence_id not in scenario.qualification.cited_evidence_ids
    assert scenario.qualification.unknowns


def test_approvals_are_bound_to_the_draft_on_disk(scenario: Scenario) -> None:
    draft_bytes = (NORTHSTAR / scenario.draft.file).read_bytes()
    assert sha256_digest(draft_bytes) == scenario.draft.artifact.content_hash
    for approval in scenario.approvals:
        assert approval.artifact == scenario.draft.artifact


def test_simulated_delivery_was_authorized_when_it_happened(scenario: Scenario) -> None:
    target = DeliveryTarget(platform="simulated", recipient_id=scenario.draft.recipient_id)
    for delivered in (e for e in scenario.events if e.type == "delivery.simulated"):
        approval_ids = [ref.id for ref in delivered.refs if ref.kind == "approval"]
        assert any(
            scenario.approval_by_id(approval_id).authorizes(
                artifact=scenario.draft.artifact,
                scope="delivery",
                target=target,
                at=delivered.occurred_at,
            )
            for approval_id in approval_ids
        )


def test_events_cover_the_loop_in_order(scenario: Scenario) -> None:
    events = sorted(scenario.events, key=lambda e: e.occurred_at)
    assert list(events) == list(scenario.events), "fixture events should be chronological"
    stage_positions = [STAGES.index(e.stage) for e in events]
    assert stage_positions == sorted(stage_positions)
    assert {e.stage for e in events} == set(STAGES)


def test_event_idempotency_keys_are_unique(scenario: Scenario) -> None:
    keys = [e.idempotency_key for e in scenario.events]
    assert len(keys) == len(set(keys))


def test_event_refs_resolve_to_scenario_records(scenario: Scenario) -> None:
    known = {
        "evidence": {e.evidence_id for e in scenario.evidence},
        "qualification": {scenario.qualification.decision_id},
        "account": {scenario.qualification.account_id},
        "artifact": {scenario.draft.artifact.artifact_id},
        "approval": {a.approval_id for a in scenario.approvals},
        "person": {scenario.draft.recipient_id},
    }
    for event in scenario.events:
        for ref in event.refs:
            if ref.kind in known:
                assert ref.id in known[ref.kind], f"{event.event_id} -> {ref.kind}:{ref.id}"


def test_records_round_trip_through_json(scenario: Scenario) -> None:
    records: list[BaseModel] = [
        scenario.scope,
        *scenario.evidence,
        scenario.qualification,
        *scenario.approvals,
        *scenario.events,
    ]
    for record in records:
        assert type(record).model_validate_json(record.model_dump_json()) == record


def _example_files() -> list[Path]:
    return [path for path in EXAMPLES.rglob("*") if path.is_file()]


def test_examples_use_only_reserved_domains() -> None:
    url_host = re.compile(r"https?://([^/\s\"'<>)]+)")
    email_host = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
    files = _example_files()
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        for host in url_host.findall(text) + email_host.findall(text):
            hostname = host.split(":")[0].lower()
            assert RESERVED_HOST.search(hostname), f"{path.name}: non-reserved host {hostname!r}"
