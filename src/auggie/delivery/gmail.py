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
from auggie.https import HttpRequest, HttpResponse, LimitedTransport, Transport

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CREDENTIAL = "gmail-token"
"""Name of the authorized-user file in the scope's credential folder (ADR 0021)."""

TIMEOUT_SECONDS = 30.0
GMAIL = Actor(kind="service", id="gmail-delivery", version="1")


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

    def send(self, *, to: str, subject: str, body: str) -> str:
        """Send one message and return Gmail's message ID.

        Raises ``SendRefused`` when nothing was sent, and ``SendUnknown`` when
        the message may have gone out without Gmail confirming it.
        """
        message = _message(to, subject, body)
        token = self._access_token()
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
        if 400 <= response.status < 500:
            raise SendRefused(f"Gmail refused the message (HTTP {response.status})")
        if response.status != 200:
            raise SendUnknown(
                f"Gmail answered HTTP {response.status}; the message may have been sent"
            )
        message_id = _field(response, "id")
        if message_id is None:
            raise SendUnknown("Gmail accepted the message but returned no message ID")
        return message_id

    def _access_token(self) -> str:
        """Renew an access token for exactly ``gmail.send``. Raises ``SendRefused`` otherwise."""
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
        if response.status != 200:
            reason = _field(response, "error")
            code = f" ({reason})" if reason and re.fullmatch(r"[a-z_]{1,40}", reason) else ""
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
        return token


def _message(to: str, subject: str, body: str) -> str:
    """The message as Gmail's base64url ``raw`` form: these headers and this text, nothing else.

    Lines end in CRLF, as RFC 5322 requires; the text is otherwise the approved body.
    """
    if any(character in subject for character in "\r\n"):
        raise SendRefused("the subject holds a line break")
    message = EmailMessage(policy=SMTP)
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _field(response: HttpResponse, name: str) -> str | None:
    """A top-level string field of a JSON reply, or None. Never echoes the reply."""
    try:
        value = json.loads(response.body).get(name)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None
