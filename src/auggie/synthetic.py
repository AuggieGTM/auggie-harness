"""Run a synthetic scenario from its inputs: the whole slice, end to end, in one process.

A scenario directory holds ``inputs/inputs.json`` and the strategy files it
names. Reviewer decisions, the delivery request and the outcome in the inputs
stand in for what a person does. Records go to an in-memory Store unless a
backend is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import StringConstraints

from auggie.accounts import Account
from auggie.acquisition.fixture import FixtureAcquisition, RecordedResponse
from auggie.approval import ReviewDecision
from auggie.contracts import Actor, Contract, CustomerScope, HumanApproval
from auggie.contracts.approval import DeliveryPlatform
from auggie.contracts.common import UtcDatetime
from auggie.judgment.rules import RulesQualifier
from auggie.pipeline import (
    OutcomeReport,
    RunResult,
    RunTimes,
    activate_strategy,
    deliver,
    record_outcome,
    record_person,
    record_review,
    research_run,
)
from auggie.store import Appended, Backend, Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import LoadedStrategy, load_strategy


class StrategyInput(Contract):
    path: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    activated_by: Actor
    activated_at: UtcDatetime


class DeliveryRequest(Contract):
    platform: DeliveryPlatform
    requested_at: UtcDatetime


class SyntheticInputs(Contract):
    note: str
    scope: CustomerScope
    strategy: StrategyInput
    account: Account
    responses: tuple[RecordedResponse, ...]
    run: RunTimes
    reviews: tuple[ReviewDecision, ...]
    delivery: DeliveryRequest | None = None
    outcome: OutcomeReport | None = None


def load_inputs(directory: Path) -> SyntheticInputs:
    path = directory / "inputs" / "inputs.json"
    return SyntheticInputs.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ScenarioRun:
    store: Store
    strategy: LoadedStrategy
    activation: Appended
    result: RunResult
    approvals: tuple[HumanApproval, ...]
    delivery: Appended | None
    outcome: Appended | None


def run_scenario(
    directory: Path, *, backend: Backend | None = None, inputs: SyntheticInputs | None = None
) -> ScenarioRun:
    """Run every stage the inputs call for, in order. Blocked steps raise.

    ``inputs`` replaces the directory's own inputs; its strategy is still read
    from the directory.
    """
    inputs = inputs or load_inputs(directory)
    if not inputs.scope.synthetic:
        raise ValueError("synthetic scenarios must use a synthetic scope")
    store = (backend or InMemoryBackend()).open(inputs.scope)
    strategy = load_strategy(
        directory / "inputs",
        inputs.strategy.path,
        commit=inputs.strategy.commit,
        scope=inputs.scope,
    )
    activation = activate_strategy(
        store, strategy, by=inputs.strategy.activated_by, at=inputs.strategy.activated_at
    )
    record_person(
        store,
        account=inputs.account,
        by=inputs.strategy.activated_by,
        at=inputs.strategy.activated_at,
    )
    result = research_run(
        store,
        strategy=strategy,
        account=inputs.account,
        acquisition=FixtureAcquisition(inputs.responses),
        qualifier=RulesQualifier(),
        times=inputs.run,
    )
    if result.draft is None:
        return ScenarioRun(store, strategy, activation, result, (), None, None)
    shown = result.draft.ref
    approvals: list[HumanApproval] = []
    for review in inputs.reviews:
        seen = sum(a.scope == review.scope for a in approvals)
        suffix = f"-{seen + 1}" if seen else ""
        approval_id = f"apr-{inputs.account.key}-{review.scope}{suffix}"
        approvals.append(record_review(store, shown=shown, review=review, approval_id=approval_id))
    delivery = None
    if inputs.delivery is not None:
        delivery = deliver(
            store,
            requested=shown,
            platform=inputs.delivery.platform,
            at=inputs.delivery.requested_at,
        )
    outcome = None
    if inputs.outcome is not None:
        outcome = record_outcome(store, artifact_id=shown.artifact_id, report=inputs.outcome)
    return ScenarioRun(store, strategy, activation, result, tuple(approvals), delivery, outcome)
