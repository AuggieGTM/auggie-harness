"""Learning from outcomes (ADR 0028): what each delivery led to, counted by play, pain, proof
and met criterion. Synthetic sellers only."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from auggie import shell
from auggie.cli import main
from auggie.learning import NO_PLAY, Delivery, deliveries, learn_view, tally
from auggie.operate import report_outcome
from auggie.synthetic import run_scenario
from tests.conftest import EXAMPLES, MONGODB_URI, NORTHSTAR, mongo_backend

KESTREL = EXAMPLES / "kestrel-security"


def delivery(*outcomes: str, play: str = "p", proof: str | None = None) -> Delivery:
    return Delivery("d", play, None, proof, ("c",), outcomes)


@pytest.mark.parametrize(
    ("outcomes", "result"),
    [
        ((), "waiting"),
        (("no_reply",), "negative"),
        (("no_reply", "positive_reply"), "positive"),
        (("negative_reply", "meeting_booked"), "positive"),
        (("bounce",), "bounced"),
        (("bounce", "positive_reply"), "positive"),
        (("opt_out",), "negative"),
    ],
)
def test_a_deliverys_result_comes_from_every_outcome_reported(
    outcomes: tuple[str, ...], result: str
) -> None:
    assert delivery(*outcomes).result == result


def test_a_rate_counts_only_settled_deliveries() -> None:
    (counted,) = tally(
        [
            delivery("positive_reply"),
            delivery("opt_out"),
            delivery("no_reply"),
            delivery("bounce"),
            delivery(),
        ],
        lambda d: [d.play or NO_PLAY],
    )
    assert counted.view() == {
        "key": "p",
        "delivered": 5,
        "positive": 1,
        "negative": 2,
        "opted_out": 1,
        "bounced": 1,
        "waiting": 1,
        "settled": 3,
        "rate": 0.333,
    }
    (none_settled,) = tally([delivery()], lambda d: ["p"])
    assert none_settled.rate is None


def test_the_report_counts_a_delivery_by_what_it_was_composed_from() -> None:
    run = run_scenario(KESTREL)  # delivered, and a meeting was booked
    (found,) = deliveries(run.store)
    assert (found.play, found.pain, found.proof) == (
        "detection-triage",
        "alert-triage",
        "harborview-triage",
    )
    assert found.outcomes == ("meeting_booked",) and found.result == "positive"
    view = learn_view(run.store)
    assert view["deliveries"] == 1
    (strategy,) = view["strategies"]
    assert strategy["strategy"] == "strategy/security.toml"
    assert [row["key"] for row in strategy["criteria"]] == [
        "detection-hiring",
        "detection-openings-new",
        "names-beaconlog",
    ]
    assert strategy["plays"][0] | {"key": None} == {
        "key": None,
        "delivered": 1,
        "positive": 1,
        "negative": 0,
        "opted_out": 0,
        "bounced": 0,
        "waiting": 0,
        "settled": 1,
        "rate": 1.0,
    }
    # A second report for the same delivery does not count it twice.
    report_outcome(
        run.store, draft_id=found.draft_id, outcome="negative_reply", reporter="demo-reviewer"
    )
    assert learn_view(run.store)["strategies"][0]["plays"][0]["delivered"] == 1


def test_a_draft_without_a_pain_is_counted_by_its_play_alone() -> None:
    run = run_scenario(NORTHSTAR)
    (view,) = learn_view(run.store)["strategies"]
    (play,) = view["plays"]
    assert play["key"] == run.strategy.strategy.plays[0].id
    assert view["pains"] == [] and view["proofs"] == []


def test_a_draft_from_before_plays_were_recorded_is_counted_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from auggie import learning

    legacy = Delivery("draft-old", None, None, None, ("c",), ("positive_reply",))
    monkeypatch.setattr(learning, "deliveries", lambda store: [legacy])
    (row,) = learn_view(run_scenario(NORTHSTAR).store)["strategies"][0]["plays"]
    assert (row["key"], row["positive"]) == (NO_PLAY, 1)


def test_the_report_is_text_in_the_shell_and_json_for_an_agent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from auggie import cli

    lines = shell.render_learn(learn_view(run_scenario(KESTREL).store))
    assert lines[0].startswith("1 deliveries. A rate is positive deliveries over settled ones")
    assert any(
        line.startswith("  detection-triage") and line.endswith("rate 100%") for line in lines
    )
    monkeypatch.setattr(cli, "interactive", lambda: False)
    with mongo_backend() as backend:
        store = ["--mongodb", os.environ[MONGODB_URI], "--database-prefix", backend.database_prefix]
        assert main(["run", str(KESTREL), *store]) == 0
        capsys.readouterr()
        assert main(["learn", str(KESTREL / "scope.json"), "--json", *store]) == 0
        view: dict[str, Any] = json.loads(capsys.readouterr().out)
        assert view["deliveries"] == 1
        assert view["strategies"][0]["proofs"][0]["key"] == "harborview-triage"


def test_a_drafts_play_and_its_point_of_views_play_must_agree() -> None:
    from pydantic import ValidationError

    from auggie.contracts import Artifact
    from tests.conftest import variant

    draft = run_scenario(KESTREL).result.draft
    assert draft is not None
    with pytest.raises(ValidationError, match="play and its point of view's play must agree"):
        Artifact.model_validate(variant(draft, play="detection-build-out"))
