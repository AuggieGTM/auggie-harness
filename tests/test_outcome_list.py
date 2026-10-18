"""Outcomes taken in from a CSV (ADR 0028), previewed against the Store before anything is
recorded. Synthetic sellers only."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auggie.cli import main
from auggie.learning import learn_view
from auggie.outcome_list import OutcomesRefused, check, read_outcomes, record
from auggie.synthetic import load_inputs, run_scenario
from tests.conftest import EXAMPLES, MONGODB_URI, mongo_backend

KESTREL = EXAMPLES / "kestrel-security"
HEADER = "draft,outcome,occurred_at\n"
NOW = datetime(2026, 10, 20, tzinfo=UTC)


def test_rows_are_read_and_checked_on_their_own() -> None:
    rows = read_outcomes(
        HEADER
        + "draft-a,positive_reply,2026-10-17\n"
        + "draft-a,positive_reply,2026-10-17T09:30:00+02:00\n"
        + "draft-b,replied,2026-10-17\n"
        + "draft-c,no_reply,2026-10-17T09:30:00\n"
        + "draft-d,no_reply,yesterday\n"
        + "draft-a,positive_reply,2026-10-17\n"
    )
    assert [(r.status, r.problems) for r in rows][:2] == [("new", ()), ("new", ())]
    assert rows[0].occurred_at == datetime(2026, 10, 17, tzinfo=UTC)
    assert rows[1].occurred_at == datetime(2026, 10, 17, 7, 30, tzinfo=UTC)
    assert "outcome must be one of positive_reply" in rows[2].problems[0]
    assert rows[3].problems == ("occurred_at: a time needs its zone, such as Z or +01:00",)
    assert rows[4].problems[0].startswith("occurred_at: Invalid isoformat")
    assert rows[5].problems == ("repeats line 2",)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "the list is empty"),
        ("draft,outcome\n", "the columns must be draft, outcome, occurred_at"),
        ('draft,outcome,occurred_at\n"draft-a,positive_reply,2026-10-17\n', "not readable CSV"),
    ],
    ids=["empty", "missing-column", "unterminated-quote"],
)
def test_a_list_that_cannot_be_read_is_refused_whole(text: str, problem: str) -> None:
    with pytest.raises(OutcomesRefused, match=problem):
        read_outcomes(text)


def test_each_row_is_checked_against_the_store_then_recorded() -> None:
    run = run_scenario(KESTREL)  # delivered on 2026-10-16; a meeting booked on 2026-10-19
    draft = "draft-fernhollow-001"
    rows = check(
        read_outcomes(
            HEADER
            + f"{draft},positive_reply,2026-10-17T10:00:00Z\n"
            + f"{draft},meeting_booked,2026-10-19T17:00:00Z\n"
            + "draft-elsewhere,positive_reply,2026-10-17\n"
            + f"{draft},no_reply,2026-10-01\n"
            + f"{draft},no_reply,2999-01-01\n"
        ),
        run.store,
        lambda: NOW,
    )
    assert [(r.status, r.problems) for r in rows] == [
        ("new", ()),
        ("recorded", ()),
        ("refused", ("no such draft in this scope",)),
        ("refused", ("before it was delivered, at 2026-10-16T15:05:00+00:00",)),
        ("refused", ("in the future",)),
    ]
    (appended,) = record(rows, run.store, "demo-reviewer")
    assert appended.event.idempotency_key.endswith(":2")  # the draft's second report
    assert appended.event.actor.id == "demo-reviewer"
    assert learn_view(run.store)["plays"][0]["positive"] == 1
    again = check(
        read_outcomes(HEADER + f"{draft},positive_reply,2026-10-17T10:00:00Z\n"),
        run.store,
        lambda: NOW,
    )
    assert again[0].status == "recorded"


def test_a_draft_that_was_never_delivered_takes_no_outcome() -> None:
    inputs = load_inputs(KESTREL).model_copy(update={"delivery": None, "outcome": None})
    undelivered = run_scenario(KESTREL, inputs=inputs)
    (row,) = check(
        read_outcomes(HEADER + "draft-fernhollow-001,positive_reply,2026-10-17\n"),
        undelivered.store,
        lambda: NOW,
    )
    assert (row.status, row.problems) == ("refused", ("never delivered",))


def test_the_outcome_command_previews_then_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listed = tmp_path / "outcomes.csv"
    listed.write_text(
        HEADER + "draft-fernhollow-001,positive_reply,2026-10-17T10:00:00Z\n", encoding="utf-8"
    )
    with mongo_backend() as backend:
        store = ["--mongodb", os.environ[MONGODB_URI], "--database-prefix", backend.database_prefix]
        assert main(["run", str(KESTREL), *store]) == 0
        capsys.readouterr()
        argv = [
            "outcome",
            str(KESTREL / "scope.json"),
            "--from",
            str(listed),
            "--reporter",
            "demo-reviewer",
            *store,
        ]
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "1 new, 0 already recorded, 0 refused" in out and "Nothing recorded" in out
        assert main([*argv, "--write"]) == 0
        assert "Recorded 1 outcomes." in capsys.readouterr().out
        assert main([*argv, "--write"]) == 0
        assert "0 new, 1 already recorded, 0 refused" in capsys.readouterr().out
        assert (
            main(
                [
                    "outcome",
                    str(KESTREL / "scope.json"),
                    "--draft",
                    "draft-fernhollow-001",
                    "--reporter",
                    "x",
                    *store,
                ]
            )
            == 1
        )
        assert "--draft needs --outcome" in capsys.readouterr().out
