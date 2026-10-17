"""Hand a projection to an operator's agent, and check what it wrote back (ADR 0019).

The harness exports the exact workbook as a file. The agent writes it to the
sheet with its own connector, as raw values, then saves what it reads back:
either in the same file shape, or as the Sheets API's ``values.batchGet``
reply, unedited. ``compare`` then decides, in code, whether the sheet holds
exactly the projection.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from auggie.contracts import sha256_digest
from auggie.projections import TABS, Workbook

FORMAT = "auggie-workbook"
VERSION = 1
MAX_DIFFERENCES = 20

Grid = Sequence[Sequence[str]]


def dump_workbook(workbook: Workbook, path: Path) -> str:
    """Write ``workbook`` to ``path`` and return the digest of the file's bytes."""
    data = {
        "format": FORMAT,
        "version": VERSION,
        "tabs": [
            {"title": title, "rows": [list(row) for row in rows]}
            for title, rows in workbook.items()
        ],
    }
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
    Path(stream.name).replace(path)
    return sha256_digest(text)


def _rows(value: object, where: str) -> list[list[str]]:
    if not isinstance(value, list) or not all(isinstance(row, list) for row in value):
        raise ValueError(f"{where}: rows must be a list of lists")
    return [[cell if isinstance(cell, str) else json.dumps(cell) for cell in row] for row in value]


def load_workbook(path: Path) -> Workbook:
    """Read a workbook file written by ``dump_workbook``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != VERSION:
        raise ValueError(f"{path} is not an {FORMAT} file, version {VERSION}")
    tabs = _tabs(data.get("tabs"), str(path))
    return {title: tuple(tuple(row) for row in rows) for title, rows in tabs.items()}


def _tabs(value: object, where: str) -> dict[str, list[list[str]]]:
    if not isinstance(value, list):
        raise ValueError(f"{where}: tabs must be a list")
    tabs: dict[str, list[list[str]]] = {}
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
            raise ValueError(f"{where}: each tab needs a title and rows")
        title = entry["title"]
        if title not in TABS:
            raise ValueError(f"{where}: {title!r} is not a managed tab")
        tabs[title] = _rows(entry.get("rows"), f"{where} {title!r}")
    return tabs


_RANGE = re.compile(r"^'((?:[^']|'')*)'(?:!.*)?$|^([^'!]+)(?:!.*)?$")


def _range_title(range_: str) -> str:
    match = _RANGE.match(range_)
    if match is None:
        raise ValueError(f"cannot read a tab name from the range {range_!r}")
    quoted, plain = match.groups()
    return quoted.replace("''", "'") if quoted is not None else plain


def load_readback(path: Path) -> dict[str, list[list[str]]]:
    """Read what the agent saved: a workbook file, or a raw ``values.batchGet`` reply."""
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "valueRanges" in data:
        ranges = data["valueRanges"]
        if not isinstance(ranges, list):
            raise ValueError(f"{path}: valueRanges must be a list")
        tabs: dict[str, list[list[str]]] = {}
        for entry in ranges:
            if not isinstance(entry, dict) or not isinstance(entry.get("range"), str):
                raise ValueError(f"{path}: each value range needs a range")
            title = _range_title(entry["range"])
            tabs[title] = _rows(entry.get("values", []), f"{path} {title!r}")
        return tabs
    if isinstance(data, dict) and isinstance(data.get("tabs"), list):
        return _tabs(data["tabs"], str(path))
    raise ValueError(f"{path} is neither a workbook file nor a values.batchGet reply")


def _trimmed(grid: Grid) -> list[list[str]]:
    """Rows without trailing empty cells or trailing empty rows, as Sheets returns them."""
    rows = [list(row) for row in grid]
    for row in rows:
        while row and row[-1] == "":
            row.pop()
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _cell(row: int, column: int) -> str:
    letters = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row + 1}"


def compare(expected: Workbook, actual: Mapping[str, Grid]) -> list[str]:
    """Differences between the projection and what the sheet holds. Empty means a match."""
    differences: list[str] = []
    for title, rows in expected.items():
        if title not in actual:
            differences.append(f"{title!r} is missing from the sheet")
            continue
        want, got = _trimmed(rows), _trimmed(actual[title])
        if len(want) != len(got):
            differences.append(f"{title!r} has {len(got)} rows, expected {len(want)}")
        for r, (want_row, got_row) in enumerate(zip(want, got, strict=False)):
            for c in range(max(len(want_row), len(got_row))):
                w = want_row[c] if c < len(want_row) else ""
                g = got_row[c] if c < len(got_row) else ""
                if w != g:
                    differences.append(f"{title!r}!{_cell(r, c)}: expected {w!r}, found {g!r}")
        if len(differences) >= MAX_DIFFERENCES:
            break
    return differences[:MAX_DIFFERENCES]
