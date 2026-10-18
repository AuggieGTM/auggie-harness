"""The careers readers, against synthetic replies in each listing service's documented shape
(ADR 0024)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest

from auggie.acquisition.careers import (
    MAX_LISTING_BYTES,
    ROLE_CAP,
    CareersReader,
    ListingTransport,
    parse,
    request_for,
)
from auggie.contracts import (
    CareersListing,
    CareersSnapshot,
    CustomerScope,
    ScopeViolation,
    roles_digest,
)
from auggie.contracts.careers import ListingService
from auggie.external import ExternalCallRefused
from auggie.https import HttpRequest, HttpResponse
from tests.conftest import EXAMPLES

REPLIES = EXAMPLES / "listings"
KESTREL = EXAMPLES / "kestrel-security"
READ_AT = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)

LISTINGS: dict[ListingService, CareersListing] = {
    "lever": CareersListing.model_validate(
        {
            "service": "lever",
            "board": "fernhollow",
            "url": "https://jobs.lever.example/fernhollow",
        }
    ),
    "greenhouse": CareersListing.model_validate(
        {
            "service": "greenhouse",
            "board": "brightquarry",
            "url": "https://boards.greenhouse.example/brightquarry",
        }
    ),
    "ashby": CareersListing.model_validate(
        {
            "service": "ashby",
            "board": "saltmarsh",
            "url": "https://jobs.ashby.example/saltmarsh",
        }
    ),
}
COMPLETE = {
    "lever": "lever-fernhollow.json",
    "greenhouse": "greenhouse-brightquarry.json",
    "ashby": "ashby-saltmarsh.json",
}


def recorded(name: str) -> tuple[int, Any]:
    case = json.loads((REPLIES / name).read_text(encoding="utf-8"))
    return case["status"], case["body"]


def encode(body: Any) -> bytes:
    return body.encode() if isinstance(body, str) else json.dumps(body).encode()


def reply(name: str) -> HttpResponse:
    status, body = recorded(name)
    return HttpResponse(status, {}, encode(body))


class Replay:
    """A transport that gives one answer and keeps the requests it was sent."""

    def __init__(self, answer: HttpResponse | Exception) -> None:
        self.answer = answer
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def scope(external: bool = True) -> CustomerScope:
    data = json.loads((KESTREL / "scope.json").read_text(encoding="utf-8"))
    data["execution"]["external_providers"] = external
    return CustomerScope.model_validate(data)


def read(service: ListingService, transport: Replay, **changes: Any) -> CareersSnapshot:
    reader = CareersReader(scope=scope(), transport=transport, now=lambda: READ_AT)
    return reader.read(
        listing=LISTINGS[service],
        customer_id=changes.get("customer_id", "demo-security"),
        account_id=changes.get("account_id", "acc-prospect"),
        snapshot_id=changes.get("snapshot_id", "snap-prospect-0001"),
    )


def lever_postings(count: int) -> bytes:
    return json.dumps(
        [
            {
                "id": f"posting-{n:05d}",
                "text": f"Role {n}",
                "hostedUrl": f"https://jobs.lever.example/fernhollow/posting-{n:05d}",
            }
            for n in range(count)
        ]
    ).encode()


def greenhouse_jobs(count: int, total: int | None = None) -> bytes:
    jobs = [
        {
            "id": n,
            "title": f"Role {n}",
            "absolute_url": f"https://boards.greenhouse.example/brightquarry/jobs/{n}",
        }
        for n in range(count)
    ]
    return json.dumps({"jobs": jobs, "meta": {"total": count if total is None else total}}).encode()


# What each service's reply becomes


def test_a_lever_reply_becomes_a_complete_listing_without_dates() -> None:
    _, body = recorded(COMPLETE["lever"])
    parsed = parse("lever", encode(body))
    assert parsed.status == "complete" and parsed.reason is None
    first, *_, last = parsed.roles
    assert [r.title for r in parsed.roles] == [
        "Senior Detection Engineer",
        "Detection Engineer",
        "SOC Analyst II",
        "Account Executive, Mid-Market",
    ]
    assert first.role_id == "6a1f0c2e-0000-4000-8000-000000000001"
    assert (first.team, first.location) == ("Security", "Remote (US)")
    assert str(first.url) == body[0]["hostedUrl"]
    # No team: the department stands in. createdAt is not documented, so no role is dated.
    assert last.team == "Sales"
    assert all(r.posted_at is None for r in parsed.roles)


def test_a_greenhouse_reply_becomes_a_complete_listing_without_dates() -> None:
    _, body = recorded(COMPLETE["greenhouse"])
    parsed = parse("greenhouse", encode(body))
    assert parsed.status == "complete"
    assert [r.role_id for r in parsed.roles] == ["4100001", "4100002", "4100003"]
    first = parsed.roles[0]
    assert (first.title, first.team, first.location) == (
        "Threat Detection Engineer",
        None,
        "Denver, CO",
    )
    assert str(first.url) == "https://boards.greenhouse.example/brightquarry/jobs/4100001"
    assert all(r.posted_at is None for r in parsed.roles)  # updated_at is not a posting date


def test_an_ashby_reply_leaves_out_unlisted_jobs_and_is_identified_by_link() -> None:
    _, body = recorded(COMPLETE["ashby"])
    parsed = parse("ashby", encode(body))
    assert parsed.status == "complete"
    assert [r.title for r in parsed.roles] == ["Detection and Response Manager", "IAM Engineer"]
    manager, iam = parsed.roles
    assert manager.role_id == str(manager.url) == body["jobs"][0]["jobUrl"]
    assert (manager.team, manager.location) == ("Detection and Response", "London, UK")
    assert iam.team == "Security"  # no team: the department stands in
    assert all(r.posted_at is None for r in parsed.roles)  # publishedAt is the last publication


def test_a_role_is_counted_once_and_copies_that_disagree_are_inconsistent() -> None:
    _, body = recorded(COMPLETE["lever"])
    repeated = parse("lever", encode([*body, body[0]]))
    assert repeated.status == "complete" and len(repeated.roles) == 4

    retitled = parse("lever", encode([*body, {**body[0], "text": "Detection Engineer III"}]))
    assert retitled.status == "inconsistent"
    assert retitled.roles[0].title == "Senior Detection Engineer"
    assert len(retitled.roles) == 4  # kept as evidence, once each


def test_a_greenhouse_total_the_jobs_do_not_match_is_inconsistent() -> None:
    _, body = recorded("greenhouse-total-mismatch.json")
    parsed = parse("greenhouse", encode(body))
    assert parsed.status == "inconsistent" and len(parsed.roles) == 3


def test_a_listing_longer_than_the_cap_is_partial() -> None:
    lever = parse("lever", lever_postings(ROLE_CAP + 1))
    assert lever.status == "partial" and len(lever.roles) == ROLE_CAP
    # A Lever reply as long as the request allowed may stop short of the listing.
    assert parse("lever", lever_postings(ROLE_CAP)).status == "complete"

    greenhouse = parse("greenhouse", greenhouse_jobs(ROLE_CAP + 5))
    assert greenhouse.status == "partial" and len(greenhouse.roles) == ROLE_CAP
    wrong_total = parse("greenhouse", greenhouse_jobs(ROLE_CAP + 5, total=ROLE_CAP + 6))
    assert wrong_total.status == "inconsistent" and len(wrong_total.roles) == ROLE_CAP


@pytest.mark.parametrize(
    ("service", "body", "reason"),
    [
        ("lever", b"<html>moved</html>", "not JSON"),
        ("lever", b"[" * 100_000 + b"]" * 100_000, "not JSON"),
        ("lever", encode({"postings": []}), "no postings list"),
        ("lever", encode(["a posting"]), "a posting is not an object"),
        ("greenhouse", encode({"jobs": []}), "no total"),
        ("greenhouse", encode({"jobs": [], "meta": {"total": True}}), "no total"),
        ("greenhouse", encode({"meta": {"total": 0}}), "no jobs list"),
        ("ashby", encode([]), "not an object"),
        (
            "greenhouse",
            encode({"jobs": [{"id": True, "title": "x", "absolute_url": "https://a.example/"}]}),
            "a role has no ID",
        ),
        ("ashby", encode({"jobs": [{"title": "x"}]}), "a role has no ID"),
        (
            "lever",
            encode([{"id": "p1", "text": "  ", "hostedUrl": "https://a.example/"}]),
            "a role has no usable title or link",
        ),
        (
            "lever",
            encode([{"id": "p1", "text": "Analyst", "hostedUrl": "ftp://a.example/"}]),
            "a role has no usable title or link",
        ),
    ],
)
def test_a_reply_unlike_the_documented_one_is_a_failed_read(
    service: ListingService, body: bytes, reason: str
) -> None:
    parsed = parse(service, body)
    assert parsed.status == "failed" and parsed.roles == ()
    assert reason in (parsed.reason or "")


def test_an_empty_listing_is_complete_not_failed() -> None:
    assert parse("lever", b"[]").status == "complete"
    assert parse("greenhouse", greenhouse_jobs(0)).status == "complete"
    assert parse("ashby", encode({"jobs": []})).status == "complete"


# The reader


@pytest.mark.parametrize("service", ["lever", "greenhouse", "ashby"])
def test_each_read_becomes_one_snapshot_stamped_when_the_reply_arrived(
    service: ListingService,
) -> None:
    transport = Replay(reply(COMPLETE[service]))
    snapshot = read(service, transport)
    assert snapshot.status == "complete" and snapshot.failure_reason is None
    assert (snapshot.customer_id, snapshot.account_id) == ("demo-security", "acc-prospect")
    assert snapshot.listing == LISTINGS[service]
    assert snapshot.read_at == READ_AT
    assert snapshot.content_hash == roles_digest(snapshot.roles)
    assert snapshot.acquired_via.id == "careers-reader"
    [request] = transport.requests
    assert (request.method, request.body) == ("GET", b"")


def test_requests_name_the_board_at_each_services_documented_endpoint() -> None:
    assert [request_for(LISTINGS[s]).url for s in ("lever", "greenhouse", "ashby")] == [
        f"https://api.lever.co/v0/postings/fernhollow?mode=json&limit={ROLE_CAP + 1}",
        "https://boards-api.greenhouse.io/v1/boards/brightquarry/jobs",
        "https://api.ashbyhq.com/posting-api/job-board/saltmarsh",
    ]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (reply("not-found-404.json"), "answered HTTP 404"),
        (OSError("timed out"), "could not be reached"),
        (HttpResponse(200, {}, b" " * (MAX_LISTING_BYTES + 1)), "too large"),
        (reply("malformed.json"), "not JSON"),
    ],
    ids=["not-found", "unreachable", "oversized", "malformed"],
)
def test_a_read_that_fails_is_kept_with_its_reason_and_no_roles(
    answer: HttpResponse | Exception, reason: str
) -> None:
    snapshot = read("greenhouse", Replay(answer))
    assert snapshot.status == "failed" and snapshot.roles == ()
    assert reason in (snapshot.failure_reason or "")
    assert snapshot.read_at == READ_AT


def test_reads_are_refused_before_anything_is_sent() -> None:
    transport = Replay(reply(COMPLETE["lever"]))
    reader = CareersReader(scope=scope(external=False), transport=transport)
    with pytest.raises(ExternalCallRefused):
        reader.read(
            listing=LISTINGS["lever"],
            customer_id="demo-security",
            account_id="acc-a",
            snapshot_id="snap-a",
        )
    with pytest.raises(ScopeViolation):
        read("lever", transport, customer_id="demo-msp")
    with pytest.raises(ValueError, match="snapshot ID"):
        read("lever", transport, snapshot_id="snap a")
    with pytest.raises(ValueError, match="account ID"):
        read("lever", transport, account_id="")
    assert transport.requests == []


# The real transport, without leaving this machine


def test_the_listing_transport_talks_only_to_the_listing_services() -> None:
    for elsewhere in ("https://api.lever.co.example/v0", "https://jobs.lever.co/fernhollow"):
        with pytest.raises(ValueError, match="only go to"):
            ListingTransport().send(HttpRequest("GET", elsewhere, {}, b"", 1.0))


def test_the_listing_transport_reads_a_reply_up_to_the_listing_limit() -> None:
    asked: list[int] = []

    class Reply:
        status = 200
        headers: MappingProxyType[str, str] = MappingProxyType({})
        length = 0

        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            asked.append(limit)
            return b"[]"

    class Opener:
        def open(self, *args: object, **kwargs: object) -> Reply:
            return Reply()

    transport = ListingTransport()
    transport._opener = Opener()  # type: ignore[assignment]
    url = request_for(LISTINGS["ashby"]).url
    assert transport.send(HttpRequest("GET", url, {}, b"", 1.0)).body == b"[]"
    assert asked == [MAX_LISTING_BYTES + 1]
