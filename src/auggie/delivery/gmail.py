"""Gmail: one approved message sent from the seller's own mailbox (ADRs 0020, 0021, 0022), and
the replies in its thread read back, headers only (ADR 0029).

Each send renews an access token from the scope's ``gmail-token`` file, uses
it only if Google lists exactly the ``gmail.send`` scope for it, and sends
one message through the Gmail API. Reading renews one from ``gmail-read-token``
for exactly ``gmail.metadata`` and never asks for a message's body. The
harness never writes either file.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP

from auggie.contracts import Actor, CustomerScope
from auggie.contracts.approval import DeliveryPlatform
from auggie.credentials import CredentialError, CredentialFiles
from auggie.delivery import SendRefused, SendUnknown
from auggie.external import require_external_calls
from auggie.https import MAX_BODY_BYTES, HttpRequest, HttpResponse, LimitedTransport, Transport

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CREDENTIAL = "gmail-token"
"""Name of the authorized-user file in the scope's credential folder (ADR 0021)."""

READ_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
READ_CREDENTIAL = "gmail-read-token"
"""The second authorized-user file, for reading headers only (ADR 0029)."""
MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
THREADS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/threads"

TIMEOUT_SECONDS = 30.0
GMAIL = Actor(kind="service", id="gmail-delivery", version="1")

MESSAGE_ID = re.compile(r"[0-9a-f]{8,40}")
"""Gmail's message IDs are hexadecimal. A reply holding anything else is not trusted as one."""

OAUTH_ERRORS = frozenset(
    {
        "access_denied",
        "admin_policy_enforced",
        "deleted_client",
        "disabled_client",
        "invalid_client",
        "invalid_grant",
        "invalid_rapt",
        "invalid_request",
        "invalid_scope",
        "org_internal",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)
"""OAuth error codes Google documents. Only these are ever repeated back; others are dropped."""


class GoogleTransport(LimitedTransport):
    """Sends requests only to Google's token and Gmail endpoints, never following redirects."""

    def __init__(self) -> None:
        super().__init__(
            ("https://oauth2.googleapis.com", "https://gmail.googleapis.com"), "Google"
        )


class GmailSender:
    """Sends from one scope's Gmail mailbox. Refused unless the scope allows external calls."""

    platform: DeliveryPlatform = "gmail"
    actor: Actor = GMAIL

    def __init__(
        self, *, scope: CustomerScope, credentials: CredentialFiles, transport: Transport
    ) -> None:
        require_external_calls(scope)
        self._scope = scope
        self._credentials = credentials
        self._transport = transport

    @property
    def scope(self) -> CustomerScope:
        """The scope whose mailbox and credential this sender uses."""
        return self._scope

    def send(self, *, to: str, subject: str, body: str) -> str:
        """Send one message and return Gmail's message ID.

        Raises ``SendRefused`` when nothing was sent, and ``SendUnknown`` when
        the message may have gone out without Gmail confirming it.
        """
        message = _message(to, subject, body)
        token, secrets = self._access_token()
        request = HttpRequest(
            method="POST",
            url=SEND_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            body=json.dumps({"raw": message}).encode(),
            timeout=TIMEOUT_SECONDS,
        )
        try:
            response = self._transport.send(request)
        except OSError:
            raise SendUnknown(
                "the Gmail call did not complete; the message may have been sent"
            ) from None
        if len(response.body) > MAX_BODY_BYTES:
            raise SendUnknown("Gmail's reply was too large to read; the message may have been sent")
        if 400 <= response.status < 500:
            raise SendRefused(f"Gmail refused the message (HTTP {response.status})")
        if response.status != 200:
            raise SendUnknown(
                f"Gmail answered HTTP {response.status}; the message may have been sent"
            )
        message_id = _field(response, "id")
        if message_id is None or not MESSAGE_ID.fullmatch(message_id):
            raise SendUnknown("Gmail accepted the message but returned no message ID")
        if any(message_id in secret or secret in message_id for secret in secrets):
            raise SendUnknown("Gmail's reply was not a message ID; the message may have been sent")
        return message_id

    def _access_token(self) -> tuple[str, tuple[str, ...]]:
        """Renew an access token for exactly ``gmail.send``. Raises ``SendRefused`` otherwise."""
        return renew(
            self._transport, self._credentials, self._scope, CREDENTIAL, SEND_SCOPE, SendRefused
        )


def renew(
    transport: Transport,
    credentials: CredentialFiles,
    scope: CustomerScope,
    name: str,
    wanted: str,
    refuse: type[Exception],
) -> tuple[str, tuple[str, ...]]:
    """Renew an access token from the scope's credential ``name``, for exactly ``wanted``.
    Raises ``refuse`` otherwise, with a reason that never holds a secret.

    Returns the token and every secret value involved, so a reply that
    repeats one is never trusted.
    """
    short = wanted.rsplit("/", 1)[-1]
    try:
        secret = credentials.secret(scope, name)
    except CredentialError as error:
        raise refuse(str(error)) from None
    try:
        stored = json.loads(secret.reveal())
        form = {
            "client_id": stored["client_id"],
            "client_secret": stored["client_secret"],
            "refresh_token": stored["refresh_token"],
            "grant_type": "refresh_token",
        }
        if not all(isinstance(value, str) and value for value in form.values()):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise refuse(f"{name} is not an authorized-user file; sign in again") from None
    request = HttpRequest(
        method="POST",
        url=TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urllib.parse.urlencode(form).encode(),
        timeout=TIMEOUT_SECONDS,
    )
    try:
        response = transport.send(request)
    except OSError:
        raise refuse("Google could not be reached to renew the token") from None
    if len(response.body) > MAX_BODY_BYTES:
        raise refuse("Google's token reply was too large to read")
    if response.status != 200:
        reason = _field(response, "error")
        code = f" ({reason})" if reason in OAUTH_ERRORS else ""
        raise refuse(
            f"Google refused to renew the token{code} (HTTP {response.status}); sign in again"
        )
    token = _field(response, "access_token")
    granted = _field(response, "scope")
    if token is None or (_field(response, "token_type") or "").lower() != "bearer":
        raise refuse("Google's token reply was not a bearer access token")
    if granted is None or set(granted.split()) != {wanted}:
        raise refuse(
            f"the token is not granted exactly {short}; sign in again with only that scope"
        )
    return token, (token, form["client_secret"], form["refresh_token"])


def _message(to: str, subject: str, body: str) -> str:
    """The message as Gmail's base64url ``raw`` form: these headers and this text, nothing else.

    The body travels base64-encoded, its lines ending in CRLF as RFC 5322
    requires and nothing added: decoded, with CRLF read as LF, it is exactly
    the approved body, final newline or not.
    """
    if any(character in subject for character in "\r\n"):
        raise SendRefused("the subject holds a line break")
    message = EmailMessage(policy=SMTP)
    message["To"] = to
    message["Subject"] = subject
    message["MIME-Version"] = "1.0"
    message["Content-Type"] = 'text/plain; charset="utf-8"'
    message["Content-Transfer-Encoding"] = "base64"
    message.set_payload(base64.encodebytes(body.replace("\n", "\r\n").encode()).decode("ascii"))
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _field(response: HttpResponse, name: str) -> str | None:
    """A top-level string field of a JSON reply, or None. Never echoes the reply."""
    try:
        value = json.loads(response.body).get(name)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


class ReadRefused(Exception):
    """The mailbox could not be read as asked. Nothing was recorded."""


@dataclass(frozen=True)
class ThreadMessage:
    """One message of a thread, as headers-only reading shows it."""

    message_id: str
    thread_id: str
    at: datetime
    """When Gmail received it."""
    from_seller: bool
    """It carries Gmail's SENT label: the seller sent it."""
    sender: str
    """Its From header, for a person to read: control characters removed, never stored."""
    header_id: str = ""
    in_reply_to: str = ""
    """Its Message-ID and In-Reply-To headers, held only to tell which message a reply
    answers; never shown or stored."""

    @property
    def looks_like_bounce(self) -> bool:
        """A mail system's delivery-failure notice, by its sender. Only ever a suggestion."""
        lowered = self.sender.lower()
        return "mailer-daemon" in lowered or "postmaster" in lowered


def _shown(value: str) -> str:
    """A header for a person to read: no control or format characters, at most 200 of them."""
    cleaned = "".join(c for c in value if unicodedata.category(c)[0] != "C")
    return cleaned.strip()[:200]


class GmailReader:
    """Reads the threads of messages a scope's mailbox sent, headers only (ADR 0029). Refused
    unless the scope allows external calls."""

    def __init__(
        self, *, scope: CustomerScope, credentials: CredentialFiles, transport: Transport
    ) -> None:
        require_external_calls(scope)
        self._scope = scope
        self._credentials = credentials
        self._transport = transport
        self._token: tuple[str, tuple[str, ...]] | None = None

    def thread_of(self, message_id: str) -> list[ThreadMessage]:
        """Every message in the thread of a message the seller sent, oldest first. Empty when
        Gmail no longer has the message. Every ID in Gmail's replies must be the one asked for.
        """
        if not MESSAGE_ID.fullmatch(message_id):
            raise ReadRefused(f"{message_id!r} is not a Gmail message ID")
        found = self._get(f"{MESSAGES_URL}/{message_id}?format=minimal")
        if found is None:
            return []
        thread_id = found.get("threadId")
        if found.get("id") != message_id:
            raise ReadRefused("Gmail answered about another message than the one asked for")
        if not isinstance(thread_id, str) or not MESSAGE_ID.fullmatch(thread_id):
            raise ReadRefused("Gmail returned no usable thread for the sent message")
        headers = "&".join(f"metadataHeaders={h}" for h in THREAD_HEADERS)
        thread = self._get(f"{THREADS_URL}/{thread_id}?format=metadata&{headers}")
        messages = thread.get("messages") if thread else None
        if thread is None or thread.get("id") != thread_id or not isinstance(messages, list):
            raise ReadRefused("Gmail returned another thread, or one without its messages")
        read = [_thread_message(m, thread_id) for m in messages]
        return sorted(read, key=lambda m: (m.at, m.message_id))

    def _get(self, url: str) -> dict[str, object] | None:
        if self._token is None:
            self._token = renew(
                self._transport,
                self._credentials,
                self._scope,
                READ_CREDENTIAL,
                READ_SCOPE,
                ReadRefused,
            )
        token, secrets = self._token
        request = HttpRequest(
            method="GET",
            url=url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            body=b"",
            timeout=TIMEOUT_SECONDS,
        )
        try:
            response = self._transport.send(request)
        except OSError:
            raise ReadRefused("Gmail could not be reached") from None
        if response.status == 404:
            return None
        if response.status != 200 or len(response.body) > MAX_BODY_BYTES:
            raise ReadRefused(f"Gmail answered HTTP {response.status}")
        if any(secret in response.body.decode("utf-8", "replace") for secret in secrets):
            raise ReadRefused("Gmail's reply repeated a credential; none of it was used")
        try:
            data = json.loads(response.body)
        except ValueError:
            raise ReadRefused("Gmail's reply was not JSON") from None
        if not isinstance(data, dict):
            raise ReadRefused("Gmail's reply was not an object")
        # Escaped in JSON, a credential only shows once the reply is decoded.
        if any(secret in text for text in _strings(data) for secret in secrets):
            raise ReadRefused("Gmail's reply repeated a credential; none of it was used")
        return data


THREAD_HEADERS = ("From", "Message-ID", "In-Reply-To")


def _strings(value: object) -> list[str]:
    """Every string in a decoded JSON value, keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def _header(headers: object, name: str) -> str:
    found = next(
        (
            h.get("value")
            for h in (headers if isinstance(headers, list) else [])
            if isinstance(h, dict) and str(h.get("name", "")).lower() == name.lower()
        ),
        "",
    )
    return found if isinstance(found, str) else ""


def _thread_message(message: object, thread_id: str) -> ThreadMessage:
    if not isinstance(message, dict):
        raise ReadRefused("a message of the thread was not an object")
    if message.get("threadId", thread_id) != thread_id:
        raise ReadRefused("a message of the thread belongs to another thread")
    message_id = message.get("id")
    labels = message.get("labelIds", [])
    received = message.get("internalDate")
    if not isinstance(message_id, str) or not MESSAGE_ID.fullmatch(message_id):
        raise ReadRefused("a message of the thread had no usable ID")
    if not isinstance(received, str) or not received.isdigit():
        raise ReadRefused("a message of the thread had no time")
    if not isinstance(labels, list):
        raise ReadRefused("a message of the thread had unreadable labels")
    payload = message.get("payload")
    headers = payload.get("headers", []) if isinstance(payload, dict) else []
    try:
        at = datetime.fromtimestamp(int(received) / 1000, tz=UTC)
    except (OverflowError, ValueError, OSError):
        raise ReadRefused("a message of the thread had an impossible time") from None
    return ThreadMessage(
        message_id=message_id,
        thread_id=thread_id,
        at=at,
        from_seller="SENT" in labels,
        sender=_shown(_header(headers, "From")),
        header_id=_header(headers, "Message-ID").strip(),
        in_reply_to=_header(headers, "In-Reply-To").strip(),
    )
