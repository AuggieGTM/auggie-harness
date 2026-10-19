"""Learning from outcomes (ADR 0028): what each delivery led to, counted by play, pain, proof
and met criterion. Reads the Store only, and judges nothing a person has not reported."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from auggie.store import Store

Result = Literal["positive", "negative", "bounced", "waiting"]
POSITIVE = frozenset({"positive_reply", "meeting_booked"})
NEGATIVE = frozenset({"negative_reply", "opt_out", "no_reply"})


@dataclass(frozen=True)
class Delivery:
    """One delivered draft, what it was composed from, and every outcome reported for it."""

    draft_id: str
    play: str | None
    pain: str | None
    proof: str | None
    criteria: tuple[str, ...]
    """The criteria its qualification found met."""
    outcomes: tuple[str, ...]
    strategy: str = ""
    """The strategy file it was composed under. Plays, pains and proofs are named within one
    file: the same ID in another file is another play."""

    @property
    def result(self) -> Result:
        reported = set(self.outcomes)
        if reported & POSITIVE:
            return "positive"
        if "bounce" in reported:
            return "bounced"
        if reported & NEGATIVE:
            return "negative"
        return "waiting"

    @property
    def opted_out(self) -> bool:
        return "opt_out" in self.outcomes


def deliveries(store: Store) -> list[Delivery]:
    """Every delivered draft of the scope, in the order delivered."""
    delivered: dict[str, None] = {}
    for type in ("delivery.simulated", "delivery.sent"):
        for event in store.list_events(type=type):
            for ref in event.refs:
                if ref.kind == "artifact":
                    delivered.setdefault(ref.id, None)
    outcomes: dict[str, list[str]] = {}
    for event in store.list_events(type="outcome.recorded"):
        for ref in event.refs:
            if ref.kind == "artifact":
                outcomes.setdefault(ref.id, []).append(str(event.attributes.get("outcome")))
    found = []
    for draft_id in delivered:
        # A delivered draft is never revised again, so its latest version is the one sent.
        draft = store.latest_artifact(draft_id)
        view = draft.point_of_view
        decision = store.get_qualification(draft.qualification_id)
        found.append(
            Delivery(
                draft_id=draft_id,
                play=draft.play or (view.play if view else None),
                pain=view.pain if view else None,
                proof=view.proof.proof_id if view and view.proof else None,
                criteria=tuple(f.criterion for f in decision.findings if f.assessment == "meets"),
                outcomes=tuple(outcomes.get(draft_id, ())),
                strategy=draft.strategy.path,
            )
        )
    return found


@dataclass(frozen=True)
class Tally:
    """What the deliveries with one play, pain, proof or met criterion led to."""

    key: str
    delivered: int = 0
    positive: int = 0
    negative: int = 0
    opted_out: int = 0
    bounced: int = 0
    waiting: int = 0

    @property
    def settled(self) -> int:
        return self.positive + self.negative

    @property
    def rate(self) -> float | None:
        """Positive deliveries over settled ones; deliveries still waiting never lower it."""
        return self.positive / self.settled if self.settled else None

    def view(self) -> dict[str, Any]:
        rate = self.rate
        return {
            "key": self.key,
            "delivered": self.delivered,
            "positive": self.positive,
            "negative": self.negative,
            "opted_out": self.opted_out,
            "bounced": self.bounced,
            "waiting": self.waiting,
            "settled": self.settled,
            "rate": round(rate, 3) if rate is not None else None,
        }


def tally(found: Iterable[Delivery], by: Callable[[Delivery], Iterable[str]]) -> list[Tally]:
    """The deliveries counted under each key ``by`` gives them, in the order first seen."""
    counts: dict[str, dict[str, int]] = {}
    for delivery in found:
        for key in by(delivery):
            count = counts.setdefault(key, {"delivered": 0})
            count["delivered"] += 1
            count[delivery.result] = count.get(delivery.result, 0) + 1
            if delivery.opted_out:
                count["opted_out"] = count.get("opted_out", 0) + 1
    return [Tally(key, **count) for key, count in counts.items()]


NO_PLAY = "(no play recorded)"


def under(found: Iterable[Delivery], strategy_path: str) -> list[Delivery]:
    """The deliveries composed under one strategy file, any of its revisions."""
    return [d for d in found if d.strategy == strategy_path]


def learn_view(store: Store) -> dict[str, Any]:
    """The report `auggie learn` prints: deliveries counted by play, pain, proof and criterion,
    for each strategy file they were composed under. IDs are never pooled across files."""
    found = deliveries(store)
    files = list(dict.fromkeys(d.strategy for d in found))
    return {
        "deliveries": len(found),
        "strategies": [_strategy_view(path, under(found, path)) for path in files],
        "basis": "reported outcomes",
    }


def _strategy_view(path: str, found: list[Delivery]) -> dict[str, Any]:
    return {
        "strategy": path,
        "deliveries": len(found),
        "plays": [t.view() for t in tally(found, lambda d: [d.play or NO_PLAY])],
        "pains": [t.view() for t in tally(found, lambda d: [d.pain] if d.pain else [])],
        "proofs": [t.view() for t in tally(found, lambda d: [d.proof] if d.proof else [])],
        "criteria": [t.view() for t in tally(found, lambda d: d.criteria)],
    }
