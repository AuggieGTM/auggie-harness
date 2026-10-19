"""OpenRouter: one model call per draft attempt, with the seller's own key (ADR 0031).

Before each call, the model's listed prices are read, free, and sent back as
``provider.max_price``, so no dearer provider can serve it. The call's ceiling is
the most those prices allow for a bound on its tokens, and is reserved against
the run's budget first. Every call asks only for providers that neither keep nor
train on the data. No retries here, and the model is given no tools.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from auggie.contracts import Actor, CustomerScope, ScopeViolation, Writer
from auggie.contracts.events import AttributeValue
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

OPENROUTER_ORIGIN = "https://openrouter.ai"
API = f"{OPENROUTER_ORIGIN}/api/v1"
ROUTE = "openrouter.chat.completions"
KEY = "openrouter-key"
"""Name of the API key file in the scope's credential folder."""

MAX_PROMPT_BYTES = 24_000
"""The longest prompt sent, in UTF-8 bytes, so the most a call can cost is known before it."""

MAX_MESSAGES = 8
"""The most messages a call sends: the instructions, the facts, and a retry's two more."""

PROMPT_ALLOWANCE_TOKENS = 256
MESSAGE_ALLOWANCE_TOKENS = 32
"""Tokens a chat format may add around the prompt, and around each message. A prompt has no
more tokens than bytes."""

TIMEOUT_SECONDS = 120.0

ECHOED = "The reply contained the request's key; nothing of it was kept."

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}")
_GENERATION = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_MILLION = Decimal(1_000_000)


class HttpsTransport(LimitedTransport):
    """Sends requests only to OpenRouter, never following redirects."""

    def __init__(self) -> None:
        super().__init__((OPENROUTER_ORIGIN,), "OpenRouter")


class ModelRefused(Exception):
    """A model call cannot be made: the model's prices are unknown, no provider honours a
    token limit, or the prompt is too long. Nothing paid was sent."""


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str = field(repr=False)


@dataclass(frozen=True)
class Prices:
    """The highest price any provider of one model lists, in US dollars, in every pricing
    window it lists."""

    prompt: Decimal
    """Per prompt token."""
    completion: Decimal
    """Per completion token, as ``provider.max_price`` bounds it."""
    output: Decimal
    """Per output token, the higher of the completion and reasoning prices."""
    request: Decimal
    """Per call."""

    def ceiling_micro_usd(self, prompt_bytes: int, messages: int, max_tokens: int) -> int:
        """The most one call can cost: every prompt token, as many as its bytes plus the chat
        format's allowance for the prompt and each message, and every output token up to
        ``max_tokens``."""
        tokens = prompt_bytes + PROMPT_ALLOWANCE_TOKENS + messages * MESSAGE_ALLOWANCE_TOKENS
        cost = tokens * self.prompt + max_tokens * self.output + self.request
        return max(1, math.ceil(cost * _MILLION))

    def max_price(self) -> dict[str, str]:
        """``provider.max_price``: US dollars per million tokens, and per request."""
        return {
            "prompt": _plain(self.prompt * _MILLION),
            "completion": _plain(self.completion * _MILLION),
            "request": _plain(self.request),
        }


@dataclass(frozen=True)
class ModelReply:
    """What came of one paid call: the text, or why there is none."""

    text: str | None = field(repr=False)
    failure: str | None
    served_by: str | None
    """The model OpenRouter says served the call."""
    attributes: Mapping[str, AttributeValue]


class TextModel(Protocol):
    """Writes text for one scope, one paid call at a time."""

    @property
    def actor(self) -> Actor:
        """The adapter, as the events of its calls name it."""
        ...

    @property
    def budget(self) -> RunBudget | None:
        """The run budget every call is reserved against; None for one that never pays."""
        ...

    @property
    def model(self) -> str:
        """The model it asks."""
        ...

    def ready(self) -> None:
        """Raise, sending nothing, if no call could be made: the scope allows no external
        calls, or there is no usable key."""
        ...

    def most_per_call(self) -> int:
        """The most one call can cost, in micro-USD, at the longest prompt it sends."""
        ...

    def write(self, *, customer_id: str, messages: Sequence[Message]) -> ModelReply:
        """Make one call, or raise before anything paid is sent."""
        ...


class OpenRouterWriter:
    """Calls the strategy's model through OpenRouter for one scope."""

    actor = Actor(kind="service", id="openrouter-chat", version="1")

    def __init__(
        self,
        *,
        scope: CustomerScope,
        credentials: CredentialFiles,
        budget: RunBudget,
        writer: Writer,
        transport: Transport | None = None,
    ) -> None:
        if budget.limit_micro_usd > scope.execution.max_run_spend_micro_usd:
            raise SpendRefused(
                f"a run budget of {budget.limit_micro_usd} micro-USD is more than the scope "
                f"allows ({scope.execution.max_run_spend_micro_usd})"
            )
        self._scope = scope
        self._credentials = credentials
        self._budget = budget
        self._writer = writer
        self._transport = transport or HttpsTransport()

    @property
    def budget(self) -> RunBudget:
        return self._budget

    @property
    def model(self) -> str:
        return self._writer.model

    def ready(self) -> None:
        require_external_calls(self._scope)
        self._key()

    def prices(self) -> Prices:
        """Read the model's listed prices, free. Raises ``ModelRefused`` if they cannot be
        read or used."""
        require_external_calls(self._scope)
        return self._prices(self._key())

    def most_per_call(self) -> int:
        return self.prices().ceiling_micro_usd(
            MAX_PROMPT_BYTES, MAX_MESSAGES, self._writer.max_output_tokens
        )

    def write(self, *, customer_id: str, messages: Sequence[Message]) -> ModelReply:
        """Make one paid call, unless the scope, its key, the prices or the budget forbid it.

        Refusals raise before anything paid is sent. A call that was sent always returns a
        reply, with no text if nothing usable came back.
        """
        if customer_id != self._scope.customer_id:
            raise ScopeViolation(f"this writer serves {self._scope.customer_id!r}")
        require_external_calls(self._scope)
        if not 0 < len(messages) <= MAX_MESSAGES:
            raise ModelRefused(f"a model call sends one to {MAX_MESSAGES} messages")
        size = sum(len(m.content.encode("utf-8")) for m in messages)
        if size > MAX_PROMPT_BYTES:
            raise ModelRefused(
                f"the prompt is {size} bytes, more than the {MAX_PROMPT_BYTES} a call may send"
            )
        key = self._key()
        prices = self._prices(key)
        max_tokens = self._writer.max_output_tokens
        ceiling = prices.ceiling_micro_usd(size, len(messages), max_tokens)
        reservation = self._budget.reserve(ceiling)
        provider: dict[str, Any] = {
            "data_collection": "deny",
            "require_parameters": True,
            "max_price": prices.max_price(),
        }
        if self._writer.zero_data_retention:
            provider["zdr"] = True
        request = HttpRequest(
            method="POST",
            url=f"{API}/chat/completions",
            headers=self._headers(key) | {"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "model": self._writer.model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "max_tokens": max_tokens,
                    "stream": False,
                    "provider": provider,
                }
            ).encode(),
            timeout=TIMEOUT_SECONDS,
        )
        try:
            response: HttpResponse | None = self._transport.send(request)
        except OSError:
            response = None
        body = _json(response)
        charged = self._budget.settle(reservation, _charge(body))
        attributes: dict[str, AttributeValue] = {
            "route": ROUTE,
            "model": self._writer.model,
            "ceiling_micro_usd": ceiling,
            "cost_micro_usd": charged,
        }
        if response is not None and echoes(response, key):
            return ModelReply(None, ECHOED, None, attributes)
        served_by = None
        if isinstance(body, dict):
            generation = body.get("id")
            if isinstance(generation, str) and _GENERATION.fullmatch(generation):
                attributes["generation_id"] = generation
            model = body.get("model")
            if isinstance(model, str) and _MODEL.fullmatch(model):
                served_by = model
                attributes["served_by"] = model
        text, failure = _read(response, body)
        return ModelReply(text, failure, served_by, attributes)

    def _key(self) -> str:
        key = self._credentials.secret(self._scope, KEY).reveal()
        if not re.fullmatch(r"[!-~]+", key):
            raise CredentialError(
                f"cannot use credential {self._scope.credential_namespace!r}/{KEY!r}: "
                "it holds characters an HTTP header cannot carry"
            )
        return key

    @staticmethod
    def _headers(key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}

    def _prices(self, key: str) -> Prices:
        model = self._writer.model
        refused = f"cannot read the prices of {model!r} from OpenRouter"
        request = HttpRequest(
            method="GET",
            url=f"{API}/models/{model}/endpoints",
            headers=self._headers(key),
            body=b"",
            timeout=TIMEOUT_SECONDS,
        )
        try:
            response = self._transport.send(request)
        except OSError:
            raise ModelRefused(f"{refused}: the call did not complete") from None
        if response.status != 200:
            raise ModelRefused(f"{refused}: OpenRouter answered HTTP {response.status}")
        if echoes(response, key):
            raise ModelRefused(f"{refused}: {ECHOED}")
        body = _json(response)
        data = body.get("data") if isinstance(body, dict) else None
        endpoints = data.get("endpoints") if isinstance(data, dict) else None
        if not isinstance(endpoints, list) or not endpoints:
            raise ModelRefused(f"{refused}: it lists no provider for the model")
        if isinstance(data, dict) and data.get("id") != model:
            raise ModelRefused(f"{refused}: the listing it sent is of another model")
        highest: dict[str, Decimal] = {}
        limited = False
        for endpoint in endpoints:
            pricing = endpoint.get("pricing") if isinstance(endpoint, dict) else None
            if not isinstance(pricing, dict):
                raise ModelRefused(f"{refused}: a provider lists no prices")
            overrides = pricing.get("overrides", [])
            if not isinstance(overrides, list) or not all(isinstance(o, dict) for o in overrides):
                raise ModelRefused(f"{refused}: a provider's price windows are malformed")
            for window in (pricing, *overrides):
                for name in ("prompt", "completion", "request", "internal_reasoning"):
                    if name in window:
                        price = _price(window[name])
                        if price is None:
                            raise ModelRefused(f"{refused}: a {name} price is not a fixed amount")
                        highest[name] = max(highest.get(name, price), price)
            for name in ("prompt", "completion"):
                if name not in pricing:
                    raise ModelRefused(f"{refused}: a provider lists no {name} price")
            supported = endpoint.get("supported_parameters")
            limited = limited or (isinstance(supported, list) and "max_tokens" in supported)
        if not limited:
            raise ModelRefused(f"no provider of {model!r} honours a limit on the tokens it writes")
        completion = highest["completion"]
        return Prices(
            prompt=highest["prompt"],
            completion=completion,
            output=max(completion, highest.get("internal_reasoning", completion)),
            request=highest.get("request", Decimal(0)),
        )


def _plain(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".")


def _price(value: object) -> Decimal | None:
    """A listed price: a decimal string, finite and not negative (OpenRouter lists a
    negative price for a model whose price varies)."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,20})?", value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _json(response: HttpResponse | None) -> object:
    if response is None or len(response.body) > MAX_BODY_BYTES:
        return None
    try:
        return json.loads(response.body)
    except (ValueError, RecursionError):
        return None


def _charge(body: object) -> int | None:
    """What OpenRouter reports the call cost, in micro-USD rounded up, or None. Its credits
    are US dollars."""
    usage = body.get("usage") if isinstance(body, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    if not math.isfinite(cost) or cost < 0 or cost > 1_000_000:
        return None
    return math.ceil(Decimal(str(cost)) * _MILLION)


BY_STATUS = {
    400: "OpenRouter refused the request (400).",
    401: "OpenRouter refused the key (401).",
    402: "OpenRouter refused the call: the account has too few credits (402).",
    403: "OpenRouter's moderation refused the prompt (403).",
    404: "No provider serves the model within these limits (404).",
    408: "The model timed out (408).",
    413: "The prompt was too large for the model (413).",
    429: "Rate limited (429).",
}


def _read(response: HttpResponse | None, body: object) -> tuple[str | None, str | None]:
    """The reply's text, or why there is none. Reasons never quote the reply."""
    if response is None:
        return None, "The OpenRouter call did not complete (timeout or network error)."
    if response.status != 200:
        if response.status >= 500:
            return None, f"The model's provider was unavailable (HTTP {response.status})."
        return None, BY_STATUS.get(response.status, f"OpenRouter answered HTTP {response.status}.")
    if not isinstance(body, dict):
        return None, "The reply was not a JSON object."
    if body.get("error") is not None:
        return None, "OpenRouter reported an error in its reply."
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "The reply held no choice."
    choice = choices[0]
    if choice.get("error") is not None:
        return None, "The provider reported an error in its reply."
    reason = choice.get("finish_reason")
    if reason == "length":
        return None, "The model stopped at its output limit before finishing."
    if reason == "content_filter":
        return None, "The provider's filter stopped the reply."
    if reason != "stop":
        return None, "The model did not finish its reply."
    message = choice.get("message")
    if not isinstance(message, dict):
        return None, "The reply held no message."
    if message.get("refusal"):
        return None, "The model refused to write the draft."
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        return None, "The reply held no text."
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None, "The reply was not valid Unicode."
    return text, None
