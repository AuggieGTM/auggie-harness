"""Prospect lists: a seller's CSV of accounts, turned into prospect files (ADR 0027).

Reading a list is pure: each row becomes a prospect or a list of problems.
Comparing it with a folder and writing new files are separate steps, so the
seller sees a preview before anything is written. Prospects stay files; the
Store does not hold accounts.
"""

from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from auggie.accounts import Account
from auggie.contracts import CustomerScope, Strategy, ensure_in_scope

FIXED = (
    "name",
    "key",
    "first_name",
    "person_id",
    "email",
    "listing_service",
    "listing_board",
    "listing_url",
)
LISTING = ("listing_service", "listing_board", "listing_url")
MAX_ROWS = 1000
MAX_BYTES = 5_000_000

Status = Literal["new", "unchanged", "conflict", "refused"]


class ListRefused(ValueError):
    """The list as a whole cannot be read: nothing in it is used."""


@dataclass(frozen=True)
class Row:
    line: int
    """The row's line in the file; the header is line 1."""
    account: Account | None
    problems: tuple[str, ...] = ()
    status: Status = "refused"


def key_for(name: str) -> str:
    """A short handle from a company name: lowercase ASCII letters and digits, hyphenated."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    key = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return key[:64].rstrip("-")


def _problems(error: ValidationError) -> tuple[str, ...]:
    found = []
    for detail in error.errors():
        where = ".".join(str(part) for part in detail["loc"]) or "row"
        found.append(f"{where}: {detail['msg']}")
    return tuple(found)


def read_list(text: str, *, scope: CustomerScope, strategy: Strategy) -> list[Row]:
    """Each row of a CSV list as a prospect of ``scope``, or the problems that stop it being
    one. Columns are the fixed ones and the strategy's source kinds; any other column refuses
    the whole list."""
    ensure_in_scope(scope, strategy)
    if len(text.encode()) > MAX_BYTES:
        raise ListRefused(f"the list is larger than {MAX_BYTES:,} bytes")
    reader = csv.reader(io.StringIO(text.removeprefix("﻿")))
    try:
        header = [cell.strip() for cell in next(reader)]
    except StopIteration:
        raise ListRefused("the list is empty") from None
    except csv.Error as error:
        raise ListRefused(f"the list is not readable CSV: {error}") from None
    known = set(FIXED) | set(strategy.sources)
    unknown = [column for column in header if column not in known]
    if unknown:
        raise ListRefused(f"unknown columns: {', '.join(repr(c) for c in unknown)}")
    if len(set(header)) != len(header):
        raise ListRefused("a column is named twice")
    for column in ("name", "first_name"):
        if column not in header:
            raise ListRefused(f"the list has no {column!r} column")
    rows: list[Row] = []
    keys: dict[str, int] = {}
    try:
        for cells in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in cells):
                continue
            if len(rows) == MAX_ROWS:
                raise ListRefused(f"the list has more than {MAX_ROWS} rows")
            if len(cells) != len(header):
                rows.append(Row(line, None, (f"{len(cells)} cells for {len(header)} columns",)))
                continue
            values = {c: v.strip() for c, v in zip(header, cells, strict=True) if v.strip()}
            row = _row(line, values, scope, strategy)
            if row.account is not None:
                first = keys.setdefault(row.account.key, line)
                if first != line:
                    row = Row(line, None, (f"key {row.account.key!r} is used by line {first}",))
            rows.append(row)
    except csv.Error as error:
        raise ListRefused(f"the list is not readable CSV: {error}") from None
    return rows


def _row(line: int, values: dict[str, str], scope: CustomerScope, strategy: Strategy) -> Row:
    name = values.get("name", "")
    key = values.get("key") or key_for(name)
    if not key:
        return Row(line, None, ("no key can be made from the name; give one in 'key'",))
    given = [column for column in LISTING if column in values]
    if given and len(given) != len(LISTING):
        missing = ", ".join(column for column in LISTING if column not in values)
        return Row(line, None, (f"a careers listing needs all three listing columns: {missing}",))
    data: dict[str, Any] = {
        "account_id": f"acc-{key}",
        "customer_id": scope.customer_id,
        "key": key,
        "name": name,
        "sources": {kind: values[kind] for kind in strategy.sources if kind in values},
        "recipient": {
            "person_id": values.get("person_id") or f"per-{key}",
            "first_name": values.get("first_name", ""),
            "email": values.get("email"),
        },
    }
    if given:
        data["careers_listing"] = {
            "service": values["listing_service"],
            "board": values["listing_board"],
            "url": values["listing_url"],
        }
    try:
        return Row(line, Account.model_validate(data), status="new")
    except ValidationError as error:
        return Row(line, None, _problems(error))


def file_for(folder: Path, account: Account) -> Path:
    return folder / f"{account.key}.json"


def compare(rows: Sequence[Row], folder: Path) -> list[Row]:
    """Each readable row as new, unchanged (an identical file exists) or a conflict (a
    different file exists for the same account or file name)."""
    existing: dict[str, Path] = {}
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                found = Account.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError):
                continue
            existing.setdefault(found.account_id, path)
    compared = []
    for row in rows:
        if row.account is None:
            compared.append(row)
            continue
        path = file_for(folder, row.account)
        held = existing.get(row.account.account_id)
        if held is not None and held != path:
            compared.append(replace(row, status="conflict", problems=(f"{held.name} holds it",)))
        elif path.exists():
            same = _same(path, row.account)
            status: Status = "unchanged" if same else "conflict"
            problems = () if same else (f"{path.name} differs; it is left as it is",)
            compared.append(replace(row, status=status, problems=problems))
        else:
            compared.append(row)
    return compared


def _same(path: Path, account: Account) -> bool:
    try:
        return Account.model_validate_json(path.read_text(encoding="utf-8")) == account
    except (OSError, ValidationError):
        return False


def write_new(rows: Sequence[Row], folder: Path) -> list[Path]:
    """Write each new row's prospect file, never replacing a file. The folder and files are
    the seller's only: they hold recipients' names and addresses."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    written = []
    for row in rows:
        if row.status != "new" or row.account is None:
            continue
        path = file_for(folder, row.account)
        body = row.account.model_dump_json(indent=2, exclude_none=True) + "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
        written.append(path)
    return written


def describe(row: Row) -> str:
    """One preview line for a row."""
    account = row.account
    if account is None or row.status in ("refused", "conflict"):
        who = account.account_id if account else "-"
        return f"line {row.line:<5} {who:<28} {row.status:<9} {'; '.join(row.problems)}"
    count = len(account.sources)
    parts = [f"{count} source{'' if count == 1 else 's'}"]
    if account.careers_listing is not None:
        listing = account.careers_listing
        parts.append(f"listing {listing.service}:{listing.board}")
    parts.append(f"to {account.recipient.first_name}")
    return (
        f"line {row.line:<5} {account.account_id:<28} {row.status:<9} "
        f"{account.name}: {', '.join(parts)}"
    )
