"""Exporting a projection for an agent, and checking its grid read-back (ADR 0019)."""

from __future__ import annotations

import copy
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


def as_grid(workbook: Workbook) -> dict[str, Any]:
    """The spreadsheets.get grid reply for a sheet holding exactly ``workbook``."""
    sheets = []
    for title, rows in workbook.items():
        row_data = [
            {"values": [{"userEnteredValue": {"stringValue": c}} if c else {} for c in row]}
            for row in rows
        ]
        sheets.append(
            {
                "properties": {
                    "title": title,
                    "gridProperties": {"rowCount": len(rows), "columnCount": len(rows[0])},
                },
                "data": [{"rowData": row_data}],
            }
        )
    sheets.append({"properties": {"title": "Notes"}, "data": [{"rowData": []}]})
    return {"revisionId": "synthetic", "sheets": sheets}


def differences(workbook: Workbook, reply: dict[str, Any], tmp_path: Path) -> list[str]:
    path = tmp_path / "readback.json"
    path.write_text(json.dumps(reply), encoding="utf-8")
    return compare(workbook, load_readback(path))


def test_a_workbook_file_round_trips_exactly(workbook: Workbook, tmp_path: Path) -> None:
    path = tmp_path / "workbook.json"
    assert dump_workbook(workbook, path).startswith("sha256:")
    assert load_workbook(path) == workbook
    assert list(load_workbook(path)) == list(TABS)


def test_connector_rows_store_every_value_as_literal_text(
    workbook: Workbook, tmp_path: Path
) -> None:
    path = tmp_path / "workbook.json"
    dump_workbook(workbook, path)
    tabs = json.loads(path.read_text(encoding="utf-8"))["tabs"]
    for tab in tabs:
        for row, written in zip(tab["rows"], tab["connector_rows"], strict=True):
            assert written == [f"'{cell}" if cell else "" for cell in row]


def test_a_faithful_read_back_matches(workbook: Workbook, tmp_path: Path) -> None:
    assert differences(workbook, as_grid(workbook), tmp_path) == []


def test_a_changed_cell_is_reported_by_its_address(workbook: Workbook, tmp_path: Path) -> None:
    reply = as_grid(workbook)
    reply["sheets"][0]["data"][0]["rowData"][1]["values"][1] = {
        "userEnteredValue": {"stringValue": "disqualified"}
    }
    assert differences(workbook, reply, tmp_path) == [
        "'auggie: accounts'!B2: expected 'qualified', found 'disqualified'"
    ]


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ({"numberValue": 1}, "a number"),
        ({"formulaValue": "=1"}, "a formula"),
        ({"boolValue": True}, "a boolean"),
        ({"errorValue": {"type": "REF"}}, "an error"),
    ],
)
def test_a_cell_that_is_not_literal_text_never_matches(
    value: dict[str, Any], kind: str, workbook: Workbook, tmp_path: Path
) -> None:
    reply = as_grid(workbook)
    reply["sheets"][3]["data"][0]["rowData"][1]["values"][3] = {"userEnteredValue": value}
    assert differences(workbook, reply, tmp_path) == [
        f"'auggie: drafts'!D2: holds {kind}, not literal text"
    ]


def test_a_tab_larger_than_the_projection_does_not_match(
    workbook: Workbook, tmp_path: Path
) -> None:
    reply = as_grid(workbook)
    accounts = reply["sheets"][0]
    accounts["properties"]["gridProperties"] = {"rowCount": 1000, "columnCount": 26}
    accounts["data"][0]["rowData"].append(
        {"values": [{"userEnteredValue": {"stringValue": "stale"}}]}
    )
    accounts["data"][0]["rowData"][0]["values"] += [{}] * 17 + [
        {"userEnteredValue": {"stringValue": "old"}}
    ]
    found = differences(workbook, reply, tmp_path)
    assert "'auggie: accounts' is 1000x26 cells, expected 2x8: resize it to the projection" in found
    assert "'auggie: accounts'!A3: expected '', found 'stale'" in found
    assert "'auggie: accounts'!Z1: expected '', found 'old'" in found


def test_missing_tabs_and_rows_are_reported(workbook: Workbook, tmp_path: Path) -> None:
    reply = as_grid(workbook)
    del reply["sheets"][3]
    reply["sheets"][1]["data"][0]["rowData"] = reply["sheets"][1]["data"][0]["rowData"][:2]
    found = differences(workbook, reply, tmp_path)
    assert "'auggie: drafts' is missing from the read-back" in found
    assert "'auggie: findings'!A3: expected 'acc-northstar', found ''" in found


@pytest.mark.parametrize("start", [{"startRow": 6}, {"startColumn": 1}])
def test_a_read_back_that_does_not_start_at_a1_is_refused(
    start: dict[str, int], workbook: Workbook, tmp_path: Path
) -> None:
    reply = as_grid(workbook)
    reply["sheets"][0]["data"][0].update(start)
    with pytest.raises(ValueError, match="must start at A1"):
        differences(workbook, reply, tmp_path)


def test_a_tab_that_appears_twice_is_refused(workbook: Workbook, tmp_path: Path) -> None:
    reply = as_grid(workbook)
    reply["sheets"].append(copy.deepcopy(reply["sheets"][0]))
    with pytest.raises(ValueError, match="appears twice"):
        differences(workbook, reply, tmp_path)


def test_a_values_only_read_back_is_refused(workbook: Workbook, tmp_path: Path) -> None:
    reply = {"valueRanges": [{"range": "'auggie: accounts'!A1:Z1000", "values": [["x"]]}]}
    with pytest.raises(ValueError, match="values-only read cannot show"):
        differences(workbook, reply, tmp_path)


@pytest.mark.parametrize(
    "tabs",
    [[], [{"title": "auggie: accounts", "rows": [["a"]]}], "duplicate"],
    ids=["empty", "partial", "duplicate"],
)
def test_the_expected_workbook_must_be_a_complete_projection(
    tabs: Any, workbook: Workbook, tmp_path: Path
) -> None:
    path = tmp_path / "workbook.json"
    if tabs == "duplicate":
        dump_workbook(workbook, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tabs"].append(data["tabs"][0])
    else:
        data = {"format": "auggie-workbook", "version": 1, "tabs": tabs}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=r"exactly the tabs|appears twice"):
        load_workbook(path)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (
            lambda s: s["properties"]["gridProperties"].update(rowCount=None),
            "positive whole number",
        ),
        (
            lambda s: s["properties"]["gridProperties"].update(columnCount=True),
            "positive whole number",
        ),
        (lambda s: s["properties"]["gridProperties"].update(rowCount=0), "positive whole number"),
        (lambda s: s["data"][0].update(rowData=1), "rowData must be a list"),
        (
            lambda s: s["data"][0]["rowData"][1]["values"].__setitem__(
                3, {"userEnteredValue": {"stringValue": 1}}
            ),
            "stringValue must be a string",
        ),
    ],
    ids=["null-rows", "boolean-columns", "zero-rows", "rowdata-not-list", "number-as-string"],
)
def test_a_malformed_grid_reply_is_refused_not_coerced(
    damage: Any, message: str, workbook: Workbook, tmp_path: Path
) -> None:
    reply = as_grid(workbook)
    damage(reply["sheets"][3])
    with pytest.raises(ValueError, match=message):
        differences(workbook, reply, tmp_path)
