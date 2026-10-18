"""Real sends through a platform adapter, recorded as ADR 0022 says, using a fake sender."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from auggie.acquisition.fixture import FixtureAcquisition
from auggie.contracts import Actor, Artifact, CustomerScope
from auggie.contracts.approval import DeliveryPlatform
from auggie.delivery import DeliveryBlocked, SendRefused, SendUnknown, split_message
from auggie.external import ExternalCallRefused
from auggie.operate import (
    deliver_draft,
    report_outcome,
    research_prospect,
    resolve_delivery,
    review_draft,
    revise_draft,
)
from auggie.pipeline import resolve_send
from auggie.store import Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from auggie.synthetic import load_inputs
from tests.conftest import NORTHSTAR, variant


@dataclass
class FakeSender:
    """Stands in for the Gmail adapter: records what it was asked to send."""

    outcomes: list[str | Exception] = field(default_factory=lambda: ["msg-001"])
    sent: list[tuple[str, str, str]] = field(default_factory=list)
    platform: DeliveryPlatform = "gmail"
    scope: CustomerScope = field(default_factory=lambda: live_scope())
    actor: Actor = field(
        default_factory=lambda: Actor(kind="service", id="fake-gmail", version="0")
    )

    def send(self, *, to: str, subject: str, body: str) -> str:
        self.sent.append((to, subject, body))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def live_scope() -> CustomerScope:
    scope = load_inputs(NORTHSTAR).scope
    execution = {"delivery": "live", "platform": "gmail", "external_providers": True}
    return CustomerScope.model_validate(variant(scope, synthetic=False, execution=execution))


def clock() -> Callable[[], datetime]:
    moments = iter(
        datetime(2026, 10, 14, 16, 0, tzinfo=UTC) + timedelta(minutes=n) for n in range(99)
    )
    return lambda: next(moments)


@pytest.fixture
def store() -> Store:
    return InMemoryBackend().open(live_scope())


def researched(store: Store, *, email: str | None = "jordan.avery@northstar.example") -> Artifact:
    inputs = load_inputs(NORTHSTAR)
    recipient = inputs.account.recipient.model_copy(update={"email": email})
    prospect = inputs.account.model_copy(update={"recipient": recipient})
    strategy = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit=inputs.strategy.commit, scope=store.scope
    )
    result = research_prospect(
        store,
        strategy=strategy,
        prospect=prospect,
        acquisition=FixtureAcquisition(inputs.responses),
        operator="demo-seller",
        now=clock(),
    )
    assert result.draft is not None
    return result.draft


def approve(store: Store, draft: Artifact, *, delivery: bool = True) -> list[str]:
    asked: list[str] = []
    replies = ["y", "", "y" if delivery else "n"]

    def ask(question: str) -> str:
        asked.append(question)
        return replies.pop(0)

    review_draft(
        store,
        draft_id=draft.ref.artifact_id,
        reviewer="demo-seller",
        ask=ask,
        out=lambda _: None,
        now=lambda: datetime(2026, 10, 14, 17, 0, tzinfo=UTC),
    )
    return asked


def sent_at() -> datetime:
    return datetime(2026, 10, 14, 17, 5, tzinfo=UTC)


def kinds(store: Store) -> list[str]:
    return [e.type for e in store.list_events() if e.type.startswith("delivery.")]


def test_the_approved_text_goes_to_the_recorded_address_once(store: Store) -> None:
    draft = researched(store)
    asked = approve(store, draft)
    assert "Jordan <jordan.avery@northstar.example> from your gmail mailbox" in asked[-1]
    sender = FakeSender()
    sent = deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    subject, body = split_message(draft.content)
    assert sender.sent == [("jordan.avery@northstar.example", subject, body)]
    assert sent.created and sent.event.attributes["receipt"] == "msg-001"
    assert kinds(store) == ["delivery.attempted", "delivery.sent"]
    again = deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    assert not again.created and len(sender.sent) == 1
    report_outcome(store, draft_id=draft.ref.artifact_id, outcome="no_reply", reporter="s")


def test_a_refused_send_is_recorded_and_may_be_tried_again(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    sender = FakeSender(outcomes=[SendRefused("Google refused to renew the token"), "msg-002"])
    with pytest.raises(DeliveryBlocked, match="nothing was sent: Google refused"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    assert deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at).created
    assert kinds(store) == [
        "delivery.attempted",
        "delivery.failed",
        "delivery.attempted",
        "delivery.sent",
    ]


def test_an_unknown_send_blocks_every_later_attempt(store: Store) -> None:
    draft = researched(store)
    draft_id = draft.ref.artifact_id
    approve(store, draft)
    sender = FakeSender(outcomes=[TimeoutError("no reply from Gmail"), "never"])
    with pytest.raises(TimeoutError):
        deliver_draft(store, draft_id=draft_id, sender=sender, now=sent_at)
    assert kinds(store) == ["delivery.attempted"]
    with pytest.raises(DeliveryBlocked, match="unknown: check your Sent folder"):
        deliver_draft(store, draft_id=draft_id, sender=sender, now=sent_at)
    with pytest.raises(ValueError, match="unresolved"):
        revise_draft(store, draft_id=draft_id, author="s", edit=lambda t: t + "PS\n", out=print)
    with pytest.raises(ValueError, match="never delivered"):
        report_outcome(store, draft_id=draft_id, outcome="no_reply", reporter="s")
    assert len(sender.sent) == 1


@pytest.mark.parametrize("found", ["sent", "not-sent"])
def test_resolving_an_unknown_send_never_makes_it_retryable(
    found: Literal["sent", "not-sent"], store: Store
) -> None:
    draft = researched(store)
    draft_id = draft.ref.artifact_id
    approve(store, draft)
    sender = FakeSender(outcomes=[TimeoutError("no reply from Gmail"), "never"])
    with pytest.raises(TimeoutError):
        deliver_draft(store, draft_id=draft_id, sender=sender, now=sent_at)
    resolved = resolve_delivery(
        store, draft_id=draft_id, found=found, reporter="demo-seller", now=sent_at
    )
    assert resolved.event.actor.kind == "human" and resolved.event.attributes["resolved"] is True
    if found == "sent":
        assert not deliver_draft(store, draft_id=draft_id, sender=sender, now=sent_at).created
        report_outcome(store, draft_id=draft_id, outcome="no_reply", reporter="s")
    else:
        with pytest.raises(DeliveryBlocked, match="never retries it"):
            deliver_draft(store, draft_id=draft_id, sender=sender, now=sent_at)
    assert len(sender.sent) == 1
    with pytest.raises(ValueError, match="no unknown send"):
        resolve_delivery(store, draft_id=draft_id, found="sent", reporter="s", now=sent_at)


def test_without_an_address_delivery_cannot_be_approved_or_sent(store: Store) -> None:
    draft = researched(store, email=None)
    asked = approve(store, draft)
    assert len(asked) == 2  # content, its note, and no delivery question
    with pytest.raises(DeliveryBlocked, match="no delivery approval"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=FakeSender(), now=sent_at)
    assert kinds(store) == []


def test_a_sender_for_another_platform_is_refused(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    with pytest.raises(DeliveryBlocked, match="no gmail sender"):
        deliver_draft(
            store,
            draft_id=draft.ref.artifact_id,
            sender=FakeSender(platform="instantly"),
            now=sent_at,
        )
    with pytest.raises(DeliveryBlocked, match="no gmail sender"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, now=sent_at)
    assert kinds(store) == []


@pytest.mark.parametrize(
    "content",
    ["Hi Jordan,\n\nThanks", "Subject: Hello\nHi Jordan", "Subject: \n\nHi", "Subject: Hi\n\n  \n"],
    ids=["no-subject", "no-blank-line", "empty-subject", "empty-body"],
)
def test_a_draft_that_is_not_one_subject_and_body_is_not_sent(content: str) -> None:
    with pytest.raises(DeliveryBlocked):
        split_message(content)


def test_a_subject_and_body_are_split_exactly() -> None:
    assert split_message("Subject: One platform\n\nHi Jordan,\n\nThanks\n") == (
        "One platform",
        "Hi Jordan,\n\nThanks\n",
    )


def scope_with(**execution: object) -> CustomerScope:
    base = {"delivery": "live", "platform": "gmail", "external_providers": True}
    base.update(execution)
    data = variant(load_inputs(NORTHSTAR).scope, synthetic=False, execution=base)
    return CustomerScope.model_validate(data)


def test_a_live_send_needs_external_calls_allowed() -> None:
    store = InMemoryBackend().open(scope_with(external_providers=False))
    draft = researched(store)
    approve(store, draft)
    sender = FakeSender(scope=store.scope)
    with pytest.raises(ExternalCallRefused):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    assert sender.sent == [] and kinds(store) == []


def test_a_live_scope_without_a_platform_cannot_deliver() -> None:
    store = InMemoryBackend().open(scope_with(platform="simulated"))
    draft = researched(store)
    with pytest.raises(DeliveryBlocked, match="names no platform"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=FakeSender(), now=sent_at)


def test_only_a_person_can_resolve_a_send(store: Store) -> None:
    draft = researched(store)
    draft_id = draft.ref.artifact_id
    approve(store, draft)
    sender = FakeSender(outcomes=[TimeoutError("no reply"), "never"])
    with pytest.raises(TimeoutError):
        deliver_draft(store, draft_id=draft_id, sender=sender, now=sent_at)
    service = Actor(kind="service", id="something", version="0")
    with pytest.raises(ValueError, match="only a person"):
        resolve_send(store, artifact_id=draft_id, found="not-sent", by=service, at=sent_at())
    with pytest.raises(DeliveryBlocked, match="unknown"):
        deliver_draft(store, draft_id=draft_id, sender=sender, now=sent_at)
    assert len(sender.sent) == 1


def test_an_unknown_send_is_not_hidden_by_a_simulated_hand_off() -> None:
    backend = InMemoryBackend()
    live = backend.open(scope_with())
    draft = researched(live)
    approve(live, draft)
    with pytest.raises(TimeoutError):
        deliver_draft(
            live,
            draft_id=draft.ref.artifact_id,
            sender=FakeSender([TimeoutError("x")]),
            now=sent_at,
        )
    events = live.list_events()
    simulated = InMemoryBackend().open(
        scope_with(delivery="simulated", platform="simulated", external_providers=False)
    )
    simulated.put_person(live.get_person("per-jordan-avery"))
    for evidence in live.list_evidence():
        simulated.put_evidence(evidence)
    simulated.put_qualification(live.get_qualification(draft.qualification_id))
    simulated.put_artifact(draft)
    for approval in live.list_approvals(draft.ref.artifact_id):
        simulated.put_approval(approval)
    for event in events:
        simulated.append_event(event)
    approve(simulated, draft)
    with pytest.raises(DeliveryBlocked, match="unknown"):
        deliver_draft(simulated, draft_id=draft.ref.artifact_id, now=sent_at)


def test_a_receipt_or_reason_that_is_not_plain_is_never_recorded(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    noisy = SendRefused("bounced for jordan.avery@northstar.example: Hi Jordan,\nthanks")
    sender = FakeSender(outcomes=[noisy, "id with spaces <jordan@northstar.example>"])
    with pytest.raises(DeliveryBlocked, match="nothing was sent: the platform refused"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    with pytest.raises(SendUnknown):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    recorded = json.dumps([e.model_dump(mode="json") for e in store.list_events()])
    assert "jordan.avery@" not in recorded and "Hi Jordan" not in recorded


def test_delivery_keys_are_bounded_and_unambiguous(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    sent = deliver_draft(store, draft_id=draft.ref.artifact_id, sender=FakeSender(), now=sent_at)
    keys = [e.idempotency_key for e in store.list_events() if e.type.startswith("delivery.")]
    assert all(len(key) <= 70 and draft.ref.artifact_id not in key for key in keys)
    assert sent.event.idempotency_key.startswith("delivery.sent:")


def test_a_send_shows_in_the_workbook_as_delivered(store: Store) -> None:
    from auggie.projections import project

    draft = researched(store)
    approve(store, draft)
    deliver_draft(store, draft_id=draft.ref.artifact_id, sender=FakeSender(), now=sent_at)
    header, *rows = project(store)["auggie: drafts"]
    (row,) = rows
    assert row[header.index("delivered_at")] == sent_at().isoformat()


def test_a_sender_for_another_scope_is_refused(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    elsewhere = CustomerScope.model_validate(
        variant(
            store.scope,
            customer_id="other-seller",
            data_namespace="auggie_other_seller",
            credential_namespace="other-seller",
            knowledge_namespace="other-seller-strategy",
        )
    )
    sender = FakeSender(scope=elsewhere)
    with pytest.raises(DeliveryBlocked, match="another scope"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    assert sender.sent == [] and kinds(store) == []


def test_a_receipt_that_is_not_an_id_leaves_the_send_unknown(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    sender = FakeSender(outcomes=["id with spaces <jordan@northstar.example>"])
    with pytest.raises(SendUnknown):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=sent_at)
    assert kinds(store) == ["delivery.attempted"]
