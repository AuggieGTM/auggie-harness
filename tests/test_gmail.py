"""The Gmail adapter against a fake Google: no network, no real mailbox (ADRs 0021, 0022)."""

from __future__ import annotations

import base64
import json
import urllib.parse
from email import message_from_bytes
from email.policy import default
from pathlib import Path

import pytest

from auggie.contracts import CustomerScope
from auggie.credentials import CredentialFiles
from auggie.delivery import SendRefused, SendUnknown
from auggie.delivery.gmail import SEND_SCOPE, SEND_URL, TOKEN_URL, GmailSender, GoogleTransport
from auggie.external import ExternalCallRefused
from auggie.https import HttpRequest, HttpResponse
from tests.conftest import Scenario, variant

CLIENT_SECRET = "synthetic-client-secret-0000"
REFRESH_TOKEN = "synthetic-refresh-token-0000"
ACCESS_TOKEN = "synthetic-access-token-0000"
SECRETS = (CLIENT_SECRET, REFRESH_TOKEN, ACCESS_TOKEN)


def reply(status: int, body: object) -> HttpResponse:
    return HttpResponse(status, {"content-type": "application/json"}, json.dumps(body).encode())


def renewed(scope: str | None = SEND_SCOPE, **changes: object) -> HttpResponse:
    body = {
        "access_token": ACCESS_TOKEN,
        "expires_in": 3599,
        "scope": scope,
        "token_type": "Bearer",
    }
    body.update(changes)
    return reply(200, {k: v for k, v in body.items() if v is not None})


class FakeGoogle:
    """Answers the token endpoint, then the send endpoint, as scripted."""

    def __init__(
        self,
        token: HttpResponse | Exception | None = None,
        send: HttpResponse | Exception | None = None,
    ) -> None:
        self.token = token or renewed()
        self.answer = send or reply(200, {"id": "18c2f0a1b2c3d4e5", "threadId": "t-1"})
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        answer = self.token if request.url == TOKEN_URL else self.answer
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def scope(scenario: Scenario) -> CustomerScope:
    execution = {"delivery": "live", "platform": "gmail", "external_providers": True}
    return CustomerScope.model_validate(
        variant(scenario.scope, synthetic=False, execution=execution)
    )


def credentials(
    tmp_path: Path, scope: CustomerScope, content: str | None = None
) -> CredentialFiles:
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    token = folder / "gmail-token"
    stored = {
        "client_id": "synthetic-client.apps.googleusercontent.example",
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "token": "an-old-access-token-that-is-never-used",
        "scopes": [SEND_SCOPE],
    }
    token.write_text(json.dumps(stored) if content is None else content, encoding="utf-8")
    token.chmod(0o600)
    return CredentialFiles(tmp_path)


def sender(tmp_path: Path, scope: CustomerScope, google: FakeGoogle) -> GmailSender:
    return GmailSender(scope=scope, credentials=credentials(tmp_path, scope), transport=google)


def send(gmail: GmailSender) -> str:
    return gmail.send(
        to="jordan.avery@northstar.example",
        subject="One device-management platform",
        body="Hi Jordan,\n\nWould that be useful?\n",
    )


def test_one_message_goes_out_with_a_token_renewed_for_gmail_send_only(
    tmp_path: Path, scope: CustomerScope
) -> None:
    google = FakeGoogle()
    assert send(sender(tmp_path, scope, google)) == "18c2f0a1b2c3d4e5"
    renew, mail = google.requests
    assert (renew.method, renew.url) == ("POST", TOKEN_URL)
    form = urllib.parse.parse_qs(renew.body.decode())
    assert form["refresh_token"] == [REFRESH_TOKEN] and form["grant_type"] == ["refresh_token"]
    assert "scope" not in form
    assert REFRESH_TOKEN not in renew.url
    assert (mail.method, mail.url) == ("POST", SEND_URL)
    assert mail.headers["Authorization"] == f"Bearer {ACCESS_TOKEN}"
    raw = base64.urlsafe_b64decode(json.loads(mail.body)["raw"])
    sent = message_from_bytes(raw, policy=default)
    assert sent["To"] == "jordan.avery@northstar.example"
    assert sent["Subject"] == "One device-management platform"
    # RFC 5322 lines end in CRLF on the wire; the text is otherwise exactly the approved body.
    assert sent.get_content().replace("\r\n", "\n") == "Hi Jordan,\n\nWould that be useful?\n"
    assert sorted(sent.keys()) == [
        "Content-Transfer-Encoding",
        "Content-Type",
        "MIME-Version",
        "Subject",
        "To",
    ]


@pytest.mark.parametrize(
    "token",
    [
        renewed(scope=f"{SEND_SCOPE} https://www.googleapis.com/auth/gmail.readonly"),
        renewed(scope="https://www.googleapis.com/auth/gmail.compose"),
        renewed(scope=None),
        renewed(token_type="mac"),
        renewed(access_token=None),
        reply(400, {"error": "invalid_grant", "error_description": "Token has been expired."}),
        reply(200, ["not", "an", "object"]),
        ConnectionError("no route to Google"),
    ],
    ids=["broader", "other", "no-scope", "not-bearer", "no-token", "refused", "garbled", "offline"],
)
def test_nothing_is_sent_unless_google_grants_exactly_gmail_send(
    token: HttpResponse | Exception, tmp_path: Path, scope: CustomerScope
) -> None:
    google = FakeGoogle(token=token)
    with pytest.raises(SendRefused) as refused:
        send(sender(tmp_path, scope, google))
    assert [r.url for r in google.requests] == [TOKEN_URL]
    assert not any(secret in str(refused.value) for secret in SECRETS)


def test_a_refused_renewal_names_googles_error_code(tmp_path: Path, scope: CustomerScope) -> None:
    google = FakeGoogle(token=reply(400, {"error": "invalid_grant"}))
    with pytest.raises(SendRefused, match=r"\(invalid_grant\) \(HTTP 400\); sign in again"):
        send(sender(tmp_path, scope, google))


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (reply(400, {"error": {"code": 400}}), SendRefused),
        (reply(403, {"error": {"code": 403}}), SendRefused),
        (reply(500, {"error": {"code": 500}}), SendUnknown),
        (reply(200, {"threadId": "t-1"}), SendUnknown),
        (TimeoutError("the read timed out"), SendUnknown),
    ],
    ids=["400", "403", "500", "no-id", "timeout"],
)
def test_gmails_answer_decides_refused_or_unknown(
    answer: HttpResponse | Exception, outcome: type[Exception], tmp_path: Path, scope: CustomerScope
) -> None:
    google = FakeGoogle(send=answer)
    with pytest.raises(outcome) as raised:
        send(sender(tmp_path, scope, google))
    assert not any(secret in str(raised.value) for secret in SECRETS)


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"client_id": "x", "client_secret": "y"}), json.dumps(["x"])],
    ids=["not-json", "no-refresh-token", "not-an-object"],
)
def test_a_credential_that_is_not_an_authorized_user_file_is_refused(
    content: str, tmp_path: Path, scope: CustomerScope
) -> None:
    google = FakeGoogle()
    gmail = GmailSender(
        scope=scope, credentials=credentials(tmp_path, scope, content), transport=google
    )
    with pytest.raises(SendRefused, match="not an authorized-user file"):
        send(gmail)
    assert google.requests == []


def test_a_missing_credential_is_refused_before_any_call(
    tmp_path: Path, scope: CustomerScope
) -> None:
    google = FakeGoogle()
    gmail = GmailSender(scope=scope, credentials=CredentialFiles(tmp_path), transport=google)
    with pytest.raises(SendRefused, match="gmail-token"):
        send(gmail)
    assert google.requests == []


def test_a_subject_with_a_line_break_is_never_sent(tmp_path: Path, scope: CustomerScope) -> None:
    google = FakeGoogle()
    with pytest.raises(SendRefused, match="line break"):
        sender(tmp_path, scope, google).send(
            to="jordan.avery@northstar.example", subject="Hi\r\nBcc: x@else.example", body="Hi"
        )
    assert google.requests == []


def test_a_scope_without_external_calls_cannot_build_a_sender(
    tmp_path: Path, scenario: Scenario
) -> None:
    with pytest.raises(ExternalCallRefused):
        GmailSender(
            scope=scenario.scope, credentials=CredentialFiles(tmp_path), transport=FakeGoogle()
        )


def test_the_transport_reaches_only_google() -> None:
    request = HttpRequest(
        method="POST", url="https://example.com/token", headers={}, body=b"", timeout=1
    )
    with pytest.raises(ValueError, match="may only go to"):
        GoogleTransport().send(request)


@pytest.mark.parametrize(
    "answer",
    [
        reply(200, {"id": ACCESS_TOKEN}),
        reply(200, {"id": "abcdef0123456789", "threadId": "t"}),
        reply(200, {"id": "Hi Jordan, thanks"}),
    ],
    ids=["reflected-token", "hex-but-fine", "message-text"],
)
def test_only_a_plain_gmail_id_is_taken_as_a_receipt(
    answer: HttpResponse, tmp_path: Path, scope: CustomerScope
) -> None:
    google = FakeGoogle(send=answer)
    gmail = sender(tmp_path, scope, google)
    if json.loads(answer.body)["id"] == "abcdef0123456789":
        assert send(gmail) == "abcdef0123456789"
        return
    with pytest.raises(SendUnknown) as raised:
        send(gmail)
    assert ACCESS_TOKEN not in str(raised.value) and "Jordan" not in str(raised.value)


def test_a_reflected_secret_is_never_taken_as_a_receipt(
    tmp_path: Path, scope: CustomerScope
) -> None:
    minted = "0123456789abcdef" + "0123"  # synthetic: an access token that looks like an ID
    google = FakeGoogle(token=renewed(access_token=minted), send=reply(200, {"id": minted[:16]}))
    with pytest.raises(SendUnknown, match="not a message ID"):
        send(sender(tmp_path, scope, google))


def test_an_unknown_oauth_error_is_never_repeated(tmp_path: Path, scope: CustomerScope) -> None:
    google = FakeGoogle(token=reply(400, {"error": REFRESH_TOKEN.replace("-", "_")}))
    with pytest.raises(SendRefused) as refused:
        send(sender(tmp_path, scope, google))
    assert "synthetic_refresh" not in str(refused.value)
    assert "Google refused to renew the token (HTTP 400)" in str(refused.value)


@pytest.mark.parametrize(
    "body",
    ["Hi Jordan", "Hi Jordan,\n\nThanks\n", "Thanks\n\n\n", "Grüße, Jördan — ✓\n"],
    ids=["no-final-newline", "one", "several", "unicode"],
)
def test_the_sent_text_is_exactly_the_approved_body(
    body: str, tmp_path: Path, scope: CustomerScope
) -> None:
    google = FakeGoogle()
    sender(tmp_path, scope, google).send(
        to="jordan.avery@northstar.example", subject="Hi", body=body
    )
    raw = base64.urlsafe_b64decode(json.loads(google.requests[-1].body)["raw"])
    sent = message_from_bytes(raw, policy=default)
    assert sent.get_content().replace("\r\n", "\n") == body


def test_a_reply_cut_short_is_not_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client
    import io

    class Socket:
        def makefile(self, mode: str) -> io.BytesIO:
            return io.BytesIO(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n"
                b'{"id":"18c2f0a1b2c3d4e5"}'
            )

    def opened(*_: object, **__: object) -> http.client.HTTPResponse:
        reply = http.client.HTTPResponse(Socket())  # type: ignore[arg-type]
        reply.begin()
        return reply

    transport = GoogleTransport()
    monkeypatch.setattr(transport._opener, "open", opened)
    request = HttpRequest(method="POST", url=SEND_URL, headers={}, body=b"", timeout=1)
    with pytest.raises(OSError, match="cut short"):
        transport.send(request)
