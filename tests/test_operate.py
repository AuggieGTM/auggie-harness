"""The operator's flow for one prospect, on the synthetic Northstar prospect (offline)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest

from auggie.acquisition.fixture import FixtureAcquisition
from auggie.delivery import DeliveryBlocked
from auggie.operate import deliver_draft, report_outcome, research_prospect, review_draft
from auggie.pipeline import RunResult
from auggie.store import Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from auggie.synthetic import load_inputs
from tests.conftest import NORTHSTAR


def clock() -> Iterator[datetime]:
    moment = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
    while True:
        yield moment
        moment += timedelta(minutes=1)


@pytest.fixture
def store() -> Store:
    return InMemoryBackend().open(load_inputs(NORTHSTAR).scope)


def research(store: Store) -> RunResult:
    inputs = load_inputs(NORTHSTAR)
    strategy = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit=inputs.strategy.commit, scope=store.scope
    )
    ticks = clock()
    return research_prospect(
        store,
        strategy=strategy,
        prospect=inputs.account,
        acquisition=FixtureAcquisition(inputs.responses),
        operator="demo-operator",
        now=lambda: next(ticks),
    )


def answers(*replies: str) -> tuple[list[str], Callable[[str], str]]:
    asked: list[str] = []
    pending = list(replies)

    def ask(question: str) -> str:
        asked.append(question)
        return pending.pop(0)

    return asked, ask


def test_a_prospect_is_researched_to_a_draft_with_its_own_run_ids(store: Store) -> None:
    first, second = research(store), research(store)
    assert first.draft is not None and second.draft is not None
    assert first.draft.ref.artifact_id.startswith("draft-northstar-run-")
    assert first.draft.ref.artifact_id != second.draft.ref.artifact_id
    assert len(store.list_events(type="strategy.activated")) == 1
    assert len(store.list_events(type="draft.composed")) == 2


def test_the_full_flow_reviews_delivers_and_records_outcomes(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    shown: list[str] = []
    asked, ask = answers("y", "Claims match the cited pages.", "y")
    approvals = review_draft(
        store,
        draft_id=draft.ref.artifact_id,
        reviewer="demo-reviewer",
        ask=ask,
        out=shown.append,
    )
    assert [(a.scope, a.decision) for a in approvals] == [
        ("content", "approved"),
        ("delivery", "approved"),
    ]
    assert (
        approvals[1].target is not None and approvals[1].target.recipient_id == "per-jordan-avery"
    )
    assert any("| Hi Jordan," in line for line in shown)
    assert any("https://northstar.example/careers/it-operations-manager" in line for line in shown)
    assert len(asked) == 3
    assert deliver_draft(store, draft_id=draft.ref.artifact_id).created
    first = report_outcome(
        store, draft_id=draft.ref.artifact_id, outcome="no_reply", reporter="demo-reviewer"
    )
    second = report_outcome(
        store, draft_id=draft.ref.artifact_id, outcome="positive_reply", reporter="demo-reviewer"
    )
    assert first.event.idempotency_key.endswith(":1") and second.event.idempotency_key.endswith(
        ":2"
    )


@pytest.mark.parametrize(
    ("replies", "blocked"),
    [
        (("c", "Too long."), "rejected or asked for changes"),
        (("n", ""), "rejected or asked for changes"),
        (("y", "", "n"), "no delivery approval"),
    ],
    ids=["changes", "rejected", "content-only"],
)
def test_delivery_waits_for_both_approvals(
    replies: tuple[str, ...], blocked: str, store: Store
) -> None:
    draft = research(store).draft
    assert draft is not None
    _, ask = answers(*replies)
    review_draft(
        store, draft_id=draft.ref.artifact_id, reviewer="demo-reviewer", ask=ask, out=lambda _: None
    )
    with pytest.raises(DeliveryBlocked, match=blocked):
        deliver_draft(store, draft_id=draft.ref.artifact_id)


def test_an_outcome_needs_a_delivery(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    with pytest.raises(ValueError, match="never delivered"):
        report_outcome(store, draft_id=draft.ref.artifact_id, outcome="no_reply", reporter="x")
