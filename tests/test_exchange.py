"""Exporting a projection for an agent, and checking what it read back (ADR 0019)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auggie.projections import TABS, Workbook, project
from auggie.projections.exchange import compare, dump_workbook, load_readback, load_workbook
from auggie.synthetic import run_scenario
from tests.conftest import NORTHSTAR


@pytest.fixture(scope="module")
def workbook() -> Workbook:
    return project(run_scenario(NORTHSTAR).store)


def as_batch_get(workbook: Workbook) -> dict[str, Any]:
    """What Sheets' values.batchGet returns for the managed tabs: trailing empties dropped."""
    ranges = []
    for title, rows in workbook.items():
        values = [list(row) for row in rows]
        for row in values:
            while row and row[-1] == "":
                row.pop()
        quoted = "'" + title.replace("'", "''") + "'"
        ranges.append({"range": f"{quoted}!A1:Z1000", "majorDimension": "ROWS", "values": values})
    return {"spreadsheetId": "synthetic-spreadsheet-0000000001", "valueRanges": ranges}


def test_a_workbook_file_round_trips_exactly(workbook: Workbook, tmp_path: Path) -> None:
    path = tmp_path / "workbook.json"
    digest = dump_workbook(workbook, path)
    assert digest.startswith("sha256:")
    assert load_workbook(path) == workbook
    assert list(load_workbook(path)) == list(TABS)


def test_a_faithful_read_back_matches_in_either_shape(workbook: Workbook, tmp_path: Path) -> None:
    raw = tmp_path / "batch-get.json"
    raw.write_text(json.dumps(as_batch_get(workbook)), encoding="utf-8")
    assert compare(workbook, load_readback(raw)) == []
    same = tmp_path / "workbook.json"
    dump_workbook(workbook, same)
    assert compare(workbook, load_readback(same)) == []


def test_a_changed_cell_is_reported_by_its_address(workbook: Workbook, tmp_path: Path) -> None:
    reply = as_batch_get(workbook)
    reply["valueRanges"][0]["values"][1][1] = "disqualified"
    path = tmp_path / "batch-get.json"
    path.write_text(json.dumps(reply), encoding="utf-8")
    assert compare(workbook, load_readback(path)) == [
        "'auggie: accounts'!B2: expected 'qualified', found 'disqualified'"
    ]


def test_missing_tabs_and_rows_are_reported(workbook: Workbook, tmp_path: Path) -> None:
    reply = as_batch_get(workbook)
    reply["valueRanges"] = reply["valueRanges"][:3]
    reply["valueRanges"][1]["values"] = reply["valueRanges"][1]["values"][:2]
    path = tmp_path / "batch-get.json"
    path.write_text(json.dumps(reply), encoding="utf-8")
    differences = compare(workbook, load_readback(path))
    assert "'auggie: drafts' is missing from the sheet" in differences
    assert "'auggie: findings' has 2 rows, expected 5" in differences


def test_reordered_rows_do_not_match(workbook: Workbook, tmp_path: Path) -> None:
    reply = as_batch_get(workbook)
    findings = reply["valueRanges"][1]["values"]
    findings[1], findings[2] = findings[2], findings[1]
    path = tmp_path / "batch-get.json"
    path.write_text(json.dumps(reply), encoding="utf-8")
    assert compare(workbook, load_readback(path)) != []


def test_unmanaged_tabs_and_malformed_files_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"tabs": [{"title": "Notes", "rows": [["x"]]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a managed tab"):
        load_readback(path)
    path.write_text(json.dumps({"valueRanges": [{"values": [["x"]]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="needs a range"):
        load_readback(path)
    path.write_text(json.dumps({"format": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not an auggie-workbook file"):
        load_workbook(path)
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(ValueError, match="neither a workbook file"):
        load_readback(path)
