from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from auggie.contracts import EVENT_STAGE, REQUIRED_REFS, STAGES, OperationalEvent
from auggie.contracts.events import EventType
from tests.conftest import Scenario, variant


def event_of_type(scenario: Scenario, event_type: str) -> OperationalEvent:
    return next(e for e in scenario.events if e.type == event_type)


def test_loop_stages_are_in_order() -> None:
    assert STAGES == (
        "define",
        "observe",
        "validate",
        "judge",
        "compose",
        "approve",
        "deliver",
        "learn",
    )


def test_every_event_type_has_a_stage_and_required_refs() -> None:
    assert set(EVENT_STAGE) == set(get_args(EventType))
    assert set(REQUIRED_REFS) == set(get_args(EventType))


def test_every_stage_has_an_event_type() -> None:
    assert set(EVENT_STAGE.values()) == set(STAGES)


def test_simulated_delivery_must_reference_an_approval(scenario: Scenario) -> None:
    delivered = event_of_type(scenario, "delivery.simulated")
    refs = [ref.model_dump() for ref in delivered.refs if ref.kind != "approval"]
    with pytest.raises(ValidationError, match="must reference: approval"):
        OperationalEvent.model_validate(variant(delivered, refs=refs))


def test_approvals_are_recorded_by_humans(scenario: Scenario) -> None:
    approved = event_of_type(scenario, "approval.recorded")
    data = variant(approved, actor={"kind": "service", "id": "auto-approver"})
    with pytest.raises(ValidationError, match="must be performed by a human"):
        OperationalEvent.model_validate(data)


@pytest.mark.parametrize("attributes", [{}, {"outcome": "probably_interested"}])
def test_outcomes_use_known_kinds(scenario: Scenario, attributes: dict[str, str]) -> None:
    outcome = event_of_type(scenario, "outcome.recorded")
    with pytest.raises(ValidationError, match=r"attributes\.outcome"):
        OperationalEvent.model_validate(variant(outcome, attributes=attributes))


def test_attributes_are_bounded(scenario: Scenario) -> None:
    event = event_of_type(scenario, "evidence.recorded")
    too_many = {f"key_{i}": i for i in range(33)}
    with pytest.raises(ValidationError, match="limited to 32 entries"):
        OperationalEvent.model_validate(variant(event, attributes=too_many))


def test_attribute_values_are_flat(scenario: Scenario) -> None:
    event = event_of_type(scenario, "evidence.recorded")
    with pytest.raises(ValidationError):
        OperationalEvent.model_validate(variant(event, attributes={"nested": {"a": 1}}))


def test_event_stage_is_derived_from_type(scenario: Scenario) -> None:
    assert event_of_type(scenario, "qualification.decided").stage == "judge"
    assert event_of_type(scenario, "outcome.recorded").stage == "learn"
