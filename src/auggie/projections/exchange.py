"""Hand a projection to an operator's agent, and check what it wrote (ADR 0019).

The harness exports the exact workbook as a file. Each tab carries ``rows``,
the values, and ``connector_rows``: the same values with a leading apostrophe,
which Google's connectors need so that a value is stored as literal text and
never parsed as a formula, number or date.

The agent resizes each managed tab to the projection's exact size, which
removes anything outside it, clears it, writes ``connector_rows``, and saves
the sheet's grid read-back (``spreadsheets.get`` with ``userEnteredValue``)
unedited. ``compare`` then checks, in code, that each tab is exactly the
projection's size, starts at A1, and holds every value as literal text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from auggie.contracts import sha256_digest
from auggie.projections import TABS, Workbook

FORMAT = "auggie-workbook"
VERSION = 1
MAX_DIFFERENCES = 20

VALUE_KINDS = {
    "stringValue": "text",
    "numberValue": "a number",
    "boolValue": "a boolean",
    "formulaValue": "a formula",
    "errorValue": "an error",
}


def as_text(cell: str) -> str:
    """A cell as a connector must send it to be stored as literal text."""
    return "'" + cell if cell else ""


def dump_workbook(workbook: Workbook, path: Path) -> str:
    """Write ``workbook`` to ``path`` and return the digest of the file's bytes."""
    data = {
        "format": FORMAT,
        "version": VERSION,
        "tabs": [
            {
                "title": title,
                "rows": [list(row) for row in rows],
                "connector_rows": [[as_text(cell) for cell in row] for row in rows],
            }
            for title, rows in workbook.items()
        ],
    }
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
    Path(stream.name).replace(path)
    return sha256_digest(text)


def load_workbook(path: Path) -> Workbook:
    """Read a complete projection written by ``dump_workbook``: the four managed tabs, once each."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != VERSION:
        raise ValueError(f"{path} is not an {FORMAT} file, version {VERSION}")
    entries = data.get("tabs")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: tabs must be a list")
    workbook: dict[str, tuple[tuple[str, ...], ...]] = {}
    for entry in entries:
        title = entry.get("title") if isinstance(entry, dict) else None
        if title not in TABS or title in workbook:
            raise ValueError(f"{path}: {title!r} is not a managed tab, or appears twice")
        rows = entry.get("rows")
        if (
            not isinstance(rows, list)
            or not rows
            or not all(isinstance(row, list) for row in rows)
            or not all(isinstance(cell, str) for row in rows for cell in row)
            or len({len(row) for row in rows}) != 1
            or not rows[0]
        ):
            raise ValueError(
                f"{path} {title!r}: rows must be a header and rows of text, all as wide"
            )
        workbook[title] = tuple(tuple(row) for row in rows)
    if list(workbook) != list(TABS):
        raise ValueError(f"{path}: a projection has exactly the tabs {list(TABS)}, in that order")
    return workbook


@dataclass(frozen=True)
class TabReadBack:
    """One managed tab as the grid read-back shows it."""

    row_count: int
    column_count: int
    cells: tuple[tuple[tuple[str, str], ...], ...]
    """Rows of (kind, value): kind is ``"text"``, ``"empty"``, or what else the cell holds."""


def _cell_value(cell: object, where: str) -> tuple[str, str]:
    if not isinstance(cell, dict):
        raise ValueError(f"{where}: a cell must be an object")
    entered = cell.get("userEnteredValue")
    if entered is None:
        return ("empty", "")
    if not isinstance(entered, dict) or len(entered) != 1:
        raise ValueError(f"{where}: userEnteredValue must hold one value")
    [(key, value)] = entered.items()
    kind = VALUE_KINDS.get(key)
    if kind is None:
        raise ValueError(f"{where}: unknown value kind {key!r}")
    if kind == "text" and not isinstance(value, str):
        raise ValueError(f"{where}: a stringValue must be a string")
    return (kind, value if isinstance(value, str) else json.dumps(value))


def _size(grid: Mapping[str, object], key: str, where: str) -> int:
    value = grid.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{where}: gridProperties.{key} must be a positive whole number")
    return value


def load_readback(path: Path) -> dict[str, TabReadBack]:
    """Read a ``spreadsheets.get`` grid reply. Each managed tab must appear once, read whole."""
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    sheets = data.get("sheets") if isinstance(data, dict) else None
    if not isinstance(sheets, list):
        raise ValueError(
            f"{path} is not a spreadsheets.get grid reply (a values-only read cannot show "
            "whether cells are literal text or where they start)"
        )
    tabs: dict[str, TabReadBack] = {}
    for sheet in sheets:
        properties = sheet.get("properties", {}) if isinstance(sheet, dict) else {}
        title = properties.get("title") if isinstance(properties, dict) else None
        if title not in TABS:
            continue
        if title in tabs:
            raise ValueError(f"{path}: {title!r} appears twice")
        grid = properties.get("gridProperties", {})
        data_ranges = sheet.get("data")
        if not isinstance(grid, dict) or not isinstance(data_ranges, list) or len(data_ranges) != 1:
            raise ValueError(f"{path} {title!r}: needs gridProperties and one data range")
        [block] = data_ranges
        if not isinstance(block, dict) or block.get("startRow", 0) or block.get("startColumn", 0):
            raise ValueError(f"{path} {title!r}: the read-back must start at A1")
        row_data = block.get("rowData", [])
        if not isinstance(row_data, list):
            raise ValueError(f"{path} {title!r}: rowData must be a list")
        rows: list[tuple[tuple[str, str], ...]] = []
        for r, row in enumerate(row_data):
            values = (row.get("values") or []) if isinstance(row, dict) else None
            if not isinstance(values, list):
                raise ValueError(f"{path} {title!r} row {r + 1}: values must be a list")
            rows.append(
                tuple(
                    _cell_value(cell, f"{path} {title!r} {_cell(r, c)}")
                    for c, cell in enumerate(values)
                )
            )
        tabs[title] = TabReadBack(
            row_count=_size(grid, "rowCount", f"{path} {title!r}"),
            column_count=_size(grid, "columnCount", f"{path} {title!r}"),
            cells=tuple(rows),
        )
    return tabs


def _cell(row: int, column: int) -> str:
    letters = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row + 1}"


def compare(expected: Workbook, actual: Mapping[str, TabReadBack]) -> list[str]:
    """Differences between the projection and the sheet. Empty means an exact match.

    Missing tabs and wrong sizes come first, so they are never crowded out by
    the cell differences that follow from them.
    """
    structure: list[str] = []
    cells: list[str] = []
    for title, rows in expected.items():
        tab = actual.get(title)
        if tab is None:
            structure.append(f"{title!r} is missing from the read-back")
            continue
        size = (len(rows), len(rows[0]))
        if (tab.row_count, tab.column_count) != size:
            structure.append(
                f"{title!r} is {tab.row_count}x{tab.column_count} cells, expected "
                f"{size[0]}x{size[1]}: resize it to the projection"
            )
        for r in range(max(len(rows), len(tab.cells))):
            want_row = rows[r] if r < len(rows) else ()
            got_row = tab.cells[r] if r < len(tab.cells) else ()
            for c in range(max(len(want_row), len(got_row))):
                want = want_row[c] if c < len(want_row) else ""
                kind, got = got_row[c] if c < len(got_row) else ("empty", "")
                where = f"{title!r}!{_cell(r, c)}"
                if kind not in ("text", "empty"):
                    cells.append(f"{where}: holds {kind}, not literal text")
                elif got != want:
                    cells.append(f"{where}: expected {want!r}, found {got!r}")
    return (structure + cells)[:MAX_DIFFERENCES]
