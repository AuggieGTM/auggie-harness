"""The OpenRouter writer, against synthetic replies in OpenRouter's documented shape
(ADR 0031). Nothing leaves this machine."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auggie.compose.openrouter import (
    ECHOED,
    MAX_PROMPT_BYTES,
    PROMPT_ALLOWANCE_TOKENS,
    HttpsTransport,
    Message,
    ModelRefused,
    OpenRouterWriter,
    Prices,
)
from auggie.contracts import CustomerScope, ScopeViolation, Strategy, Writer
from auggie.credentials import CredentialError, CredentialFiles
from auggie.external import ExternalCallRefused
from auggie.https import HttpRequest, HttpResponse
from auggie.spend import RunBudget, SpendRefused
from auggie.strategy import load_strategy
from auggie.synthetic import load_inputs
from tests.conftest import NORTHSTAR, Scenario, variant

KEY_VALUE = "sk-or-synthetic-0000"
MODEL = "synthetic-lab/writer-1"
WRITER = Writer(model=MODEL, voice="Plain and short.", max_output_tokens=1000)
MESSAGES = [Message("system", "Write the draft."), Message("user", "Facts: none.")]
PROMPT_BYTES = len(b"Write the draft.") + len(b"Facts: none.")


def endpoint(**pricing: Any) -> dict[str, Any]:
    return {
        "name": "Synthetic Provider | writer-1",
        "provider_name": "Synthetic Provider",
        "pricing": {"prompt": "0.000003", "completion": "0.000015", **pricing},
        "supported_parameters": ["max_tokens", "temperature"],
    }


def listing(*endpoints: dict[str, Any]) -> HttpResponse:
    body = {"data": {"id": MODEL, "name": "Writer 1", "endpoints": list(endpoints)}}
    return HttpResponse(200, {"content-type": "application/json"}, json.dumps(body).encode())


def completion(text: str = "Subject: Hello\n\nHi Dana,", **changes: Any) -> HttpResponse:
    body: dict[str, Any] = {
        "id": "gen-synthetic-0001",
        "object": "chat.completion",
        "created": 1_792_000_000,
        "model": MODEL,
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text, "refusal": None},
            }
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 300, "cost": 0.0072},
    }
    body.update(changes)
    return HttpResponse(200, {"content-type": "application/json"}, json.dumps(body).encode())


class Fake:
    """Answers the price listing and the completion from synthetic replies; keeps requests."""

    def __init__(
        self,
        prices: HttpResponse | Exception | None = None,
        reply: HttpResponse | Exception | None = None,
    ) -> None:
        self.prices = prices if prices is not None else listing(endpoint())
        self.reply = reply if reply is not None else completion()
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        answer = self.prices if request.method == "GET" else self.reply
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def calls(self) -> list[HttpRequest]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture
def scope(scenario: Scenario) -> CustomerScope:
    return CustomerScope.model_validate(
        variant(
            scenario.scope,
            execution={
                "delivery": "simulated",
                "external_providers": True,
                "max_run_spend_micro_usd": 100_000,
            },
        )
    )


@pytest.fixture
def credentials(tmp_path: Path, scope: CustomerScope) -> CredentialFiles:
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    key = folder / "openrouter-key"
    key.write_text(KEY_VALUE + "\n", encoding="utf-8")
    key.chmod(0o600)
    return CredentialFiles(tmp_path)


@pytest.fixture
def make(
    scope: CustomerScope, credentials: CredentialFiles
) -> Callable[..., tuple[OpenRouterWriter, Fake]]:
    def build(
        fake: Fake | None = None, writer: Writer = WRITER, budget: RunBudget | None = None
    ) -> tuple[OpenRouterWriter, Fake]:
        fake = fake or Fake()
        model = OpenRouterWriter(
            scope=scope,
            credentials=credentials,
            budget=budget or RunBudget.for_scope(scope),
            writer=writer,
            transport=fake,
        )
        return model, fake

    return build


def write(model: OpenRouterWriter, customer_id: str = "demo-msp") -> Any:
    return model.write(customer_id=customer_id, messages=MESSAGES)


# The strategy's [writer]


def test_a_play_written_by_a_model_needs_the_writer_table() -> None:
    inputs = load_inputs(NORTHSTAR)
    rules = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit="0" * 40, scope=inputs.scope
    ).strategy
    raw = rules.model_dump(mode="json")
    raw["plays"][0]["writer"] = "model"
    with pytest.raises(ValidationError, match=r"\[writer\] is missing"):
        Strategy.model_validate(raw)
    raw["writer"] = {"model": MODEL, "voice": "Plain."}
    strategy = Strategy.model_validate(raw)
    assert strategy.plays[0].writer == "model" and strategy.writer is not None
    assert strategy.writer.max_words == 150 and strategy.writer.zero_data_retention is False
    assert rules.writer is None and all(play.writer == "template" for play in rules.plays)


@pytest.mark.parametrize(
    "model", ["writer-1", "lab/writer-1:free", "lab/writer?x=1", "lab/../keys", "Lab/writer"]
)
def test_a_model_id_is_author_and_slug_and_nothing_else(model: str) -> None:
    with pytest.raises(ValidationError):
        Writer(model=model, voice="Plain.")


# Refused before anything paid is sent


def test_another_scopes_draft_is_refused_before_anything_is_sent(make: Any) -> None:
    model, fake = make()
    with pytest.raises(ScopeViolation):
        write(model, customer_id="demo-security")
    assert fake.requests == []


def test_a_scope_without_external_calls_sends_nothing(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    closed = scope.model_copy(
        update={"execution": scope.execution.model_copy(update={"external_providers": False})}
    )
    fake = Fake()
    model = OpenRouterWriter(
        scope=closed,
        credentials=credentials,
        budget=RunBudget(0),
        writer=WRITER,
        transport=fake,
    )
    with pytest.raises(ExternalCallRefused):
        write(model)
    with pytest.raises(ExternalCallRefused):
        model.prices()
    assert fake.requests == []


def test_a_missing_key_sends_nothing(scope: CustomerScope, tmp_path: Path) -> None:
    fake = Fake()
    model = OpenRouterWriter(
        scope=scope,
        credentials=CredentialFiles(tmp_path / "none"),
        budget=RunBudget.for_scope(scope),
        writer=WRITER,
        transport=fake,
    )
    with pytest.raises(CredentialError, match="not found") as refused:
        write(model)
    assert KEY_VALUE not in str(refused.value) and fake.requests == []


def test_a_key_a_header_cannot_carry_is_refused(
    make: Any, credentials: CredentialFiles, scope: CustomerScope
) -> None:
    (credentials.root / scope.credential_namespace / "openrouter-key").write_text("sk or\n")
    model, fake = make()
    with pytest.raises(CredentialError, match="HTTP header"):
        write(model)
    assert fake.requests == []


def test_a_prompt_too_long_to_bound_is_refused(make: Any) -> None:
    model, fake = make()
    with pytest.raises(ModelRefused, match="more than the"):
        model.write(
            customer_id="demo-msp", messages=[Message("user", "x" * (MAX_PROMPT_BYTES + 1))]
        )
    assert fake.requests == []


@pytest.mark.parametrize(
    ("prices", "why"),
    [
        (OSError("down"), "did not complete"),
        (HttpResponse(404, {}, b"{}"), "HTTP 404"),
        (listing(), "lists no provider"),
        (listing(endpoint(prompt="-1")), "not a fixed amount"),
        (listing(endpoint(completion="0.1e-5")), "not a fixed amount"),
        (listing({**endpoint(), "supported_parameters": ["temperature"]}), "honours a limit"),
        (listing({**endpoint(), "pricing": {"completion": "0.00001"}}), "no prompt price"),
        (HttpResponse(200, {}, b"not json"), "lists no provider"),
    ],
)
def test_prices_that_cannot_be_read_stop_the_call_before_it_is_paid(
    make: Any, prices: Any, why: str
) -> None:
    model, fake = make(Fake(prices=prices))
    with pytest.raises(ModelRefused, match=why):
        write(model)
    assert fake.calls == [] and model.budget.committed_micro_usd == 0


def test_a_call_that_could_pass_the_run_limit_is_never_sent(make: Any) -> None:
    model, fake = make(budget=RunBudget(1000))
    with pytest.raises(SpendRefused):
        write(model)
    assert [r.method for r in fake.requests] == ["GET"]


def test_a_budget_larger_than_the_scope_allows_is_refused(
    scope: CustomerScope, credentials: CredentialFiles
) -> None:
    with pytest.raises(SpendRefused):
        OpenRouterWriter(
            scope=scope,
            credentials=credentials,
            budget=RunBudget(scope.execution.max_run_spend_micro_usd + 1),
            writer=WRITER,
        )


# The ceiling


def test_the_ceiling_is_the_most_the_dearest_listed_price_allows() -> None:
    prices = Prices(
        prompt=Decimal("0.000003"),
        completion=Decimal("0.000015"),
        output=Decimal("0.000015"),
        request=Decimal("0.001"),
    )
    # (1000 + 256) prompt tokens at $3/M, 500 output tokens at $15/M, and $0.001 a call.
    assert prices.ceiling_micro_usd(1000, 500) == 3768 + 7500 + 1000
    assert prices.max_price() == {"prompt": "3", "completion": "15", "request": "0.001"}
    free = Prices(Decimal(0), Decimal(0), Decimal(0), Decimal(0))
    assert free.ceiling_micro_usd(10, 10) == 1  # a reservation is never empty


def test_prices_are_the_highest_of_every_provider_and_window(make: Any) -> None:
    dear = endpoint(
        prompt="0.000002",
        completion="0.00002",
        internal_reasoning="0.00004",
        request="0.0005",
        overrides=[{"min_prompt_tokens": 200_000, "prompt": "0.000006"}],
    )
    model, _ = make(Fake(prices=listing(endpoint(), dear)))
    prices = model.prices()
    assert prices == Prices(
        prompt=Decimal("0.000006"),
        completion=Decimal("0.00002"),
        output=Decimal("0.00004"),
        request=Decimal("0.0005"),
    )
    assert model.most_per_call() == prices.ceiling_micro_usd(MAX_PROMPT_BYTES, 1000)


# The call


def test_the_call_asks_for_private_providers_within_the_listed_prices(make: Any) -> None:
    model, fake = make()
    reply = write(model)
    get, post = fake.requests
    assert get.url == f"https://openrouter.ai/api/v1/models/{MODEL}/endpoints"
    assert post.url == "https://openrouter.ai/api/v1/chat/completions"
    assert post.headers["Authorization"] == f"Bearer {KEY_VALUE}"
    assert KEY_VALUE not in repr(post) and KEY_VALUE not in repr(reply)
    body = json.loads(post.body)
    assert body == {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Write the draft."},
            {"role": "user", "content": "Facts: none."},
        ],
        "max_tokens": 1000,
        "stream": False,
        "provider": {
            "data_collection": "deny",
            "require_parameters": True,
            "max_price": {"prompt": "3", "completion": "15", "request": "0"},
        },
    }
    assert "tools" not in body


def test_zero_data_retention_is_asked_for_when_the_strategy_sets_it(make: Any) -> None:
    model, fake = make(writer=WRITER.model_copy(update={"zero_data_retention": True}))
    write(model)
    assert json.loads(fake.calls[0].body)["provider"]["zdr"] is True


def test_a_reply_settles_to_its_reported_cost(make: Any) -> None:
    model, _ = make()
    reply = write(model)
    ceiling = math_ceiling()
    assert reply.text == "Subject: Hello\n\nHi Dana," and reply.failure is None
    assert reply.served_by == MODEL
    assert dict(reply.attributes) == {
        "route": "openrouter.chat.completions",
        "model": MODEL,
        "ceiling_micro_usd": ceiling,
        "cost_micro_usd": 7200,
        "generation_id": "gen-synthetic-0001",
        "served_by": MODEL,
    }
    assert model.budget.spent_micro_usd == 7200


def math_ceiling() -> int:
    """Each prompt token at $3/M is 3 micro-USD; 1,000 output tokens at $15/M are 15,000."""
    return (PROMPT_BYTES + PROMPT_ALLOWANCE_TOKENS) * 3 + 1000 * 15


def test_a_cost_not_reported_counts_as_the_ceiling(make: Any) -> None:
    model, _ = make(Fake(reply=completion(usage={"prompt_tokens": 1})))
    reply = write(model)
    assert reply.attributes["cost_micro_usd"] == reply.attributes["ceiling_micro_usd"]
    assert model.budget.spent_micro_usd == reply.attributes["ceiling_micro_usd"]


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        (OSError("timed out"), "did not complete"),
        (HttpResponse(402, {}, b'{"error": {"code": 402, "message": "no"}}'), "credits"),
        (HttpResponse(404, {}, b"{}"), "No provider serves"),
        (HttpResponse(503, {}, b"{}"), "unavailable (HTTP 503)"),
        (HttpResponse(418, {}, b"{}"), "HTTP 418"),
        (HttpResponse(200, {}, b"[]"), "not a JSON object"),
        (completion(error={"code": 502, "message": "upstream"}), "reported an error"),
        (completion(choices=[]), "no choice"),
        (
            completion(choices=[{"finish_reason": "length", "message": {"content": "Hi"}}]),
            "output limit",
        ),
        (
            completion(choices=[{"finish_reason": "content_filter", "message": {}}]),
            "filter",
        ),
        (completion(choices=[{"finish_reason": None, "message": {"content": "Hi"}}]), "finish"),
        (
            completion(
                choices=[{"finish_reason": "stop", "message": {"content": "", "refusal": "No."}}]
            ),
            "refused",
        ),
        (completion(text="   "), "no text"),
        (completion(choices=[{"finish_reason": "stop", "message": {"content": None}}]), "no text"),
    ],
)
def test_a_call_that_brought_no_text_says_why_without_quoting_the_reply(
    make: Any, reply: Any, why: str
) -> None:
    model, _ = make(Fake(reply=reply))
    written = write(model)
    assert written.text is None and written.failure is not None and why in written.failure
    assert "upstream" not in written.failure and "No." not in written.failure
    assert model.budget.committed_micro_usd == written.attributes["cost_micro_usd"]


def test_a_reply_that_echoes_the_key_keeps_nothing_from_it(make: Any) -> None:
    model, _ = make(Fake(reply=completion(text=f"Your key is {KEY_VALUE}")))
    written = write(model)
    assert (written.text, written.failure, written.served_by) == (None, ECHOED, None)
    assert "generation_id" not in written.attributes


def test_a_price_listing_that_echoes_the_key_is_refused(make: Any) -> None:
    echoed = listing({**endpoint(), "name": KEY_VALUE})
    model, fake = make(Fake(prices=echoed))
    with pytest.raises(ModelRefused) as refused:
        write(model)
    assert KEY_VALUE not in str(refused.value) and fake.calls == []


def test_the_served_model_and_generation_are_kept_only_when_well_formed(make: Any) -> None:
    model, _ = make(Fake(reply=completion(id="gen <script>", model="x" * 300)))
    written = write(model)
    assert written.served_by is None and "generation_id" not in written.attributes
    assert written.text is not None


def test_the_transport_reaches_only_openrouter() -> None:
    request = HttpRequest(
        method="GET", url="https://example.invalid/api", headers={}, body=b"", timeout=1
    )
    with pytest.raises(ValueError, match=r"openrouter\.ai"):
        HttpsTransport().send(request)
