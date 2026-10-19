"""Recorded model replies, for rehearsing a play written by a model with no provider call
(ADR 0031)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import Field

from auggie.compose.openrouter import Message, ModelReply
from auggie.contracts import Actor, Contract
from auggie.contracts.common import RecordId
from auggie.contracts.events import AttributeValue
from auggie.spend import RunBudget


class RecordedReplies(Contract):
    """What the model is recorded as writing for one account, attempt by attempt."""

    account_id: RecordId
    texts: tuple[str, ...] = Field(min_length=1, max_length=2)


class RecordedModel:
    """Answers each account's calls with its recorded texts, in order. Never pays."""

    actor = Actor(kind="service", id="recorded-model", version="1")

    def __init__(self, model: str, replies: Sequence[RecordedReplies]) -> None:
        self._model = model
        ids = [r.account_id for r in replies]
        if len(set(ids)) != len(ids):
            raise ValueError("model_replies names an account twice")
        self._replies: Mapping[str, list[str]] = {r.account_id: list(r.texts) for r in replies}

    @property
    def budget(self) -> RunBudget | None:
        return None

    @property
    def model(self) -> str:
        return self._model

    def ready(self) -> None:
        return None

    def most_per_call(self) -> int:
        return 0

    def write(
        self, *, customer_id: str, messages: Sequence[Message], account_id: str | None = None
    ) -> ModelReply:
        attributes: dict[str, AttributeValue] = {
            "route": "recorded",
            "model": self._model,
            "cost_micro_usd": 0,
        }
        pending = self._replies.get(account_id or "", [])
        if not pending:
            return ModelReply(None, "No reply was recorded for this account.", None, attributes)
        served = f"recorded {self._model}"
        attributes["served_by"] = served
        return ModelReply(pending.pop(0), None, served, attributes)


def recorded_model(model: str, recorded: object) -> RecordedModel:
    """The model replies a replay file records under ``model_replies``, if any."""
    found = recorded.get("model_replies", []) if isinstance(recorded, dict) else []
    if not isinstance(found, list):
        raise ValueError("model_replies must be a list of recorded replies")
    return RecordedModel(model, [RecordedReplies.model_validate(r) for r in found])
