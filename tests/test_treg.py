"""The Treg adapter, against synthetic replies in Treg's documented shape (ADR 0017)."""

from __future__ import annotations

import http.client
import http.server
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auggie.acquisition.treg import (
    CEILING_MICRO_USD,
    HttpRequest,
    HttpResponse,
    HttpsTransport,
    TregPageText,
    _NoRedirects,
)
from auggie.contracts import CustomerScope, ScopeViolation, SubjectRef, sha256_digest
from auggie.credentials import CredentialError, CredentialFiles
from auggie.external import ExternalCallRefused
from auggie.judgment.rules import RulesQualifier
from auggie.pipeline import research_run
from auggie.spend import RunBudget, SpendRefused
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from auggie.synthetic import load_inputs
from tests.conftest import EXAMPLES, NORTHSTAR, Scenario, variant

REPLIES = EXAMPLES / "treg"
CAREERS = "https://northstar.example/careers/it-operations-manager"
TOKEN_VALUE = "synthetic-treg-token-0000"
OBSERVED = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)


def reply(name: str, **body_changes: Any) -> HttpResponse:
    case = json.loads((REPLIES / name).read_text(encoding="utf-8"))
    body = case["body"]
    if body_changes:
        body = {**body, **body_changes}
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    return HttpResponse(case["status"], case["headers"], raw)


def page(url: str, text: str) -> HttpResponse:
    body = {
        "requestId": "req-synthetic-0100",
        "results": [{"id": url, "url": url, "text": text}],
        "statuses": [{"id": url, "status": "success", "source": "live"}],
    }
    headers = {"x-treg-call-id": "call-synthetic-0100", "x-treg-cost-micro": "1000"}
    return HttpResponse(200, headers, json.dumps(body).encode())


class Replay:
    """A transport that answers from synthetic replies, by requested page, and keeps requests."""

    def __init__(self, answers: HttpResponse | Exception | Mapping[str, HttpResponse]) -> None:
        self.answers = answers
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        answer = self.answers
        if isinstance(answer, Mapping):
            answer = answer[json.loads(request.body)["urls"][0]]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def scope(scenario: Scenario) -> CustomerScope:
    return CustomerScope.model_validate(
        variant(
            scenario.scope,
            execution={
                "delivery": "simulated",
                "external_providers": True,
                "max_run_spend_micro_usd": 10_000,
            },
        )
    )


@pytest.fixture
def credentials(tmp_path: Path, scope: CustomerScope) -> CredentialFiles:
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    token = folder / "treg-token"
    token.write_text(TOKEN_VALUE + "\n", encoding="utf-8")
    token.chmod(0o600)
    return CredentialFiles(tmp_path)


def adapter(
    scope: CustomerScope,
    credentials: CredentialFiles,
    transport: Replay,
    budget: RunBudget | None = None,
) -> TregPageText:
    return TregPageText(
        scope=scope,
        credentials=credentials,
        budget=budget or RunBudget.for_scope(scope),
        transport=transport,
        now=lambda: OBSERVED,
        new_key=lambda: "key-synthetic-0001",
    )


def fetch(treg: TregPageText, url: str = CAREERS) -> Any:
    return treg.acquire(
        evidence_id="ev-northstar-careers",
        customer_id="demo-msp",
        subject=SubjectRef(kind="account", id="acc-northstar"),
        url=url,  # type: ignore[arg-type]
    )


def test_a_page_becomes_retrieved_evidence(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    acquired = fetch(adapter(scope, credentials, Replay(reply("page-text.json"))))
    evidence = acquired.evidence
    text = json.loads((REPLIES / "page-text.json").read_text())["body"]["results"][0]["text"]
    assert evidence.status == "retrieved"
    assert str(evidence.source_url) == CAREERS
    assert evidence.final_url is None
    assert evidence.provider_request_id == "req-synthetic-0001"
    assert evidence.observed_at == OBSERVED
    assert evidence.published_at == datetime(2026, 10, 9, tzinfo=UTC)
    assert evidence.excerpt == text.strip()
    assert evidence.content_hash == sha256_digest(text)
    assert evidence.acquired_via.id == "treg-exa-contents"
    assert dict(acquired.attributes) == {
        "route": "exa.web.contents.get",
        "cost_micro_usd": 1000,
        "treg_idempotency_key": "key-synthetic-0001",
        "treg_call_id": "call-synthetic-0001",
        "cache": "miss",
    }


def test_the_request_is_pinned_and_sends_the_token_only_as_treg_auth(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    replay = Replay(reply("page-text.json"))
    fetch(adapter(scope, credentials, replay))
    [request] = replay.requests
    assert (request.method, request.url) == ("POST", "https://treg.to/call/exa.web.contents.get")
    assert request.headers["X-Treg-Token"] == TOKEN_VALUE
    assert "Authorization" not in request.headers
    assert request.headers["X-Treg-Route-Max-Cost"] == "0.002000"
    assert request.headers["Idempotency-Key"] == "key-synthetic-0001"
    assert json.loads(request.body) == {"urls": [CAREERS], "text": {"maxCharacters": 4000}}


def test_a_printed_request_never_shows_the_token(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    replay = Replay(reply("page-text.json"))
    fetch(adapter(scope, credentials, replay))
    printed = f"{replay.requests[0]!r} {replay.requests[0]}"
    assert TOKEN_VALUE not in printed
    assert "X-Treg-Token" in printed


@pytest.mark.parametrize(
    ("name", "status", "reason", "charged"),
    [
        ("page-not-found.json", "not_found", "CRAWL_NOT_FOUND", 0),
        ("bot-challenge.json", "blocked", "bot challenge", 1000),
        ("empty-text.json", "failed", "no text", 1000),
        ("over-ceiling-402.json", "failed", "(402)", 0),
        ("rate-limited-429.json", "failed", "(429)", CEILING_MICRO_USD),
        ("unavailable-503.json", "failed", "(503)", CEILING_MICRO_USD),
        ("malformed.json", "failed", "not valid JSON", 1000),
    ],
)
def test_an_unusable_reply_is_recorded_with_its_reason_and_no_content(
    name: str,
    status: str,
    reason: str,
    charged: int,
    scope: CustomerScope,
    credentials: CredentialFiles,
) -> None:
    acquired = fetch(adapter(scope, credentials, Replay(reply(name))))
    evidence = acquired.evidence
    assert evidence.status == status
    assert reason in (evidence.failure_reason or "")
    assert evidence.excerpt is None and evidence.content_hash is None
    assert acquired.attributes["cost_micro_usd"] == charged


def test_a_call_that_never_answers_costs_its_ceiling(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    budget = RunBudget.for_scope(scope)
    acquired = fetch(adapter(scope, credentials, Replay(TimeoutError("timed out")), budget))
    assert acquired.evidence.status == "failed"
    assert "did not complete" in (acquired.evidence.failure_reason or "")
    assert budget.spent_micro_usd == CEILING_MICRO_USD
    assert "treg_call_id" not in acquired.attributes


def test_an_oversized_reply_is_not_read(scope: CustomerScope, credentials: CredentialFiles) -> None:
    huge = HttpResponse(200, {"x-treg-cost-micro": "1000"}, b"{" + b" " * 2_000_001)
    acquired = fetch(adapter(scope, credentials, Replay(huge)))
    assert "larger than the adapter accepts" in (acquired.evidence.failure_reason or "")


def test_a_publication_date_after_the_observation_is_dropped(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    body = json.loads((REPLIES / "page-text.json").read_text())["body"]
    body["results"][0]["publishedDate"] = "2030-01-01T00:00:00Z"
    response = HttpResponse(200, {"x-treg-cost-micro": "1000"}, json.dumps(body).encode())
    assert fetch(adapter(scope, credentials, Replay(response))).evidence.published_at is None


def test_calls_are_refused_before_anything_is_sent(
    scope: CustomerScope, credentials: CredentialFiles, tmp_path: Path
) -> None:
    replay = Replay(reply("page-text.json"))
    offline = CustomerScope.model_validate(
        variant(scope, execution={"delivery": "simulated", "max_run_spend_micro_usd": 10_000})
    )
    with pytest.raises(ExternalCallRefused):
        fetch(adapter(offline, credentials, replay))
    unfunded = CustomerScope.model_validate(
        variant(scope, execution={"delivery": "simulated", "external_providers": True})
    )
    with pytest.raises(SpendRefused):
        fetch(adapter(unfunded, credentials, replay))
    budget = RunBudget.for_scope(scope)
    with pytest.raises(CredentialError, match="not found"):
        fetch(adapter(scope, CredentialFiles(tmp_path / "empty"), replay, budget))
    with pytest.raises(ScopeViolation):
        adapter(scope, credentials, replay).acquire(
            evidence_id="ev-x",
            customer_id="demo-other",
            subject=SubjectRef(kind="account", id="acc-northstar"),
            url=CAREERS,  # type: ignore[arg-type]
        )
    assert replay.requests == []
    assert budget.committed_micro_usd == 0


def test_the_run_budget_stops_calls_at_its_limit(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    budget = RunBudget(3000)
    treg = adapter(scope, credentials, Replay(reply("page-text.json")), budget)
    fetch(treg)
    fetch(treg)
    with pytest.raises(SpendRefused, match="past its limit of 3000"):
        fetch(treg)
    assert budget.spent_micro_usd == 2000


def test_the_https_transport_talks_only_to_treg_and_never_follows_redirects() -> None:
    elsewhere = HttpRequest("POST", "https://northstar.example/call", {}, b"", 1.0)
    with pytest.raises(ValueError, match=r"only go to https://treg\.to"):
        HttpsTransport().send(elsewhere)
    assert _NoRedirects().redirect_request() is None


def test_a_research_run_records_what_each_treg_call_cost(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    inputs = load_inputs(NORTHSTAR)
    account = inputs.account
    sources = account.sources
    replay = Replay(
        {
            str(sources["careers"]): reply("page-text.json"),
            str(sources["technology"]): page(
                str(sources["technology"]),
                "We run on Microsoft 365 and plan to consolidate onto a single "
                "device-management platform this year.",
            ),
            str(sources["news"]): reply("unavailable-503.json"),
        }
    )
    budget = RunBudget.for_scope(scope)
    store = InMemoryBackend().open(scope)
    strategy = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit=inputs.strategy.commit, scope=scope
    )
    result = research_run(
        store,
        strategy=strategy,
        account=account,
        acquisition=adapter(scope, credentials, replay, budget),
        qualifier=RulesQualifier(),
        times=inputs.run,
    )
    recorded = store.list_events(type="evidence.recorded")
    assert [e.attributes.get("cost_micro_usd") for e in recorded] == [1000, 1000, 2000]
    assert recorded[2].attributes["status"] == "failed"
    assert budget.spent_micro_usd == 4000
    assert result.decision.outcome == "qualified"
    assert result.draft is not None


# Refusals that must happen before a paid call (review findings 1 to 3)


@pytest.mark.parametrize("token", ["two\nlines", "carriage\rreturn", "spaced token", "tök€n"])
def test_a_token_a_header_cannot_carry_is_refused_without_showing_it(
    token: str, scope: CustomerScope, tmp_path: Path
) -> None:
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    (folder / "treg-token").write_text(token, encoding="utf-8")
    (folder / "treg-token").chmod(0o600)
    replay = Replay(reply("page-text.json"))
    budget = RunBudget.for_scope(scope)
    with pytest.raises(CredentialError) as caught:
        fetch(adapter(scope, CredentialFiles(tmp_path), replay, budget))
    assert token not in str(caught.value)
    assert replay.requests == [] and budget.committed_micro_usd == 0


def test_a_budget_larger_than_the_scope_allows_is_refused(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    unfunded = CustomerScope.model_validate(
        variant(scope, execution={"delivery": "simulated", "external_providers": True})
    )
    replay = Replay(reply("page-text.json"))
    with pytest.raises(SpendRefused, match="more than the scope allows"):
        adapter(unfunded, credentials, replay, RunBudget(10_000))
    smaller = adapter(scope, credentials, replay, RunBudget(2000))
    assert fetch(smaller).evidence.status == "retrieved"


@pytest.mark.parametrize("url", ["not-a-url", "ftp://northstar.example/careers", ""])
def test_an_invalid_url_is_refused_before_anything_is_sent(
    url: str, scope: CustomerScope, credentials: CredentialFiles
) -> None:
    replay = Replay(reply("page-text.json"))
    budget = RunBudget.for_scope(scope)
    with pytest.raises(ValueError):
        fetch(adapter(scope, credentials, replay, budget), url)
    assert replay.requests == [] and budget.committed_micro_usd == 0


def test_the_requested_url_is_normalized_before_it_is_sent(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    replay = Replay(page("https://northstar.example/", "Northstar Works builds things."))
    acquired = fetch(adapter(scope, credentials, replay), "HTTPS://NORTHSTAR.example")
    assert json.loads(replay.requests[0].body)["urls"] == ["https://northstar.example/"]
    assert acquired.evidence.status == "retrieved"


# Replies that must fail closed (review findings 4 to 6)


@pytest.mark.parametrize(
    "body",
    [
        {"requestId": "req-synthetic-0200", "statuses": 1, "results": []},
        {"requestId": "req-synthetic-0200", "results": 1},
        {"requestId": "req-synthetic-0200", "results": ["text"]},
        {
            "requestId": "req-synthetic-0200",
            "statuses": {"id": CAREERS, "status": "error"},
            "results": [{"id": CAREERS, "text": "Error page"}],
        },
    ],
)
def test_a_malformed_reply_is_a_failed_retrieval_not_content(
    body: dict[str, Any], scope: CustomerScope, credentials: CredentialFiles
) -> None:
    response = HttpResponse(200, {"x-treg-cost-micro": "1000"}, json.dumps(body).encode())
    acquired = fetch(adapter(scope, credentials, Replay(response)))
    assert acquired.evidence.status == "failed"
    assert acquired.evidence.excerpt is None
    assert acquired.evidence.provider_request_id == "req-synthetic-0200"
    assert acquired.attributes["cost_micro_usd"] == 1000


@pytest.mark.parametrize(
    "header",
    ["²", "1" * 4400, "-5", "1.5", ""],
    ids=["superscript", "too-long", "negative", "decimal", "empty"],
)
def test_an_unreadable_charge_settles_at_the_ceiling(
    header: str, scope: CustomerScope, credentials: CredentialFiles
) -> None:
    case = reply("page-text.json")
    response = HttpResponse(200, {**case.headers, "x-treg-cost-micro": header}, case.body)
    acquired = fetch(adapter(scope, credentials, Replay(response)))
    assert acquired.attributes["cost_micro_usd"] == CEILING_MICRO_USD


def test_text_that_is_not_valid_unicode_is_a_failed_retrieval(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    raw = json.dumps({"results": [{"id": CAREERS, "text": "broken \ud800 text"}]}).encode()
    response = HttpResponse(200, {"x-treg-cost-micro": "1000"}, raw)
    evidence = fetch(adapter(scope, credentials, Replay(response))).evidence
    assert evidence.status == "failed" and "Unicode" in (evidence.failure_reason or "")


@pytest.mark.parametrize("name", ["page-not-found.json", "bot-challenge.json", "empty-text.json"])
def test_a_failed_result_keeps_the_providers_request_id(
    name: str, scope: CustomerScope, credentials: CredentialFiles
) -> None:
    evidence = fetch(adapter(scope, credentials, Replay(reply(name)))).evidence
    assert evidence.status != "retrieved"
    assert (evidence.provider_request_id or "").startswith("req-synthetic-")


# The real transport, without leaving this machine


class FailingOpener:
    """Stands in for urllib's opener and fails the way http.client can."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def open(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        http.client.IncompleteRead(b"partial"),
        http.client.BadStatusLine("garbage"),
        ValueError(f"Invalid header value b'{TOKEN_VALUE}\\nsecond-line'"),
    ],
    ids=["incomplete-read", "bad-status-line", "invalid-header"],
)
def test_protocol_failures_become_a_failed_retrieval_without_detail(
    error: Exception, scope: CustomerScope, credentials: CredentialFiles
) -> None:
    transport = HttpsTransport()
    transport._opener = FailingOpener(error)  # type: ignore[assignment]
    request = HttpRequest("POST", "https://treg.to/call/x", {"X-Treg-Token": TOKEN_VALUE}, b"", 1.0)
    with pytest.raises(OSError) as caught:
        transport.send(request)
    assert TOKEN_VALUE not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    budget = RunBudget.for_scope(scope)
    treg = TregPageText(scope=scope, credentials=credentials, budget=budget, transport=transport)
    evidence = fetch(treg).evidence
    assert evidence.status == "failed" and "did not complete" in (evidence.failure_reason or "")
    assert budget.spent_micro_usd == CEILING_MICRO_USD


def test_a_redirect_is_never_followed() -> None:
    visited: list[str] = []

    class Redirecting(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            visited.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = urllib.request.build_opener(_NoRedirects)
        with pytest.raises(urllib.error.HTTPError) as caught:
            opener.open(f"http://127.0.0.1:{server.server_port}/start", timeout=5)
        assert caught.value.code == 302
        assert visited == ["/start"]
    finally:
        server.shutdown()
        server.server_close()


# Recheck findings: status rows, nesting, and a reply that echoes the token


@pytest.mark.parametrize(
    "row",
    [
        {"status": "error"},
        {"id": [CAREERS], "status": "error"},
        {"id": CAREERS},
        {"id": CAREERS, "status": 1},
    ],
    ids=["no-id", "list-id", "no-status", "number-status"],
)
def test_a_malformed_status_row_fails_the_retrieval(
    row: dict[str, Any], scope: CustomerScope, credentials: CredentialFiles
) -> None:
    body = {"statuses": [row], "results": [{"id": CAREERS, "text": "Northstar Works page."}]}
    response = HttpResponse(200, {"x-treg-cost-micro": "1000"}, json.dumps(body).encode())
    evidence = fetch(adapter(scope, credentials, Replay(response))).evidence
    assert evidence.status == "failed" and "statuses were malformed" in (
        evidence.failure_reason or ""
    )


def test_deeply_nested_json_is_a_failed_retrieval(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    raw = b'{"extra": ' + b"[" * 20_000 + b"]" * 20_000 + b"}"
    budget = RunBudget.for_scope(scope)
    response = HttpResponse(200, {"x-treg-cost-micro": "1000"}, raw)
    evidence = fetch(adapter(scope, credentials, Replay(response), budget)).evidence
    assert evidence.status == "failed" and "not valid JSON" in (evidence.failure_reason or "")
    assert budget.spent_micro_usd == 1000


def echoing(where: str) -> HttpResponse:
    body = json.loads((REPLIES / "page-text.json").read_text())["body"]
    headers = {"x-treg-cost-micro": "1000", "x-treg-call-id": "call-synthetic-0300"}
    if where == "request-id":
        body["requestId"] = TOKEN_VALUE
    elif where == "text":
        body["results"][0]["text"] += f" Debug: {TOKEN_VALUE}"
    elif where == "escaped-text":
        body["results"][0]["text"] += " Debug: " + TOKEN_VALUE.replace("-", "\\u002d")
    elif where == "error-tag":
        body["statuses"] = [{"id": CAREERS, "status": "error", "error": {"tag": TOKEN_VALUE}}]
    elif where == "header":
        headers["x-treg-cache"] = TOKEN_VALUE
    raw = json.dumps(body).replace("\\\\u002d", "\\u002d").encode()
    return HttpResponse(200, headers, raw)


@pytest.mark.parametrize("where", ["request-id", "text", "escaped-text", "error-tag", "header"])
def test_a_reply_that_echoes_the_token_keeps_nothing_from_it(
    where: str, scope: CustomerScope, credentials: CredentialFiles
) -> None:
    acquired = fetch(adapter(scope, credentials, Replay(echoing(where))))
    evidence = acquired.evidence
    assert evidence.status == "failed"
    assert "contained the request's credential" in (evidence.failure_reason or "")
    assert evidence.excerpt is None and evidence.provider_request_id is None
    assert "treg_call_id" not in acquired.attributes and "cache" not in acquired.attributes
    assert TOKEN_VALUE not in evidence.model_dump_json() + json.dumps(dict(acquired.attributes))
    assert acquired.attributes["cost_micro_usd"] == 1000


def test_only_plain_call_ids_and_cache_states_are_kept(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    case = reply("page-text.json")
    odd = {**case.headers, "x-treg-call-id": "call <script>", "x-treg-cache": "MISS; extra"}
    acquired = fetch(adapter(scope, credentials, Replay(HttpResponse(200, odd, case.body))))
    assert "treg_call_id" not in acquired.attributes and "cache" not in acquired.attributes


@pytest.mark.parametrize(
    "token", ['synthetic"token', "synthetic\\token"], ids=["quote", "backslash"]
)
@pytest.mark.parametrize("where", ["requestId", "text", "key"])
def test_an_echo_of_a_token_json_must_escape_is_still_caught(
    token: str, where: str, scope: CustomerScope, tmp_path: Path
) -> None:
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    (folder / "treg-token").write_text(token, encoding="utf-8")
    (folder / "treg-token").chmod(0o600)
    body: dict[str, Any] = {
        "requestId": "req-synthetic-0400",
        "results": [{"id": CAREERS, "text": "Northstar Works page."}],
    }
    if where == "requestId":
        body["requestId"] = token
    elif where == "text":
        body["results"][0]["text"] += token
    else:
        body["results"][0][token] = "x"
    response = HttpResponse(200, {"x-treg-cost-micro": "1000"}, json.dumps(body).encode())
    acquired = fetch(adapter(scope, CredentialFiles(tmp_path), Replay(response)))
    assert acquired.evidence.status == "failed"
    assert token not in acquired.evidence.model_dump_json()
