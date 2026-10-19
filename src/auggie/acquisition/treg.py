"""Treg acquisition: the text of one page through ``exa.web.contents.get`` (ADR 0017).

One HTTPS call per attempt, to Treg's fixed origin, with the scope's own agent
token and a per-call cost ceiling reserved against the run's budget first. No
retries and no fallback to another provider. Only a bounded excerpt and the
hash of the returned text become evidence.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import HttpUrl, TypeAdapter

from auggie.acquisition import Acquired
from auggie.contracts import (
    Actor,
    CustomerScope,
    ScopeViolation,
    SourceEvidence,
    SubjectRef,
    sha256_digest,
)
from auggie.contracts.common import RecordId
from auggie.contracts.events import AttributeValue
from auggie.contracts.evidence import RetrievalStatus
from auggie.credentials import CredentialError, CredentialFiles
from auggie.external import require_external_calls
from auggie.https import (
    MAX_BODY_BYTES,
    HttpRequest,
    HttpResponse,
    LimitedTransport,
    Transport,
    echoes,
)
from auggie.spend import RunBudget, SpendRefused

TREG_ORIGIN = "https://treg.to"
ROUTE = "exa.web.contents.get"
TOKEN = "treg-token"
"""Name of the agent token file in the scope's credential folder."""

MAX_CHARACTERS = 4000
"""Text requested per page: the excerpt bound of ``SourceEvidence``."""

CEILING_MICRO_USD = 2000
"""Per-call ceiling. The catalog price for one page of text is 1,000 micro-USD."""

TIMEOUT_SECONDS = 30.0

CHALLENGE_PHRASES = ("just a moment", "attention required", "enable javascript and cookies")
"""Openings of bot-challenge pages, which are not the page's content."""


_URL: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)
_RECORD_ID: TypeAdapter[str] = TypeAdapter(RecordId)


ECHOED = "The reply contained the request's credential; none of it was kept."


class HttpsTransport(LimitedTransport):
    """Sends requests only to Treg, never following redirects."""

    def __init__(self) -> None:
        super().__init__((TREG_ORIGIN,), "Treg")


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
        if budget.limit_micro_usd > scope.execution.max_run_spend_micro_usd:
            raise SpendRefused(
                f"a run budget of {budget.limit_micro_usd} micro-USD is more than the scope "
                f"allows ({scope.execution.max_run_spend_micro_usd})"
            )
        self._scope = scope
        self._credentials = credentials
        self._budget = budget
        self._transport = transport or HttpsTransport()
        self._now = now
        self._new_key = new_key

    @property
    def budget(self) -> RunBudget:
        """The run budget every call is reserved against."""
        return self._budget

    def acquire(
        self, *, evidence_id: str, customer_id: str, subject: SubjectRef, url: HttpUrl
    ) -> Acquired:
        """Make one call, unless the scope, its credentials or the budget forbid it.

        Refusals raise before anything is sent. A call that was sent always
        returns evidence, failed if nothing usable came back.
        """
        if customer_id != self._scope.customer_id:
            raise ScopeViolation(f"this adapter serves {self._scope.customer_id!r}")
        require_external_calls(self._scope)
        page = _URL.validate_python(url)
        _RECORD_ID.validate_python(evidence_id)
        token = self._credentials.secret(self._scope, TOKEN)
        if not re.fullmatch(r"[!-~]+", token.reveal()):
            raise CredentialError(
                f"cannot use credential {self._scope.credential_namespace!r}/{TOKEN!r}: "
                "it holds characters an HTTP header cannot carry"
            )
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
                {"urls": [str(page)], "text": {"maxCharacters": MAX_CHARACTERS}}
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
        echoed = response is not None and echoes(response, token.reveal())
        if response is not None and not echoed:
            for header, name, allowed in (
                ("x-treg-call-id", "treg_call_id", r"[A-Za-z0-9_.:-]{1,200}"),
                ("x-treg-cache", "cache", r"[a-z-]{1,20}"),
            ):
                value = response.headers.get(header, "")
                if re.fullmatch(allowed, value):
                    attributes[name] = value

        status: RetrievalStatus
        details: dict[str, Any]
        if echoed:
            status, details = "failed", {"reason": ECHOED}
        else:
            status, details = _read(response, str(page), observed_at)
        retrieved = status == "retrieved"
        text: str = details.get("text", "")
        evidence = SourceEvidence(
            evidence_id=evidence_id,
            customer_id=customer_id,
            subject=subject,
            source_url=page,
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
    return int(reported) if re.fullmatch(r"[0-9]{1,12}", reported) else None


def _read(
    response: HttpResponse | None, url: str, observed_at: datetime
) -> tuple[RetrievalStatus, dict[str, Any]]:
    """Classify a response as a retrieval outcome. Reasons never quote the response."""
    known: dict[str, Any] = {}

    def failed(
        reason: str, status: RetrievalStatus = "failed"
    ) -> tuple[RetrievalStatus, dict[str, Any]]:
        return status, {**known, "reason": reason}

    if response is None:
        return failed("The Treg call did not complete (timeout or network error).")
    if response.status != 200:
        by_status = {
            402: "Treg refused the call: over its cost ceiling or the balance (402).",
            429: "Rate limited (429).",
            503: "Treg or the provider was unavailable (503).",
        }
        return failed(by_status.get(response.status, f"Treg answered HTTP {response.status}."))
    if len(response.body) > MAX_BODY_BYTES:
        return failed("The response was larger than the adapter accepts.")
    try:
        body = json.loads(response.body)
    except (ValueError, RecursionError):
        return failed("The response was not valid JSON.")
    if not isinstance(body, dict):
        return failed("The response was not a JSON object.")
    request_id = body.get("requestId")
    if isinstance(request_id, str) and request_id:
        known["request_id"] = request_id[:200]

    statuses = body.get("statuses")
    statuses = [] if statuses is None else statuses
    results = body.get("results")
    if not isinstance(statuses, list) or not all(
        isinstance(s, dict) and isinstance(s.get("id"), str) and isinstance(s.get("status"), str)
        for s in statuses
    ):
        return failed("The response's statuses were malformed.")
    if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
        return failed("The response's results were malformed.")

    for entry in statuses:
        if entry.get("id") == url and entry.get("status") != "success":
            error = entry.get("error")
            raw_tag = error.get("tag", "") if isinstance(error, dict) else ""
            tag = re.sub(r"[^A-Z0-9_]", "", str(raw_tag).upper())[:64] or "UNKNOWN"
            outcome: RetrievalStatus = "not_found" if "NOT_FOUND" in tag else "failed"
            return failed(f"The provider could not fetch the page ({tag}).", outcome)

    result = next((r for r in results if url in (r.get("id"), r.get("url"))), None)
    if result is None:
        return failed("The response held no result for the requested page.")
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        return failed("The page returned no text.")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return failed("The page text was not valid Unicode.")
    if text.strip().lower().startswith(CHALLENGE_PHRASES):
        return failed("The page served a bot challenge instead of its content.", "blocked")

    details: dict[str, Any] = {**known, "text": text}
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
