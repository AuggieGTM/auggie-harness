"""Replies read from the seller's Gmail, headers only, for the person to classify (ADR 0029).
A fake Google answers; no network, no real mailbox."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from auggie import cli, replies
from auggie.cli import main
from auggie.contracts import CustomerScope
from auggie.credentials import CredentialFiles
from auggie.delivery.gmail import (
    MESSAGES_URL,
    READ_SCOPE,
    SEND_SCOPE,
    THREADS_URL,
    TOKEN_URL,
    GmailReader,
    ReadRefused,
)
from auggie.https import HttpRequest, HttpResponse
from auggie.learning import learn_view
from auggie.operate import deliver_draft
from auggie.store import Store
from auggie.store.memory import InMemoryBackend
from tests.conftest import MONGODB_URI, mongo_backend
from tests.test_gmail import ACCESS_TOKEN, CLIENT_SECRET, REFRESH_TOKEN, renewed, reply
from tests.test_live_delivery import FakeSender, approve, live_scope, researched, sent_at

SENT = "18c2f0a1b2c3d4e5"
THREAD = "18c2f0a1b2c3d4e5"
JORDAN = "18c2f0a1b2c3d4f0"
DAEMON = "18c2f0a1b2c3d4f1"
SELLER_FOLLOW_UP = "18c2f0a1b2c3d4f2"


def ms(at: datetime) -> str:
    return str(int(at.timestamp() * 1000))


def message(message_id: str, at: datetime, sender: str, *labels: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": THREAD,
        "labelIds": list(labels),
        "internalDate": ms(at),
        "payload": {"headers": [{"name": "From", "value": sender}]},
    }


def thread() -> dict[str, Any]:
    sent = sent_at()
    return {
        "id": THREAD,
        "messages": [
            message(SENT, sent, "Riley <riley@seller.example>", "SENT"),
            message(
                JORDAN,
                sent + timedelta(hours=3),
                "Jordan <jordan.avery@northstar.example>",
                "INBOX",
            ),
            message(
                DAEMON,
                sent + timedelta(minutes=1),
                "Mail Delivery Subsystem <mailer-daemon@googlemail.example>",
                "INBOX",
            ),
            message(
                SELLER_FOLLOW_UP, sent + timedelta(days=1), "Riley <riley@seller.example>", "SENT"
            ),
        ],
    }


class FakeGmail:
    """Answers the token endpoint and the two headers-only reads, and keeps every request."""

    def __init__(self, token: HttpResponse | None = None, **answers: Any) -> None:
        self.token = token or renewed(READ_SCOPE)
        self.minimal: HttpResponse = answers.get(
            "minimal", reply(200, {"id": SENT, "threadId": THREAD})
        )
        self.thread: HttpResponse = answers.get("thread", reply(200, thread()))
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if request.url == TOKEN_URL:
            return self.token
        if request.url.startswith(MESSAGES_URL):
            return self.minimal
        if request.url.startswith(THREADS_URL):
            return self.thread
        raise AssertionError(f"unexpected request to {request.url}")


def credentials(
    root: Path, scope: CustomerScope, name: str = "gmail-read-token"
) -> CredentialFiles:
    folder = root / scope.credential_namespace
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stored = {
        "client_id": "synthetic-client.apps.googleusercontent.example",
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "scopes": [READ_SCOPE if name == "gmail-read-token" else SEND_SCOPE],
    }
    (folder / name).write_text(json.dumps(stored), encoding="utf-8")
    (folder / name).chmod(0o600)
    return CredentialFiles(root)


def reader(tmp_path: Path, google: FakeGmail, name: str = "gmail-read-token") -> GmailReader:
    scope = live_scope()
    return GmailReader(
        scope=scope, credentials=credentials(tmp_path, scope, name), transport=google
    )


# Reading a thread


def test_a_thread_is_read_headers_only_with_a_token_for_gmail_metadata_only(
    tmp_path: Path,
) -> None:
    google = FakeGmail()
    read = reader(tmp_path, google).thread_of(SENT)
    assert [m.message_id for m in read] == [SENT, DAEMON, JORDAN, SELLER_FOLLOW_UP]
    renewal, minimal, metadata = google.requests
    assert renewal.url == TOKEN_URL
    assert minimal.url == f"{MESSAGES_URL}/{SENT}?format=minimal"
    assert metadata.url == f"{THREADS_URL}/{THREAD}?format=metadata&metadataHeaders=From"
    assert all(r.method == "GET" and r.body == b"" for r in (minimal, metadata))
    assert minimal.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    jordan = read[2]
    assert (jordan.from_seller, jordan.looks_like_bounce) == (False, False)
    assert read[1].looks_like_bounce and read[0].from_seller


def test_reading_needs_its_own_token_granted_exactly_gmail_metadata(tmp_path: Path) -> None:
    for n, token in enumerate((renewed(SEND_SCOPE), renewed(f"{READ_SCOPE} {SEND_SCOPE}"))):
        google = FakeGmail(token=token)
        with pytest.raises(ReadRefused, match=r"not granted exactly gmail\.metadata"):
            reader(tmp_path / str(n), google).thread_of(SENT)
        assert [r.url for r in google.requests] == [TOKEN_URL]
    # The send token is never used to read.
    google = FakeGmail()
    with pytest.raises(ReadRefused, match="gmail-read-token"):
        reader(tmp_path / "send-only", google, name="gmail-token").thread_of(SENT)
    assert google.requests == []


def test_a_message_gmail_no_longer_has_has_no_thread(tmp_path: Path) -> None:
    google = FakeGmail(minimal=reply(404, {"error": {"code": 404}}))
    assert reader(tmp_path, google).thread_of(SENT) == []


@pytest.mark.parametrize(
    ("answers", "problem"),
    [
        ({"minimal": reply(200, {"id": SENT, "threadId": "not hex"})}, "no usable thread"),
        ({"thread": reply(200, {"id": THREAD})}, "without its messages"),
        ({"thread": reply(500, {})}, "HTTP 500"),
        ({"thread": reply(200, {"messages": [{"id": JORDAN, "labelIds": []}]})}, "had no time"),
        (
            {"minimal": reply(200, {"id": ACCESS_TOKEN, "threadId": THREAD})},
            "repeated a credential",
        ),
    ],
    ids=["bad-thread-id", "no-messages", "server-error", "no-time", "echoed-token"],
)
def test_a_reply_unlike_gmails_is_refused(
    answers: dict[str, Any], problem: str, tmp_path: Path
) -> None:
    with pytest.raises(ReadRefused, match=problem):
        reader(tmp_path, FakeGmail(**answers)).thread_of(SENT)


def test_a_header_is_shown_without_control_characters_and_bounded(tmp_path: Path) -> None:
    sneaky = "Jordan\x1b[31m <jordan@northstar.example>‮" + "x" * 300
    answer = thread()
    answer["messages"][1]["payload"]["headers"][0]["value"] = sneaky
    read = reader(tmp_path, FakeGmail(thread=reply(200, answer))).thread_of(SENT)
    shown = next(m for m in read if m.message_id == JORDAN).sender
    assert "\x1b" not in shown and "‮" not in shown and len(shown) == 200


def test_only_message_ids_gmail_could_have_are_read(tmp_path: Path) -> None:
    google = FakeGmail()
    with pytest.raises(ReadRefused, match="not a Gmail message ID"):
        reader(tmp_path, google).thread_of("../threads/x")
    assert google.requests == []


# Putting replies to the person


def delivered(store: Store) -> str:
    draft = researched(store)
    approve(store, draft)
    deliver_draft(
        store, draft_id=draft.ref.artifact_id, sender=FakeSender(outcomes=[SENT]), now=sent_at
    )
    return draft.ref.artifact_id


def test_new_messages_from_others_wait_for_the_person_and_are_kept_as_outcomes(
    tmp_path: Path,
) -> None:
    store = InMemoryBackend().open(live_scope())
    draft_id = delivered(store)
    waiting = replies.pending(store, reader(tmp_path, FakeGmail()))
    assert [(r.draft_id, r.first_name, r.message.message_id) for r in waiting] == [
        (draft_id, "Jordan", DAEMON),
        (draft_id, "Jordan", JORDAN),
    ]
    appended = replies.record_reply(store, waiting[1], "positive_reply", "demo-seller")
    assert appended.event.attributes == {"outcome": "positive_reply", "reply": JORDAN}
    assert appended.event.occurred_at == sent_at() + timedelta(hours=3)
    assert appended.event.actor.id == "demo-seller"
    still = replies.pending(store, reader(tmp_path / "again", FakeGmail()))
    assert [r.message.message_id for r in still] == [DAEMON]
    with pytest.raises(ValueError, match="already answered"):
        replies.record_reply(store, waiting[1], "negative_reply", "demo-seller")
    # Nothing from the mailbox but the outcome, its time and the opaque ID is recorded.
    recorded = json.dumps([e.model_dump(mode="json") for e in store.list_events()])
    assert "jordan.avery@northstar.example>" not in recorded and "Jordan <" not in recorded
    (plays,) = [s["plays"] for s in learn_view(store)["strategies"]]
    assert plays[0]["positive"] == 1


def test_the_command_asks_the_person_about_each_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scope = live_scope()
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(scope.model_dump_json(), encoding="utf-8")
    credentials(tmp_path / "credentials", scope)
    monkeypatch.setattr(cli, "google_transport", lambda: FakeGmail())
    monkeypatch.setattr(cli, "interactive", lambda: True)
    with mongo_backend() as backend:
        delivered(backend.open(scope))
        argv = [
            "outcome",
            str(scope_file),
            "--replies",
            "--reporter",
            "demo-seller",
            "--credentials",
            str(tmp_path / "credentials"),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        answers = iter(["s", "p"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "2 new messages in the threads of drafts you sent from Gmail" in out
        assert "(it looks like a delivery failure)" in out
        assert "skipped   nothing recorded" in out and "recorded  positive_reply" in out
        answers = iter(["b"])
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "1 new messages" in out and "recorded  bounce" in out
        monkeypatch.setattr(cli, "interactive", lambda: False)
        assert main(argv) == 1
        assert "must be answered at a terminal" in capsys.readouterr().out


def test_replies_are_read_only_for_a_scope_that_sends_live_from_gmail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    simulated = live_scope().model_copy(
        update={
            "execution": live_scope().execution.model_copy(
                update={"delivery": "simulated", "platform": "simulated"}
            )
        }
    )
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(simulated.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(cli, "interactive", lambda: True)
    argv = [
        "outcome",
        str(scope_file),
        "--replies",
        "--reporter",
        "demo-seller",
        "--mongodb",
        "mongodb://127.0.0.1:1",
    ]
    assert main(argv) == 1
    assert "only for a scope that sends live from Gmail" in capsys.readouterr().out
