"""Export workbook tabs as UTF-8 CSV with spreadsheet formulas disabled."""

from __future__ import annotations

import csv
from pathlib import Path
from tempfile import NamedTemporaryFile

from auggie.projections import TABS, Workbook


def write_csv(workbook: Workbook, directory: Path) -> list[Path]:
    """Write only the managed tabs, in order, without following existing file links."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for tab in TABS:
        path = directory / f"{tab.removeprefix('auggie: ')}.csv"
        temporary: Path | None = None
        try:
            # Replace rather than truncate so existing links cannot modify an outside file.
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=directory, delete=False
            ) as stream:
                temporary = Path(stream.name)
                writer = csv.writer(stream)
                writer.writerows(
                    tuple(
                        "'" + cell if cell.startswith(("=", "+", "-", "@", "\t", "\r")) else cell
                        for cell in row
                    )
                    for row in workbook[tab]
                )
            temporary.replace(path)
            paths.append(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return paths
