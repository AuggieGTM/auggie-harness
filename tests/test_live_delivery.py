"""Real sends through a platform adapter, recorded as ADR 0022 says, using a fake sender."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from auggie.acquisition.fixture import FixtureAcquisition
from auggie.contracts import Actor, Artifact, CustomerScope
from auggie.contracts.approval import DeliveryPlatform
from auggie.delivery import DeliveryBlocked, SendRefused, split_message
from auggie.operate import (
    deliver_draft,
    report_outcome,
    research_prospect,
    resolve_delivery,
    review_draft,
    revise_draft,
)
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


AT = lambda: datetime(2026, 10, 14, 17, 5, tzinfo=UTC)  # noqa: E731


def kinds(store: Store) -> list[str]:
    return [e.type for e in store.list_events() if e.type.startswith("delivery.")]


def test_the_approved_text_goes_to_the_recorded_address_once(store: Store) -> None:
    draft = researched(store)
    asked = approve(store, draft)
    assert "Jordan <jordan.avery@northstar.example> from your gmail mailbox" in asked[-1]
    sender = FakeSender()
    sent = deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=AT)
    subject, body = split_message(draft.content)
    assert sender.sent == [("jordan.avery@northstar.example", subject, body)]
    assert sent.created and sent.event.attributes["receipt"] == "msg-001"
    assert kinds(store) == ["delivery.attempted", "delivery.sent"]
    again = deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=AT)
    assert not again.created and len(sender.sent) == 1
    report_outcome(store, draft_id=draft.ref.artifact_id, outcome="no_reply", reporter="s")


def test_a_refused_send_is_recorded_and_may_be_tried_again(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    sender = FakeSender(outcomes=[SendRefused("Google refused to renew the token"), "msg-002"])
    with pytest.raises(DeliveryBlocked, match="nothing was sent: Google refused"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=AT)
    assert deliver_draft(store, draft_id=draft.ref.artifact_id, sender=sender, now=AT).created
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
        deliver_draft(store, draft_id=draft_id, sender=sender, now=AT)
    assert kinds(store) == ["delivery.attempted"]
    with pytest.raises(DeliveryBlocked, match="unknown: check your Sent folder"):
        deliver_draft(store, draft_id=draft_id, sender=sender, now=AT)
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
        deliver_draft(store, draft_id=draft_id, sender=sender, now=AT)
    resolved = resolve_delivery(
        store, draft_id=draft_id, found=found, reporter="demo-seller", now=AT
    )
    assert resolved.event.actor.kind == "human" and resolved.event.attributes["resolved"] is True
    if found == "sent":
        assert not deliver_draft(store, draft_id=draft_id, sender=sender, now=AT).created
        report_outcome(store, draft_id=draft_id, outcome="no_reply", reporter="s")
    else:
        with pytest.raises(DeliveryBlocked, match="never retries it"):
            deliver_draft(store, draft_id=draft_id, sender=sender, now=AT)
    assert len(sender.sent) == 1
    with pytest.raises(ValueError, match="no unknown send"):
        resolve_delivery(store, draft_id=draft_id, found="sent", reporter="s", now=AT)


def test_without_an_address_delivery_cannot_be_approved_or_sent(store: Store) -> None:
    draft = researched(store, email=None)
    asked = approve(store, draft)
    assert len(asked) == 2  # content, its note, and no delivery question
    with pytest.raises(DeliveryBlocked, match="no delivery approval"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, sender=FakeSender(), now=AT)
    assert kinds(store) == []


def test_a_sender_for_another_platform_is_refused(store: Store) -> None:
    draft = researched(store)
    approve(store, draft)
    with pytest.raises(DeliveryBlocked, match="no gmail sender"):
        deliver_draft(
            store, draft_id=draft.ref.artifact_id, sender=FakeSender(platform="instantly"), now=AT
        )
    with pytest.raises(DeliveryBlocked, match="no gmail sender"):
        deliver_draft(store, draft_id=draft.ref.artifact_id, now=AT)
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
