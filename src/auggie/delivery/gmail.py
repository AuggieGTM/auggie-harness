"""Gmail delivery: one approved message from the seller's own mailbox (ADRs 0020, 0021, 0022).

Each send renews an access token from the scope's ``gmail-token`` file, uses
it only if Google lists exactly the ``gmail.send`` scope for it, and sends
one message through the Gmail API. The harness never writes the file.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
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
        """Renew an access token for exactly ``gmail.send``. Raises ``SendRefused`` otherwise.

        Returns the token and every secret value involved, so a reply that
        repeats one is never trusted as a receipt.
        """
        try:
            secret = self._credentials.secret(self._scope, CREDENTIAL)
        except CredentialError as error:
            raise SendRefused(str(error)) from None
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
            raise SendRefused(
                f"{CREDENTIAL} is not an authorized-user file; sign in again"
            ) from None
        request = HttpRequest(
            method="POST",
            url=TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urllib.parse.urlencode(form).encode(),
            timeout=TIMEOUT_SECONDS,
        )
        try:
            response = self._transport.send(request)
        except OSError:
            raise SendRefused("Google could not be reached to renew the token") from None
        if len(response.body) > MAX_BODY_BYTES:
            raise SendRefused("Google's token reply was too large to read")
        if response.status != 200:
            reason = _field(response, "error")
            code = f" ({reason})" if reason in OAUTH_ERRORS else ""
            raise SendRefused(
                f"Google refused to renew the token{code} (HTTP {response.status}); sign in again"
            )
        token = _field(response, "access_token")
        granted = _field(response, "scope")
        if token is None or (_field(response, "token_type") or "").lower() != "bearer":
            raise SendRefused("Google's token reply was not a bearer access token")
        if granted is None or set(granted.split()) != {SEND_SCOPE}:
            raise SendRefused(
                "the token is not granted exactly gmail.send; sign in again with only that scope"
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
