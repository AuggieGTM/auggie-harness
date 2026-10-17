"""Operational events: append-only facts about what happened in the loop."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal, get_args

from pydantic import Field, StringConstraints, field_validator, model_validator

from auggie.contracts.common import Actor, Contract, RecordId, Slug, UtcDatetime

Stage = Literal["define", "observe", "validate", "judge", "compose", "approve", "deliver", "learn"]
STAGES: tuple[Stage, ...] = get_args(Stage)
"""The loop, in order: Define, Observe, Validate, Judge, Compose, Approve, Deliver, Learn."""

EventType = Literal[
    "strategy.activated",
    "evidence.recorded",
    "evidence.validated",
    "evidence.rejected",
    "qualification.decided",
    "draft.composed",
    "approval.recorded",
    "delivery.simulated",
    "outcome.recorded",
]

RecordKind = Literal[
    "strategy", "account", "person", "evidence", "qualification", "artifact", "approval"
]

OutcomeKind = Literal[
    "positive_reply", "negative_reply", "meeting_booked", "opt_out", "bounce", "no_reply"
]
"""Observed results of a delivery. ``no_reply`` is not treated as a negative result."""

EVENT_STAGE: Mapping[EventType, Stage] = MappingProxyType(
    {
        "strategy.activated": "define",
        "evidence.recorded": "observe",
        "evidence.validated": "validate",
        "evidence.rejected": "validate",
        "qualification.decided": "judge",
        "draft.composed": "compose",
        "approval.recorded": "approve",
        "delivery.simulated": "deliver",
        "outcome.recorded": "learn",
    }
)

REQUIRED_REFS: Mapping[EventType, frozenset[RecordKind]] = MappingProxyType(
    {
        "strategy.activated": frozenset({"strategy"}),
        "evidence.recorded": frozenset({"evidence"}),
        "evidence.validated": frozenset({"evidence"}),
        "evidence.rejected": frozenset({"evidence"}),
        "qualification.decided": frozenset({"qualification", "account"}),
        "draft.composed": frozenset({"artifact", "qualification"}),
        "approval.recorded": frozenset({"approval", "artifact"}),
        "delivery.simulated": frozenset({"approval", "artifact", "person"}),
        "outcome.recorded": frozenset({"artifact", "person"}),
    }
)
"""Record kinds each event type must reference, so lineage can always be walked."""

AttributeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
AttributeValue = Annotated[str, StringConstraints(max_length=500)] | int | float | bool | None


class RecordRef(Contract):
    kind: RecordKind
    id: RecordId


class OperationalEvent(Contract):
    """Something that happened, recorded once and never edited.

    Consumers deduplicate on ``idempotency_key``: two events with the same key
    describe the same fact. ``attributes`` is a small flat map for operational
    detail. It must never hold credentials or confidential customer content.
    """

    schema_version: Literal[1] = 1
    event_id: RecordId
    customer_id: Slug
    type: EventType
    occurred_at: UtcDatetime
    actor: Actor
    refs: Annotated[tuple[RecordRef, ...], Field(min_length=1)]
    idempotency_key: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    run_id: RecordId | None = None
    attributes: dict[AttributeKey, AttributeValue] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def _bounded(cls, value: dict[str, AttributeValue]) -> dict[str, AttributeValue]:
        if len(value) > 32:
            raise ValueError("attributes is limited to 32 entries")
        return value

    @model_validator(mode="after")
    def _check_type_rules(self) -> OperationalEvent:
        missing = REQUIRED_REFS[self.type] - {ref.kind for ref in self.refs}
        if missing:
            raise ValueError(f"{self.type} must reference: {', '.join(sorted(missing))}")
        if self.type == "approval.recorded" and self.actor.kind != "human":
            raise ValueError("approval.recorded must be performed by a human")
        if self.type == "outcome.recorded":
            outcome = self.attributes.get("outcome")
            if outcome not in get_args(OutcomeKind):
                raise ValueError(
                    f"outcome.recorded needs attributes.outcome in {get_args(OutcomeKind)}"
                )
        return self

    @property
    def stage(self) -> Stage:
        return EVENT_STAGE[self.type]
