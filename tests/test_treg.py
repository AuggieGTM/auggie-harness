"""The Treg adapter, against synthetic replies in Treg's documented shape (ADR 0017)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auggie.acquisition.treg import (
    CEILING_MICRO_USD,
    ExternalCallRefused,
    HttpRequest,
    HttpResponse,
    HttpsTransport,
    TregPageText,
    _NoRedirects,
)
from auggie.contracts import CustomerScope, ScopeViolation, SubjectRef, sha256_digest
from auggie.credentials import CredentialError, CredentialFiles
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
