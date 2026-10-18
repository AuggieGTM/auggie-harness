from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from auggie import cli
from auggie.cli import main
from auggie.projections.exchange import load_workbook
from auggie.synthetic import run_scenario
from tests.conftest import MONGODB_URI, NORTHSTAR, mongo_backend
from tests.test_exchange import as_grid
from tests.test_sheets import MemorySheets
from tests.test_treg import CAREERS, TOKEN_VALUE, Replay, reply


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
    assert lines[-1] == "14 events in the demo-msp store."


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


def test_run_can_keep_records_in_mongodb(capsys: pytest.CaptureFixture[str]) -> None:
    with mongo_backend() as backend:
        uri = os.environ[MONGODB_URI]
        prefix = backend.database_prefix
        argv = ["run", str(NORTHSTAR), "--mongodb", uri, "--database-prefix", prefix]
        assert main(argv) == 0
        first = capsys.readouterr().out
        assert f"Stored in MongoDB database {prefix}auggie_demo_msp." in first
        assert main(argv) == 0
        assert "already delivered; nothing sent again" in capsys.readouterr().out


def treg_setup(tmp_path: Path, *, external: bool = True) -> tuple[Path, Path]:
    """A paid copy of the Northstar scope on disk, and a credentials root holding its token."""
    scope = json.loads((NORTHSTAR / "inputs/inputs.json").read_text(encoding="utf-8"))["scope"]
    scope["execution"] = {
        "delivery": "simulated",
        "external_providers": external,
        "max_run_spend_micro_usd": 10_000,
    }
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(json.dumps(scope), encoding="utf-8")
    folder = tmp_path / "credentials" / scope["credential_namespace"]
    folder.mkdir(mode=0o700, parents=True)
    (folder / "treg-token").write_text(TOKEN_VALUE, encoding="utf-8")
    (folder / "treg-token").chmod(0o600)
    return scope_file, tmp_path / "credentials"


def test_treg_fetch_makes_one_call_and_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replay = Replay(reply("page-text.json"))
    monkeypatch.setattr(cli, "treg_transport", lambda: replay)
    scope_file, credentials = treg_setup(tmp_path)
    argv = ["treg-fetch", str(scope_file), CAREERS, "--credentials", str(credentials)]
    assert main([*argv, "--show-excerpt"]) == 0
    out = capsys.readouterr().out
    assert "status    retrieved" in out
    assert "treg      cost_micro_usd: 1000" in out
    assert "spent     1000 of 10000 micro-USD" in out
    assert "| Northstar Works is hiring" in out
    assert TOKEN_VALUE not in out
    assert len(replay.requests) == 1


def test_treg_fetch_reports_an_unusable_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "treg_transport", lambda: Replay(reply("unavailable-503.json")))
    scope_file, credentials = treg_setup(tmp_path)
    assert main(["treg-fetch", str(scope_file), CAREERS, "--credentials", str(credentials)]) == 2
    assert (
        "status    failed: Treg or the provider was unavailable (503)." in capsys.readouterr().out
    )


def test_treg_fetch_refuses_a_scope_without_external_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replay = Replay(reply("page-text.json"))
    monkeypatch.setattr(cli, "treg_transport", lambda: replay)
    scope_file, credentials = treg_setup(tmp_path, external=False)
    assert main(["treg-fetch", str(scope_file), CAREERS, "--credentials", str(credentials)]) == 1
    assert "stopped   ExternalCallRefused" in capsys.readouterr().out
    assert replay.requests == []


def test_treg_fetch_stops_on_an_unreadable_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "scope.json").write_text("{}", encoding="utf-8")
    assert main(["treg-fetch", str(tmp_path / "scope.json"), CAREERS]) == 1
    assert "stopped   cannot read the scope: ValidationError" in capsys.readouterr().out


def test_treg_fetch_refuses_an_invalid_url_before_calling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replay = Replay(reply("page-text.json"))
    monkeypatch.setattr(cli, "treg_transport", lambda: replay)
    scope_file, credentials = treg_setup(tmp_path)
    assert (
        main(["treg-fetch", str(scope_file), "not-a-url", "--credentials", str(credentials)]) == 1
    )
    assert "stopped   the URL is not a valid http(s) URL" in capsys.readouterr().out
    assert replay.requests == []


def test_run_can_export_the_projection_as_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", str(NORTHSTAR), "--csv", str(tmp_path / "out")]) == 0
    assert "accounts.csv, findings.csv, evidence.csv, drafts.csv" in capsys.readouterr().out
    accounts = (tmp_path / "out" / "accounts.csv").read_text(encoding="utf-8").splitlines()
    assert accounts[1].startswith("acc-northstar,qualified,")


def scope_file(tmp_path: Path, *, external: bool) -> Path:
    scope = json.loads((NORTHSTAR / "inputs/inputs.json").read_text(encoding="utf-8"))["scope"]
    scope["execution"] = {"delivery": "simulated", "external_providers": external}
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(scope), encoding="utf-8")
    return path


def test_project_writes_a_stores_tabs_as_csv_or_to_its_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sheets = MemorySheets()
    monkeypatch.setattr(cli, "sheets_api", lambda scope, credentials: sheets)
    with mongo_backend() as backend:
        run_scenario(NORTHSTAR, backend=backend)
        store = ["--mongodb", os.environ[MONGODB_URI], "--database-prefix", backend.database_prefix]
        offline = scope_file(tmp_path, external=False)
        assert main(["project", str(offline), *store, "--csv", str(tmp_path / "csv")]) == 0
        assert "Projected 9 rows" in capsys.readouterr().out
        sheet = ["--spreadsheet", "synthetic-spreadsheet-0000000001"]
        assert main(["project", str(offline), *store, *sheet]) == 1
        assert "stopped   ExternalCallRefused" in capsys.readouterr().out
        assert sheets.calls == []
        online = scope_file(tmp_path, external=True)
        assert main(["project", str(online), *store, *sheet]) == 0
        assert "auggie: accounts, auggie: findings" in capsys.readouterr().out
    assert sheets.tabs["auggie: accounts"][1][:2] == ["acc-northstar", "qualified"]
    assert sheets.tabs["Notes"] == [["keep me"]]


def test_project_stops_on_a_store_that_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sheets = MemorySheets()
    monkeypatch.setattr(cli, "sheets_api", lambda scope, credentials: sheets)
    with mongo_backend() as backend:
        run_scenario(NORTHSTAR, backend=backend)
        mistyped = ["--mongodb", os.environ[MONGODB_URI], "--database-prefix", "typo_"]
        sheet = ["--spreadsheet", "synthetic-spreadsheet-0000000001"]
        online = scope_file(tmp_path, external=True)
        assert main(["project", str(online), *mistyped, *sheet]) == 1
        assert "stopped   RecordNotFound: no store" in capsys.readouterr().out
        assert "typo_auggie_demo_msp" not in backend._client.list_database_names()
    assert sheets.calls == []


def test_an_agent_written_sheet_is_verified_against_its_workbook(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workbook = tmp_path / "workbook.json"
    assert main(["run", str(NORTHSTAR), "--workbook", str(workbook)]) == 0
    assert "Workbook for an agent:" in capsys.readouterr().out
    reply = as_grid(load_workbook(workbook))
    readback = tmp_path / "readback.json"
    readback.write_text(json.dumps(reply), encoding="utf-8")
    assert main(["verify-sheet", str(workbook), str(readback)]) == 0
    assert "match     the sheet holds exactly the projection: 4 tabs" in capsys.readouterr().out

    reply["sheets"][0]["data"][0]["rowData"][1]["values"][1] = {
        "userEnteredValue": {"stringValue": "disqualified"}
    }
    readback.write_text(json.dumps(reply), encoding="utf-8")
    assert main(["verify-sheet", str(workbook), str(readback)]) == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out and "'auggie: accounts'!B2" in out
    assert main(["verify-sheet", str(workbook), str(workbook)]) == 1
    assert "not a spreadsheets.get grid reply" in capsys.readouterr().out
    assert main(["verify-sheet", str(workbook), str(tmp_path / "missing.json")]) == 1


def test_one_prospect_runs_from_research_to_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = NORTHSTAR
    answers = iter(["y", "Claims match the cited pages.", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "interactive", lambda: True)  # stands in for a person at a terminal
    with mongo_backend() as backend:
        store = ["--mongodb", os.environ[MONGODB_URI], "--database-prefix", backend.database_prefix]
        scope = str(files / "scope.json")
        research = [
            "research",
            scope,
            str(files / "prospect.json"),
            "--strategy-repo",
            str(Path(__file__).resolve().parent.parent),
            "--strategy-path",
            "examples/synthetic/northstar-msp/inputs/strategy/msp.toml",
            "--operator",
            "demo-operator",
            "--replay",
            str(files / "inputs" / "inputs.json"),
        ]
        assert main([*research, *store]) == 0
        out = capsys.readouterr().out
        draft = out.split("--draft ")[1].split()[0]
        assert draft.startswith("draft-northstar-run-")
        sign = "p = Path(sys.argv[1]); p.write_text(p.read_text().replace('Sam', 'Sam Lee'))"
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", _editor(tmp_path, sign))
        assert main(["revise", scope, "--draft", draft, "--author", "demo-seller", *store]) == 0
        assert "replacing 1c05fafeae99" in capsys.readouterr().out
        assert main(["review", scope, "--draft", draft, "--reviewer", "demo-reviewer", *store]) == 0
        out = capsys.readouterr().out
        assert "| Sam Lee" in out and "recorded  delivery approved" in out
        assert main(["deliver", scope, "--draft", draft, *store]) == 0
        assert main(["deliver", scope, "--draft", draft, *store]) == 0
        assert "already delivered" in capsys.readouterr().out
        outcome = ["outcome", scope, "--draft", draft, "--outcome", "meeting_booked"]
        assert main([*outcome, "--reporter", "demo-reviewer", *store]) == 0
        assert "outcome meeting_booked recorded" in capsys.readouterr().out
        typo = ["--mongodb", os.environ[MONGODB_URI], "--database-prefix", "typo_"]
        assert main(["deliver", scope, "--draft", draft, *typo]) == 1
        assert "no store for 'demo-msp'" in capsys.readouterr().out


def test_the_walkthrough_files_match_the_scenario_inputs() -> None:
    inputs = json.loads((NORTHSTAR / "inputs/inputs.json").read_text(encoding="utf-8"))
    assert (
        json.loads((NORTHSTAR / "prospect.json").read_text(encoding="utf-8")) == inputs["account"]
    )
    assert json.loads((NORTHSTAR / "scope.json").read_text(encoding="utf-8")) == inputs["scope"]


@pytest.mark.parametrize("command", ["review", "revise"])
def test_person_commands_refuse_input_that_does_not_come_from_a_terminal(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    scope = str(NORTHSTAR / "scope.json")
    unreachable = ["--mongodb", "mongodb://127.0.0.1:1"]
    person = "--reviewer" if command == "review" else "--author"
    assert main([command, scope, "--draft", "draft-x", person, "someone", *unreachable]) == 1
    assert "must be answered at a terminal" in capsys.readouterr().out


def _editor(tmp_path: Path, body: str) -> str:
    script = tmp_path / "editor.py"
    script.write_text(f"import sys\nfrom pathlib import Path\n{body}\n", encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


def test_the_editor_returns_what_the_person_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replace = "p = Path(sys.argv[1]); p.write_text(p.read_text().replace('Sam', 'Samantha'))"
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", _editor(tmp_path, replace))
    assert cli.edit_in_editor("Thanks,\nSam\n") == "Thanks,\nSamantha\n"


def test_an_editors_final_newline_is_not_added_to_a_draft_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = "p = Path(sys.argv[1]); p.write_text(p.read_text() + chr(10))"
    monkeypatch.setenv("VISUAL", _editor(tmp_path, append))
    assert cli.edit_in_editor("Thanks, Sam") == "Thanks, Sam"


def test_an_editor_that_fails_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISUAL", _editor(tmp_path, "sys.exit(3)"))
    with pytest.raises(OSError, match="status 3"):
        cli.edit_in_editor("Thanks, Sam\n")


def test_the_editor_keeps_the_drafts_exact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISUAL", _editor(tmp_path, "pass"))
    assert cli.edit_in_editor("Hi Jordan,\r\nThanks\r\n") == "Hi Jordan,\r\nThanks\r\n"
    crlf = "Path(sys.argv[1]).write_bytes(b'Hi Jordan,\\r\\nThanks')"
    monkeypatch.setenv("VISUAL", _editor(tmp_path, crlf))
    assert cli.edit_in_editor("Hi Jordan,\nThanks\n") == "Hi Jordan,\r\nThanks"


def test_the_editor_file_is_private_to_the_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mode = tmp_path / "mode"
    record = f"import os; Path({str(mode)!r}).write_text(oct(os.stat(sys.argv[1]).st_mode & 0o777))"
    monkeypatch.setenv("VISUAL", _editor(tmp_path, record))
    cli.edit_in_editor("Thanks, Sam\n")
    assert mode.read_text() == "0o600"
