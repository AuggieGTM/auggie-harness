"""The Northstar scenario is produced by the workflow from its inputs, not prewritten."""

from __future__ import annotations

import pytest

from auggie.acquisition.fixture import FixtureAcquisition
from auggie.judgment.rules import RulesQualifier
from auggie.pipeline import research_run
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from auggie.synthetic import ScenarioRun, load_inputs, run_scenario
from tests.conftest import NORTHSTAR, Scenario, mongo_backend


@pytest.fixture
def run() -> ScenarioRun:
    return run_scenario(NORTHSTAR)


def test_the_workflow_reproduces_every_scenario_record(
    run: ScenarioRun, scenario: Scenario
) -> None:
    store = run.store
    assert store.scope == scenario.scope
    assert store.list_evidence() == scenario.evidence
    assert store.get_qualification(scenario.qualification.decision_id) == scenario.qualification
    assert store.list_approvals(scenario.draft.artifact.artifact_id) == scenario.approvals
    assert store.list_events() == scenario.events


def test_the_workflow_reproduces_the_draft_byte_for_byte(
    run: ScenarioRun, scenario: Scenario
) -> None:
    draft = run.store.latest_artifact(scenario.draft.artifact.artifact_id)
    assert draft.ref == scenario.draft.artifact
    assert draft.content.encode("utf-8") == (NORTHSTAR / scenario.draft.file).read_bytes()
    assert draft.recipient_id == scenario.draft.recipient_id


def test_each_stage_reports_what_it_did(run: ScenarioRun) -> None:
    assert run.activation.created
    assert [v.rejected_for for v in run.result.verdicts] == [None, None, "retrieval_failed"]
    assert run.result.decision.outcome == "qualified"
    assert run.result.draft is not None
    assert [a.scope for a in run.approvals] == ["content", "delivery"]
    assert run.delivery is not None and run.delivery.created
    assert run.outcome is not None and run.outcome.event.attributes == {"outcome": "positive_reply"}


def test_running_the_same_inputs_again_writes_nothing_new(scenario: Scenario) -> None:
    backend = InMemoryBackend()
    run_scenario(NORTHSTAR, backend=backend)
    again = run_scenario(NORTHSTAR, backend=backend)
    assert not again.activation.created
    assert again.delivery is not None and not again.delivery.created
    assert again.store.list_events() == scenario.events


def test_the_workflow_reproduces_the_scenario_on_mongodb(scenario: Scenario) -> None:
    with mongo_backend() as backend:
        run = run_scenario(NORTHSTAR, backend=backend)
        assert run.store.list_evidence() == scenario.evidence
        assert run.store.list_approvals(scenario.draft.artifact.artifact_id) == scenario.approvals
        assert run.store.list_events() == scenario.events
        again = run_scenario(NORTHSTAR, backend=backend)
        assert again.store.list_events() == scenario.events


def test_per_run_ids_let_a_prospect_be_researched_again(scenario: Scenario) -> None:
    inputs = load_inputs(NORTHSTAR)
    backend = InMemoryBackend()
    store = backend.open(inputs.scope)
    strategy = load_strategy(
        NORTHSTAR / "inputs",
        inputs.strategy.path,
        commit=inputs.strategy.commit,
        scope=inputs.scope,
    )
    results = []
    for run_id in ("run-a", "run-b"):
        times = inputs.run.model_copy(update={"run_id": run_id})
        results.append(
            research_run(
                store,
                strategy=strategy,
                account=inputs.account,
                acquisition=FixtureAcquisition(inputs.responses),
                qualifier=RulesQualifier(),
                times=times,
                per_run_ids=True,
            )
        )
    first, second = results
    assert first.decision.decision_id == "qual-northstar-run-a"
    assert second.draft is not None and second.draft.ref.artifact_id == "draft-northstar-run-b"
    assert len(store.list_evidence()) == 6
