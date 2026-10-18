"""Careers listings, read whole from listing services' public job-board feeds (ADR 0024).

One parser per service, each a pure function from the service's documented reply
to roles. What each takes from the reply follows the service's own documentation:

- Lever, the postings API (github.com/lever/postings-api): ``GET
  /v0/postings/{site}?mode=json`` with ``skip`` and ``limit``. Each posting's ``id``,
  ``text`` (its name), ``categories`` (team, department, location) and ``hostedUrl``.
  No field is documented as when a posting was created, so roles carry no date.
- Greenhouse, the Job Board API (developers.greenhouse.io/job-board.html): ``GET
  /v1/boards/{board_token}/jobs``, unpaginated. Each job's ``id`` (the job post),
  ``title``, ``location.name`` and ``absolute_url``, and ``meta.total``. ``updated_at`` is
  not documented as a posting date, so roles carry none.
- Ashby, the public job posting API (developers.ashbyhq.com): ``GET
  /posting-api/job-board/{name}``, unpaginated. Jobs have no documented ID, so a job's
  ``jobUrl`` identifies it. ``publishedAt`` is when a job was *last* published, not when it
  was posted, so roles carry no date. A job whose ``isListed`` is false is not in the
  listing.

Requests go only to these services' API origins, never follow a redirect, cost
nothing and are made only for a scope that allows external calls (ADR 0016).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from auggie.contracts import (
    Actor,
    CareersListing,
    CareersSnapshot,
    CustomerScope,
    OpenRole,
    ScopeViolation,
    roles_digest,
)
from auggie.contracts.careers import ListingService, ReadStatus
from auggie.contracts.common import RecordId
from auggie.external import require_external_calls
from auggie.https import HttpRequest, HttpResponse, LimitedTransport, Transport
from auggie.signals.tally import count_once

ORIGINS: dict[ListingService, str] = {
    "lever": "https://api.lever.co",
    "greenhouse": "https://boards-api.greenhouse.io",
    "ashby": "https://api.ashbyhq.com",
}
ROLE_CAP = 2000
"""The most roles a snapshot keeps. A longer listing is kept as partial: "at least N"."""

MAX_LISTING_BYTES = 32_000_000
"""Lever and Ashby send each role's full description, so a listing near the role cap runs
to tens of megabytes."""

TIMEOUT_SECONDS = 30.0
READER = Actor(kind="service", id="careers-reader", version="1")


class ListingTransport(LimitedTransport):
    """Sends requests only to the listing services' API origins, never following redirects."""

    def __init__(self) -> None:
        super().__init__(tuple(ORIGINS.values()), "listing service", MAX_LISTING_BYTES)


def request_for(listing: CareersListing) -> HttpRequest:
    """The one request that reads a listing whole. Lever is asked for one more role than the
    cap in a single page, so a listing is never stitched from pages read at different times."""
    board = listing.board  # the contract allows only letters, digits, '.', '_' and '-'
    paths: dict[ListingService, str] = {
        "lever": f"/v0/postings/{board}?mode=json&limit={ROLE_CAP + 1}",
        "greenhouse": f"/v1/boards/{board}/jobs",
        "ashby": f"/posting-api/job-board/{board}",
    }
    return HttpRequest(
        method="GET",
        url=ORIGINS[listing.service] + paths[listing.service],
        headers={"Accept": "application/json"},
        body=b"",
        timeout=TIMEOUT_SECONDS,
    )


class Malformed(ValueError):
    """The reply is not what the service documents."""


@dataclass(frozen=True)
class Reply:
    """The roles a reply lists, in its order, and what it says about the rest of the listing."""

    roles: list[OpenRole]
    total: int | None = None
    """How many roles the reply says it holds, where the service states it."""
    more: bool = False
    """Whether the listing may hold roles the reply left out."""


@dataclass(frozen=True)
class Parsed:
    status: ReadStatus
    roles: tuple[OpenRole, ...] = ()
    reason: str | None = None


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _role(role_id: Any, title: Any, url: Any, team: Any = None, location: Any = None) -> OpenRole:
    if isinstance(role_id, bool) or not isinstance(role_id, str | int) or role_id == "":
        raise Malformed("a role has no ID")
    try:
        return OpenRole(
            role_id=str(role_id),
            title=_text(title) or "",
            url=url,
            team=_text(team),
            location=_text(location),
        )
    except ValidationError:
        raise Malformed("a role has no usable title or link") from None


def _objects(items: Any, what: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise Malformed(f"no {what} list")
    if not all(isinstance(item, dict) for item in items):
        raise Malformed(f"a {what[:-1]} is not an object")
    return items


def lever(reply: Any) -> Reply:
    roles = []
    for posting in _objects(reply, "postings"):
        categories = posting.get("categories")
        categories = categories if isinstance(categories, dict) else {}
        roles.append(
            _role(
                posting.get("id"),
                posting.get("text"),
                posting.get("hostedUrl"),
                categories.get("team") or categories.get("department"),
                categories.get("location"),
            )
        )
    # Asked for one more than the cap: a reply that long may not be the whole listing.
    return Reply(roles, more=len(roles) > ROLE_CAP)


def greenhouse(reply: Any) -> Reply:
    if not isinstance(reply, dict):
        raise Malformed("not an object")
    roles = []
    for job in _objects(reply.get("jobs"), "jobs"):
        location = job.get("location")
        roles.append(
            _role(
                job.get("id"),
                job.get("title"),
                job.get("absolute_url"),
                location=location.get("name") if isinstance(location, dict) else None,
            )
        )
    meta = reply.get("meta")
    total = meta.get("total") if isinstance(meta, dict) else None
    if isinstance(total, bool) or not isinstance(total, int):
        raise Malformed("no total")
    return Reply(roles, total=total)


def ashby(reply: Any) -> Reply:
    if not isinstance(reply, dict):
        raise Malformed("not an object")
    roles = []
    for job in _objects(reply.get("jobs"), "jobs"):
        if job.get("isListed") is False:
            continue  # reachable only by a direct link, so not part of the listing
        url = job.get("jobUrl")
        roles.append(
            _role(
                url,
                job.get("title"),
                url,
                job.get("team") or job.get("department"),
                job.get("location"),
            )
        )
    return Reply(roles)


PARSERS: dict[ListingService, Callable[[Any], Reply]] = {
    "lever": lever,
    "greenhouse": greenhouse,
    "ashby": ashby,
}


def parse(service: ListingService, body: bytes) -> Parsed:
    """What a service's reply says the listing holds, judged complete, partial, inconsistent or
    failed as ADR 0024 defines them. Roles are counted once each, by the service's role ID."""
    try:
        reply = PARSERS[service](json.loads(body))
    except Malformed as error:
        return Parsed("failed", reason=f"The listing reply was malformed: {error}.")
    except (ValueError, RecursionError):
        return Parsed("failed", reason="The listing reply was not JSON.")
    roles, consistent = count_once(reply.roles)
    if not consistent or (reply.total is not None and reply.total != len(reply.roles)):
        return Parsed("inconsistent", roles[:ROLE_CAP])
    if reply.more or len(roles) > ROLE_CAP:
        return Parsed("partial", roles[:ROLE_CAP])
    return Parsed("complete", roles)


def snapshot_of(
    *,
    customer_id: str,
    listing: CareersListing,
    account_id: str,
    snapshot_id: str,
    read_at: datetime,
    response: HttpResponse | None,
    actor: Actor,
) -> CareersSnapshot:
    """The snapshot one read attempt makes. ``response`` is None when no reply arrived."""
    if response is None:
        parsed = Parsed("failed", reason="The listing service could not be reached.")
    elif response.status != 200:
        parsed = Parsed("failed", reason=f"The listing service answered HTTP {response.status}.")
    elif len(response.body) > MAX_LISTING_BYTES:
        parsed = Parsed("failed", reason="The listing reply was too large to read.")
    else:
        parsed = parse(listing.service, response.body)
    return CareersSnapshot(
        snapshot_id=snapshot_id,
        customer_id=customer_id,
        account_id=account_id,
        listing=listing,
        read_at=read_at,
        status=parsed.status,
        failure_reason=parsed.reason,
        roles=parsed.roles,
        content_hash=roles_digest(parsed.roles),
        acquired_via=actor,
    )


class ListingReader(Protocol):
    """Reads one account's listing once. Whatever the outcome, the result is a snapshot."""

    @property
    def actor(self) -> Actor: ...

    def read(
        self, *, listing: CareersListing, customer_id: str, account_id: str, snapshot_id: str
    ) -> CareersSnapshot: ...


_RECORD_ID: TypeAdapter[str] = TypeAdapter(RecordId)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CareersReader:
    """Reads accounts' listings for one scope, one read attempt per call."""

    actor = READER

    def __init__(
        self,
        *,
        scope: CustomerScope,
        transport: Transport | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._scope = scope
        self._transport = transport or ListingTransport()
        self._now = now

    def read(
        self, *, listing: CareersListing, customer_id: str, account_id: str, snapshot_id: str
    ) -> CareersSnapshot:
        """Read ``listing`` once and return the snapshot, whatever the outcome.

        Refusals raise before anything is sent. The snapshot is stamped when the reply
        arrives, or when the read fails.
        """
        if customer_id != self._scope.customer_id:
            raise ScopeViolation(f"this reader serves {self._scope.customer_id!r}")
        require_external_calls(self._scope)
        for value, what in ((account_id, "account ID"), (snapshot_id, "snapshot ID")):
            try:
                _RECORD_ID.validate_python(value)
            except ValidationError:
                raise ValueError(f"not a valid {what}: {value!r}") from None
        response: HttpResponse | None
        try:
            response = self._transport.send(request_for(listing))
        except OSError:
            response = None
        return snapshot_of(
            customer_id=customer_id,
            listing=listing,
            account_id=account_id,
            snapshot_id=snapshot_id,
            read_at=self._now(),
            response=response,
            actor=self.actor,
        )
