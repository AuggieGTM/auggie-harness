"""Replies read from the seller's Gmail for the person to classify (ADR 0029).

The threads of drafts sent from Gmail are read headers only. Each message in a
thread after the sent one, not sent by the seller, and not yet answered is put to
the person, who decides what it was. Nothing from the mailbox is kept but the
outcome they choose, the message's time and Gmail's opaque ID for it.
"""

from __future__ import annotations

from collections.abc import Mapping
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


def answered(store: Store) -> set[str]:
    """Every reply ID an outcome in this scope already answers, whatever its draft: one reply
    is never counted twice."""
    return {
        str(e.attributes["reply"])
        for e in store.list_events(type="outcome.recorded")
        if isinstance(e.attributes.get("reply"), str)
    }


@dataclass(frozen=True)
class Found:
    """What reading the threads found: replies to put to the person, and notes on what had to
    be left to them, such as a reply that cannot be tied to one draft."""

    replies: list[Reply]
    notes: list[str]


def pending(store: Store, reader: ThreadReader) -> Found:
    """Every message waiting for the person, thread by thread in the order the drafts were sent.

    A message is put to the person only when it came after a draft the seller sent in its
    thread, the seller did not send it, no outcome answers it yet, and it can be tied to one
    draft. With an In-Reply-To, the chain of replies it names must lead, within the thread, to
    that draft's message; without one, only one sent draft may precede it. Anything else is
    left for the person to record by hand.
    """
    sent = sent_from_gmail(store)
    by_receipt = {receipt: (draft, person) for draft, person, receipt in sent}
    known = answered(store)
    seen_threads: set[str] = set()
    replies: list[Reply] = []
    notes: list[str] = []
    for draft_id, _person, receipt in sent:
        thread = reader.thread_of(receipt)
        if not any(m.message_id == receipt for m in thread):
            notes.append(f"{draft_id}: its sent message is no longer in Gmail; nothing read")
            continue
        thread_id = thread[0].thread_id
        if thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        anchors = [m for m in thread if m.message_id in by_receipt]
        by_header = {m.header_id: m for m in thread if m.header_id}
        for message in thread:
            if message.message_id in by_receipt or message.from_seller:
                continue
            if message.message_id in known:
                continue
            before = [a for a in anchors if a.at < message.at]
            if message.in_reply_to:
                answers = _answers(message, by_header, by_receipt)
                chosen = [answers] if answers is not None and answers in before else []
            else:
                chosen = before if len(before) == 1 else []
            if len(chosen) != 1:
                if before:
                    drafts = ", ".join(by_receipt[a.message_id][0] for a in before)
                    why = (
                        "names a message that is not one of your drafts in its thread"
                        if message.in_reply_to
                        else f"could answer any of {drafts}"
                    )
                    notes.append(
                        f"a reply at {message.at.isoformat()} in the thread of {drafts} {why}; "
                        "record it by hand with auggie outcome --draft"
                    )
                continue
            draft, person = by_receipt[chosen[0].message_id]
            first_name = store.get_person(person).first_name
            replies.append(Reply(draft, person, first_name, message))
    return Found(replies, notes)


def _answers(
    message: ThreadMessage,
    by_header: Mapping[str, ThreadMessage],
    by_receipt: Mapping[str, object],
) -> ThreadMessage | None:
    """The sent draft a reply's In-Reply-To chain leads to within its thread, or None when the
    chain leaves the thread, loops, or ends anywhere else."""
    seen: set[str] = set()
    current = message
    while current.in_reply_to and current.in_reply_to not in seen:
        seen.add(current.in_reply_to)
        parent = by_header.get(current.in_reply_to)
        if parent is None:
            return None
        if parent.message_id in by_receipt:
            return parent
        current = parent
    return None


def record_reply(store: Store, reply: Reply, outcome: str, reporter: str) -> Appended:
    """Record the person's answer as the draft's next outcome, with the reply's time and ID."""
    if reply.message.message_id in answered(store):
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
