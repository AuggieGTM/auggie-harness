"""The operator's flow for one prospect, on the synthetic Northstar prospect (offline)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest

from auggie.acquisition.fixture import FixtureAcquisition
from auggie.contracts import ArtifactRef, ScopeViolation, sha256_digest
from auggie.delivery import DeliveryBlocked
from auggie.operate import (
    deliver_draft,
    replay_clock,
    report_outcome,
    research_prospect,
    review_draft,
    revise_draft,
)
from auggie.pipeline import RunResult, _revision_event, complete_revision, record_revision
from auggie.store import RecordConflict, Store, StoreError
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
    assert any("to Jordan <jordan.avery@northstar.example>" in line for line in shown)
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


def test_another_customers_prospect_is_refused_before_anything_is_written(store: Store) -> None:
    inputs = load_inputs(NORTHSTAR)
    strategy = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit=inputs.strategy.commit, scope=store.scope
    )
    foreign = inputs.account.model_copy(update={"customer_id": "other-customer"})
    with pytest.raises(ScopeViolation, match="other-customer"):
        research_prospect(
            store,
            strategy=strategy,
            prospect=foreign,
            acquisition=FixtureAcquisition(inputs.responses),
            operator="demo-operator",
        )
    assert store.list_events() == () and store.list_evidence() == ()


def test_a_rehearsal_is_judged_as_of_its_recordings_whatever_the_date(store: Store) -> None:
    inputs = load_inputs(NORTHSTAR)
    strategy = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit=inputs.strategy.commit, scope=store.scope
    )
    as_of = replay_clock(inputs.responses)
    assert as_of() == max(r.observed_at for r in inputs.responses)
    result = research_prospect(
        store,
        strategy=strategy,
        prospect=inputs.account,
        acquisition=FixtureAcquisition(inputs.responses),
        operator="demo-operator",
        now=as_of,
    )
    assert result.decision.decided_at == as_of() and result.draft is not None


def test_each_stage_is_stamped_when_it_happens(store: Store) -> None:
    research(store)
    events = store.list_events()
    times = {e.type: e.occurred_at for e in events}
    assert (
        times["strategy.activated"]
        < times["evidence.validated"]
        < times["qualification.decided"]
        < times["draft.composed"]
    )


def test_a_new_research_never_reuses_an_existing_run_id(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = iter(["run-fixed", "run-fixed", "run-other"])
    monkeypatch.setattr("auggie.operate.new_run_id", lambda at: next(ids))
    first, second = research(store), research(store)
    assert first.draft is not None and second.draft is not None
    assert first.draft.ref.artifact_id == "draft-northstar-run-fixed"
    assert second.draft.ref.artifact_id == "draft-northstar-run-other"


def test_research_stops_rather_than_reuse_a_run(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("auggie.operate.new_run_id", lambda at: "run-fixed")
    research(store)
    with pytest.raises(StoreError, match="unused run ID"):
        research(store)


def review(store: Store, draft_id: str, *replies: str) -> None:
    _, ask = answers(*replies)
    review_draft(store, draft_id=draft_id, reviewer="demo-reviewer", ask=ask, out=lambda _: None)


def test_a_revision_is_a_new_version_that_needs_its_own_review(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    draft_id = draft.ref.artifact_id
    review(store, draft_id, "c", "Drop the last paragraph.")
    shown: list[str] = []
    seen: list[str] = []

    def edit(text: str) -> str:
        seen.append(text)
        return text.replace("Would that be useful?\n\n", "")

    revised = revise_draft(
        store, draft_id=draft_id, author="demo-seller", edit=edit, out=shown.append
    )

    assert revised is not None
    assert seen == [draft.content]
    assert shown == ["review    changes_requested by demo-reviewer: Drop the last paragraph."]
    assert revised.supersedes == draft.ref.content_hash
    assert revised.composed_by.kind == "human" and revised.composed_by.id == "demo-seller"
    assert revised.evidence_ids == draft.evidence_ids
    assert store.latest_artifact(draft_id) == revised
    (event,) = [e for e in store.list_events(type="draft.composed") if e.actor.id == "demo-seller"]
    assert event.attributes["supersedes"] == draft.ref.content_hash
    with pytest.raises(DeliveryBlocked, match="no content approval"):
        deliver_draft(store, draft_id=draft_id)
    review(store, draft_id, "y", "", "y")
    assert deliver_draft(store, draft_id=draft_id).created


def test_an_unchanged_revision_records_nothing(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    shown: list[str] = []
    unchanged = revise_draft(
        store,
        draft_id=draft.ref.artifact_id,
        author="demo-seller",
        edit=lambda text: text,
        out=shown.append,
    )
    assert unchanged is None
    assert shown == [f"unchanged {draft.ref.artifact_id}: nothing recorded"]
    assert store.latest_artifact(draft.ref.artifact_id) == draft


@pytest.mark.parametrize("delivered", [False, True], ids=["emptied", "delivered"])
def test_a_revision_is_refused_without_recording(delivered: bool, store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    draft_id = draft.ref.artifact_id
    if delivered:
        review(store, draft_id, "y", "", "y")
        deliver_draft(store, draft_id=draft_id)
    events = len(store.list_events())
    with pytest.raises(ValueError, match="already delivered" if delivered else "empty"):
        revise_draft(
            store, draft_id=draft_id, author="demo-seller", edit=lambda _: " \n", out=print
        )
    assert store.latest_artifact(draft_id) == draft
    assert len(store.list_events()) == events


def test_a_draft_delivered_while_it_is_being_edited_is_not_revised(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    draft_id = draft.ref.artifact_id
    review(store, draft_id, "y", "", "y")

    def edit_while_someone_delivers(text: str) -> str:
        deliver_draft(store, draft_id=draft_id)
        return text + "PS\n"

    with pytest.raises(ValueError, match="already delivered"):
        revise_draft(
            store,
            draft_id=draft_id,
            author="demo-seller",
            edit=edit_while_someone_delivers,
            out=print,
        )
    assert store.latest_artifact(draft_id) == draft
    assert not [e for e in store.list_events(type="draft.composed") if e.actor.id == "demo-seller"]


def test_reverting_to_an_earlier_text_is_refused_without_recording(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    draft_id = draft.ref.artifact_id
    revised = revise_draft(
        store, draft_id=draft_id, author="demo-seller", edit=lambda t: t + "PS\n", out=print
    )
    assert revised is not None
    events = len(store.list_events())
    with pytest.raises(ValueError, match="earlier version"):
        revise_draft(
            store, draft_id=draft_id, author="demo-seller", edit=lambda _: draft.content, out=print
        )
    assert store.latest_artifact(draft_id) == revised
    assert len(store.list_events()) == events


def test_only_the_latest_stored_version_can_be_revised(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    by = draft.composed_by.model_copy(update={"kind": "human", "id": "demo-seller"})
    record_revision(
        store, previous=draft.ref, content=draft.content + "PS\n", by=by, at=draft.composed_at
    )
    with pytest.raises(ValueError, match="newer version"):
        record_revision(
            store, previous=draft.ref, content=draft.content + "PPS\n", by=by, at=draft.composed_at
        )


def test_a_revision_of_the_longest_draft_id_keeps_its_event(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    long_id = "draft-" + "x" * 122
    first = draft.model_copy(
        update={
            "ref": ArtifactRef(
                artifact_id=long_id, kind="draft", content_hash=draft.ref.content_hash
            )
        }
    )
    store.put_artifact(first)
    by = draft.composed_by.model_copy(update={"kind": "human", "id": "demo-seller"})
    events = len(store.list_events())
    revised = record_revision(
        store, previous=first.ref, content=draft.content + "PS\n", by=by, at=draft.composed_at
    )
    assert revised.ref.content_hash == sha256_digest(draft.content + "PS\n")
    assert store.latest_artifact(long_id) == revised
    (event,) = store.list_events()[events:]
    assert len(event.idempotency_key) <= 200


def test_a_revision_stopped_before_its_event_is_finished_by_the_next_step(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = research(store).draft
    assert draft is not None
    draft_id = draft.ref.artifact_id

    def stop(*_: object) -> None:
        raise ConnectionError("the run stopped here")

    with monkeypatch.context() as patch:
        patch.setattr(store, "append_event", stop)
        with pytest.raises(ConnectionError):
            revise_draft(
                store, draft_id=draft_id, author="demo-seller", edit=lambda t: t + "PS\n", out=print
            )
    revised = store.latest_artifact(draft_id)
    assert revised.supersedes == draft.ref.content_hash

    def by_seller() -> list[object]:
        return [e for e in store.list_events(type="draft.composed") if e.actor.id == "demo-seller"]

    assert by_seller() == []
    shown: list[str] = []
    review(store, draft_id, "n", "")
    revise_draft(store, draft_id=draft_id, author="demo-seller", edit=lambda t: t, out=shown.append)
    assert len(by_seller()) == 1
    assert not any("unfinished" in line for line in shown)


def _stopped_revision(store: Store, monkeypatch: pytest.MonkeyPatch) -> str:
    draft = research(store).draft
    assert draft is not None

    def stop(*_: object) -> None:
        raise ConnectionError("the run stopped here")

    with monkeypatch.context() as patch:
        patch.setattr(store, "append_event", stop)
        with pytest.raises(ConnectionError):
            revise_draft(
                store,
                draft_id=draft.ref.artifact_id,
                author="demo-seller",
                edit=lambda t: t + "PS\n",
                out=print,
            )
    return draft.ref.artifact_id


@pytest.mark.parametrize("field", ["actor", "attributes"])
def test_finishing_a_revision_refuses_an_event_that_tells_it_differently(
    field: str, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id = _stopped_revision(store, monkeypatch)
    canonical = _revision_event(store, store.latest_artifact(draft_id))
    different = {
        "actor": canonical.actor.model_copy(update={"id": "someone-else"}),
        "attributes": {"supersedes": "sha256:" + "0" * 64},
    }[field]
    store.append_event(canonical.model_copy(update={field: different}))
    with pytest.raises(RecordConflict, match="different revision"):
        complete_revision(store, draft_id)


def test_only_a_persons_edit_is_finished_this_way(store: Store) -> None:
    draft = research(store).draft
    assert draft is not None
    content = draft.content + "PS\n"
    composed = draft.model_copy(
        update={
            "ref": draft.ref.model_copy(update={"content_hash": sha256_digest(content)}),
            "content": content,
            "supersedes": draft.ref.content_hash,
        }
    )
    store.put_artifact(type(draft).model_validate(composed.model_dump()))
    events = len(store.list_events())
    assert not complete_revision(store, draft.ref.artifact_id)
    assert len(store.list_events()) == events


def test_the_recipient_is_recorded_once_as_a_person(store: Store) -> None:
    research(store)
    research(store)
    person = store.get_person("per-jordan-avery")
    assert (person.account_id, person.email) == ("acc-northstar", "jordan.avery@northstar.example")
    assert len(store.list_events(type="person.recorded")) == 1


def test_a_changed_address_needs_a_new_person_id(store: Store) -> None:
    research(store)
    inputs = load_inputs(NORTHSTAR)
    moved = inputs.account.model_copy(
        update={"recipient": inputs.account.recipient.model_copy(update={"email": "j@new.example"})}
    )
    strategy = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit=inputs.strategy.commit, scope=store.scope
    )
    drafts = len(store.list_events(type="draft.composed"))
    with pytest.raises(ValueError, match="new person_id"):
        research_prospect(
            store,
            strategy=strategy,
            prospect=moved,
            acquisition=FixtureAcquisition(inputs.responses),
            operator="demo-operator",
        )
    assert store.get_person("per-jordan-avery").email == "jordan.avery@northstar.example"
    assert len(store.list_events(type="draft.composed")) == drafts
