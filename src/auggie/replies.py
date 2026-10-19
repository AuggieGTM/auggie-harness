"""Replies read from the seller's Gmail for the person to classify (ADR 0029).

The threads of drafts sent from Gmail are read headers only. Each message in a
thread after the sent one, not sent by the seller, and not yet answered is put to
the person, who decides what it was. Nothing from the mailbox is kept but the
outcome they choose, the message's time and Gmail's opaque ID for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from auggie.contracts import Actor, RecordRef
from auggie.contracts.events import OutcomeKind
from auggie.delivery.gmail import ThreadMessage
from auggie.pipeline import OutcomeReport, record_outcome
from auggie.store import Appended, Store


class ThreadReader(Protocol):
    def thread_of(self, message_id: str) -> list[ThreadMessage]: ...


@dataclass(frozen=True)
class Reply:
    """A message in the thread of a sent draft, waiting for the person to say what it was."""

    draft_id: str
    recipient_id: str
    first_name: str
    message: ThreadMessage


def sent_from_gmail(store: Store) -> list[tuple[str, str, str]]:
    """Each draft sent from Gmail with a receipt: its ID, its recipient and the message ID."""
    sent = []
    for event in store.list_events(type="delivery.sent"):
        receipt = event.attributes.get("receipt")
        if event.attributes.get("platform") != "gmail" or not isinstance(receipt, str):
            continue
        draft = next((r.id for r in event.refs if r.kind == "artifact"), None)
        person = next((r.id for r in event.refs if r.kind == "person"), None)
        if draft is not None and person is not None:
            sent.append((draft, person, receipt))
    return sent


def answered(store: Store, draft_id: str) -> set[str]:
    """The reply IDs this draft's recorded outcomes already answer."""
    artifact = RecordRef(kind="artifact", id=draft_id)
    return {
        str(e.attributes["reply"])
        for e in store.list_events(type="outcome.recorded")
        if artifact in e.refs and isinstance(e.attributes.get("reply"), str)
    }


def pending(store: Store, reader: ThreadReader) -> list[Reply]:
    """Every message waiting for the person, draft by draft in the order sent."""
    waiting = []
    for draft_id, recipient_id, receipt in sent_from_gmail(store):
        thread = reader.thread_of(receipt)
        sent_at = next((m.at for m in thread if m.message_id == receipt), None)
        known = answered(store, draft_id)
        first_name = store.get_person(recipient_id).first_name
        for message in thread:
            if (
                message.message_id == receipt
                or message.from_seller
                or (sent_at is not None and message.at <= sent_at)
                or message.message_id in known
            ):
                continue
            waiting.append(Reply(draft_id, recipient_id, first_name, message))
    return waiting


def record_reply(store: Store, reply: Reply, outcome: str, reporter: str) -> Appended:
    """Record the person's answer as the draft's next outcome, with the reply's time and ID."""
    if reply.message.message_id in answered(store, reply.draft_id):
        raise ValueError("this reply is already answered")
    artifact = RecordRef(kind="artifact", id=reply.draft_id)
    earlier = sum(artifact in e.refs for e in store.list_events(type="outcome.recorded"))
    report = OutcomeReport(
        outcome=cast(OutcomeKind, outcome),
        reported_by=Actor(kind="human", id=reporter),
        occurred_at=reply.message.at,
        sequence=earlier + 1,
    )
    return record_outcome(
        store, artifact_id=reply.draft_id, report=report, reply_id=reply.message.message_id
    )
