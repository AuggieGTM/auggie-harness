from __future__ import annotations

from pathlib import Path

import pytest

from auggie.cli import main
from tests.conftest import NORTHSTAR


def test_run_reports_every_stage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", str(NORTHSTAR)]) == 0
    lines = capsys.readouterr().out.splitlines()
    stages = [line.split()[0] for line in lines if line and not line.startswith(" ")][1:-1]
    assert stages == [
        "define",
        *["observe"] * 3,
        *["validate"] * 3,
        "judge",
        "compose",
        *["approve"] * 2,
        "deliver",
        "learn",
    ]
    assert f"validate  {'ev-northstar-news':<24} rejected: retrieval_failed" in lines
    assert lines[-1] == "13 events in the demo-msp store."


def test_show_draft_prints_the_composed_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", str(NORTHSTAR), "--show-draft"]) == 0
    assert "          | Hi Jordan," in capsys.readouterr().out.splitlines()


def test_negative_paths_are_all_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", str(NORTHSTAR), "--negative"]) == 0
    out = capsys.readouterr().out
    assert out.count("\nblocked   ") == 6
    assert "NOT BLOCKED" not in out


def test_a_missing_scenario_stops_with_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(tmp_path)]) == 1
    assert "stopped   FileNotFoundError" in capsys.readouterr().out
