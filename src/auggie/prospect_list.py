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
    reader = csv.reader(io.StringIO(text.removeprefix("\ufeff")), strict=True)
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


def file_name(account: Account) -> str:
    return f"{account.key}.json"


class FolderRefused(ValueError):
    """The prospect folder cannot be used as it is."""


class ProspectFolder:
    """One prospect folder, pinned by its descriptor, so the preview and the files written are
    in the same directory even if its path changes in between (ADR 0027).

    ``open`` never creates the folder; ``for_writing`` creates it, readable by its owner only,
    and refuses an existing folder anyone else can read. Neither follows a symlink at the
    folder or at a file.
    """

    def __init__(self, path: Path, descriptor: int | None) -> None:
        self.path = path
        self._descriptor = descriptor

    @classmethod
    def open(cls, path: Path) -> ProspectFolder:
        """The folder for a preview: absent is allowed, and holds nothing."""
        if not path.exists() and not path.is_symlink():
            return cls(path, None)
        return cls(path, cls._pin(path))

    @classmethod
    def for_writing(cls, path: Path) -> ProspectFolder:
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700, parents=True)
        descriptor = cls._pin(path)
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(descriptor)
            raise FolderRefused(
                f"{path} can be read by others; keep prospects in a folder only you can read "
                "(chmod 700)"
            )
        return cls(path, descriptor)

    @staticmethod
    def _pin(path: Path) -> int:
        try:
            return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:
            raise FolderRefused(f"{path} is not a folder the harness may use: {error}") from None

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None

    def __enter__(self) -> ProspectFolder:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def accounts(self) -> dict[str, tuple[str, Account | None]]:
        """Each file name in the folder, and the prospect it holds if it is one."""
        if self._descriptor is None:
            return {}
        found: dict[str, tuple[str, Account | None]] = {}
        for name in sorted(os.listdir(self._descriptor)):
            if name.endswith(".json"):
                found[name] = (name, self._read(name))
        return found

    def _read(self, name: str) -> Account | None:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._descriptor)
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                return Account.model_validate_json(handle.read())
        except (OSError, ValidationError, UnicodeDecodeError):
            return None

    def write(self, name: str, body: str) -> None:
        """Create ``name`` in the folder, readable by its owner only; never replace a file."""
        if self._descriptor is None:
            raise FolderRefused(f"{self.path} is not open for writing")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(name, flags, 0o600, dir_fd=self._descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)


def compare(rows: Sequence[Row], folder: ProspectFolder) -> list[Row]:
    """Each readable row as new, unchanged (an identical file exists) or a conflict (a
    different file exists for the same account or file name)."""
    files = folder.accounts()
    by_account: dict[str, str] = {}
    for name, (_, account) in files.items():
        if account is not None:
            by_account.setdefault(account.account_id, name)
    compared = []
    for row in rows:
        if row.account is None:
            compared.append(row)
            continue
        name = file_name(row.account)
        held = by_account.get(row.account.account_id)
        if held is not None and held != name:
            compared.append(replace(row, status="conflict", problems=(f"{held} holds it",)))
        elif name in files:
            same = files[name][1] == row.account
            status: Status = "unchanged" if same else "conflict"
            problems = () if same else (f"{name} differs; it is left as it is",)
            compared.append(replace(row, status=status, problems=problems))
        else:
            compared.append(row)
    return compared


def write_new(rows: Sequence[Row], folder: ProspectFolder) -> list[str]:
    """Write each new row's prospect file into the pinned folder, never replacing a file. The
    files are the seller's only: they hold recipients' names and addresses."""
    written = []
    for row in rows:
        if row.status != "new" or row.account is None:
            continue
        name = file_name(row.account)
        folder.write(name, row.account.model_dump_json(indent=2, exclude_none=True) + "\n")
        written.append(name)
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
