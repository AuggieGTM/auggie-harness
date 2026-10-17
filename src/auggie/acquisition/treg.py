"""Treg acquisition: the text of one page through ``exa.web.contents.get`` (ADR 0017).

One HTTPS call per attempt, to Treg's fixed origin, with the scope's own agent
token and a per-call cost ceiling reserved against the run's budget first. No
retries and no fallback to another provider. Only a bounded excerpt and the
hash of the returned text become evidence.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import HttpUrl

from auggie.acquisition import Acquired
from auggie.contracts import (
    Actor,
    CustomerScope,
    ScopeViolation,
    SourceEvidence,
    SubjectRef,
    sha256_digest,
)
from auggie.contracts.events import AttributeValue
from auggie.contracts.evidence import RetrievalStatus
from auggie.credentials import CredentialFiles
from auggie.spend import RunBudget

TREG_ORIGIN = "https://treg.to"
ROUTE = "exa.web.contents.get"
TOKEN = "treg-token"
"""Name of the agent token file in the scope's credential folder."""

MAX_CHARACTERS = 4000
"""Text requested per page: the excerpt bound of ``SourceEvidence``."""

CEILING_MICRO_USD = 2000
"""Per-call ceiling. The catalog price for one page of text is 1,000 micro-USD."""

TIMEOUT_SECONDS = 30.0
MAX_BODY_BYTES = 2_000_000

CHALLENGE_PHRASES = ("just a moment", "attention required", "enable javascript and cookies")
"""Openings of bot-challenge pages, which are not the page's content."""


class ExternalCallRefused(Exception):
    """The scope does not allow calls to external providers. Nothing was sent."""


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout: float

    def __repr__(self) -> str:
        names = ", ".join(sorted(self.headers))
        return f"HttpRequest({self.method} {self.url}, headers: {names})"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    """Header names in lower case."""
    body: bytes


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        """Send ``request``. Raises ``OSError`` (including timeouts) if no response arrives."""
        ...


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it would carry the token to another address."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> urllib.request.Request | None:
        return None


class HttpsTransport:
    """Sends requests with the standard library, only to Treg, never following redirects."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirects)

    def send(self, request: HttpRequest) -> HttpResponse:
        if not request.url.startswith(TREG_ORIGIN + "/"):
            raise ValueError(f"requests may only go to {TREG_ORIGIN}")
        outgoing = urllib.request.Request(
            request.url, data=request.body, headers=dict(request.headers), method=request.method
        )
        try:
            with self._opener.open(outgoing, timeout=request.timeout) as reply:
                return HttpResponse(
                    reply.status,
                    {k.lower(): v for k, v in reply.headers.items()},
                    reply.read(MAX_BODY_BYTES + 1),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                error.code,
                {k.lower(): v for k, v in error.headers.items()},
                error.read(MAX_BODY_BYTES + 1),
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_key() -> str:
    return uuid.uuid4().hex


class TregPageText:
    """Fetches one page's text per call for one scope."""

    actor = Actor(kind="service", id="treg-exa-contents", version="1")

    def __init__(
        self,
        *,
        scope: CustomerScope,
        credentials: CredentialFiles,
        budget: RunBudget,
        transport: Transport | None = None,
        now: Callable[[], datetime] = _utc_now,
        new_key: Callable[[], str] = _new_key,
    ) -> None:
        self._scope = scope
        self._credentials = credentials
        self._budget = budget
        self._transport = transport or HttpsTransport()
        self._now = now
        self._new_key = new_key

    def acquire(
        self, *, evidence_id: str, customer_id: str, subject: SubjectRef, url: HttpUrl
    ) -> Acquired:
        """Make one call, unless the scope, its credentials or the budget forbid it.

        Refusals raise before anything is sent. A call that was sent always
        returns evidence, failed if nothing usable came back.
        """
        if customer_id != self._scope.customer_id:
            raise ScopeViolation(f"this adapter serves {self._scope.customer_id!r}")
        if not self._scope.execution.external_providers:
            raise ExternalCallRefused(
                f"scope {customer_id!r} does not allow calls to external providers"
            )
        token = self._credentials.secret(self._scope, TOKEN)
        reservation = self._budget.reserve(CEILING_MICRO_USD)
        key = self._new_key()
        request = HttpRequest(
            method="POST",
            url=f"{TREG_ORIGIN}/call/{ROUTE}",
            headers={
                "X-Treg-Token": token.reveal(),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": key,
                "X-Treg-Route-Max-Cost": f"{CEILING_MICRO_USD / 1_000_000:.6f}",
            },
            body=json.dumps(
                {"urls": [str(url)], "text": {"maxCharacters": MAX_CHARACTERS}}
            ).encode(),
            timeout=TIMEOUT_SECONDS,
        )
        try:
            response: HttpResponse | None = self._transport.send(request)
        except OSError:
            response = None
        observed_at = self._now()
        charged = self._budget.settle(reservation, _charge(response))

        attributes: dict[str, AttributeValue] = {
            "route": ROUTE,
            "cost_micro_usd": charged,
            "treg_idempotency_key": key,
        }
        if response is not None:
            for header, name in (("x-treg-call-id", "treg_call_id"), ("x-treg-cache", "cache")):
                if response.headers.get(header):
                    attributes[name] = response.headers[header][:200]

        status, details = _read(response, str(url), observed_at)
        retrieved = status == "retrieved"
        text: str = details.get("text", "")
        evidence = SourceEvidence(
            evidence_id=evidence_id,
            customer_id=customer_id,
            subject=subject,
            source_url=url,
            acquired_via=self.actor,
            status=status,
            observed_at=observed_at,
            provider_request_id=details.get("request_id"),
            published_at=details.get("published_at"),
            excerpt=text.strip()[:MAX_CHARACTERS].strip() if retrieved else None,
            content_hash=sha256_digest(text) if retrieved else None,
            failure_reason=None if retrieved else details["reason"],
        )
        return Acquired(evidence, attributes)


def _charge(response: HttpResponse | None) -> int | None:
    """The settled charge Treg reports, or None if it reports none."""
    if response is None:
        return None
    reported = response.headers.get("x-treg-cost-micro", "")
    return int(reported) if reported.isdigit() else None


def _read(
    response: HttpResponse | None, url: str, observed_at: datetime
) -> tuple[RetrievalStatus, dict[str, Any]]:
    """Classify a response as a retrieval outcome. Reasons never quote the response."""

    def failed(
        reason: str, status: RetrievalStatus = "failed"
    ) -> tuple[RetrievalStatus, dict[str, Any]]:
        return status, {"reason": reason}

    if response is None:
        return failed("The Treg call did not complete (timeout or network error).")
    if response.status != 200:
        known = {
            402: "Treg refused the call: over its cost ceiling or the balance (402).",
            429: "Rate limited (429).",
            503: "Treg or the provider was unavailable (503).",
        }
        return failed(known.get(response.status, f"Treg answered HTTP {response.status}."))
    if len(response.body) > MAX_BODY_BYTES:
        return failed("The response was larger than the adapter accepts.")
    try:
        body = json.loads(response.body)
    except ValueError:
        return failed("The response was not valid JSON.")
    if not isinstance(body, dict):
        return failed("The response was not a JSON object.")

    for entry in body.get("statuses") or []:
        if isinstance(entry, dict) and entry.get("id") == url and entry.get("status") != "success":
            error = entry.get("error")
            raw_tag = error.get("tag", "") if isinstance(error, dict) else ""
            tag = re.sub(r"[^A-Z0-9_]", "", str(raw_tag).upper())[:64] or "UNKNOWN"
            outcome: RetrievalStatus = "not_found" if "NOT_FOUND" in tag else "failed"
            return failed(f"The provider could not fetch the page ({tag}).", outcome)

    result = next(
        (
            r
            for r in body.get("results") or []
            if isinstance(r, dict) and url in (r.get("id"), r.get("url"))
        ),
        None,
    )
    if result is None:
        return failed("The response held no result for the requested page.")
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        return failed("The page returned no text.")
    if text.strip().lower().startswith(CHALLENGE_PHRASES):
        return failed("The page served a bot challenge instead of its content.", "blocked")

    details: dict[str, Any] = {"text": text}
    request_id = body.get("requestId")
    if isinstance(request_id, str) and request_id:
        details["request_id"] = request_id[:200]
    published = _published(result.get("publishedDate"), observed_at)
    if published is not None:
        details["published_at"] = published
    return "retrieved", details


def _published(value: object, observed_at: datetime) -> datetime | None:
    """A credible publication date: parseable, and not later than the observation."""
    if not isinstance(value, str):
        return None
    try:
        published = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return published if published <= observed_at else None
