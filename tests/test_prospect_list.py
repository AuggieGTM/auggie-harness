"""Importing a list of accounts from CSV as prospect files, after a preview (ADR 0027).
Synthetic lists only."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from auggie.accounts import Account
from auggie.cli import main
from auggie.contracts import CustomerScope
from auggie.prospect_list import (
    MAX_ROWS,
    FolderRefused,
    ListRefused,
    ProspectFolder,
    Row,
    compare,
    key_for,
    read_list,
    write_new,
)
from auggie.strategy import load_strategy
from tests.conftest import EXAMPLES

KESTREL = EXAMPLES / "kestrel-security"
SCOPE = CustomerScope.model_validate_json((KESTREL / "scope.json").read_text(encoding="utf-8"))
STRATEGY = load_strategy(
    KESTREL / "inputs", "strategy/security.toml", commit="0" * 40, scope=SCOPE
).strategy
LIST = (KESTREL / "prospects.csv").read_text(encoding="utf-8")
HEADER = "name,first_name,security"


def rows(text: str) -> list[Row]:
    return read_list(text, scope=SCOPE, strategy=STRATEGY)


def test_the_synthetic_list_becomes_three_prospects() -> None:
    read = rows(LIST)
    assert [r.status for r in read] == ["new"] * 3
    assert [r.account.account_id for r in read if r.account] == [
        "acc-fernhollow",
        "acc-brightquarry",
        "acc-saltmarsh",
    ]
    by_hand = Account.model_validate_json((KESTREL / "prospect.json").read_text(encoding="utf-8"))
    assert read[0].account == by_hand
    saltmarsh = read[2].account
    assert saltmarsh is not None
    listing = saltmarsh.careers_listing
    assert listing is not None and (listing.service, listing.board) == ("ashby", "saltmarsh")


def test_a_row_needs_little_and_the_rest_is_derived() -> None:
    (row,) = rows(
        f"{HEADER}\nFernhollow Logistics Inc.,Casey,https://fernhollow.example/security\n"
    )
    account = row.account
    assert account is not None
    assert (account.key, account.account_id) == (
        "fernhollow-logistics-inc",
        "acc-fernhollow-logistics-inc",
    )
    assert account.recipient.person_id == "per-fernhollow-logistics-inc"
    assert account.recipient.email is None and account.careers_listing is None
    assert account.customer_id == "demo-security"


@pytest.mark.parametrize(
    ("name", "key"),
    [("Café Société", "cafe-societe"), ("  A&B -- Co  ", "a-b-co"), ("東京", "")],
)
def test_keys_come_from_names(name: str, key: str) -> None:
    assert key_for(name) == key


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("", "the list is empty"),
        ("name,first_name,securty\n", "unknown columns: 'securty'"),
        ("name,name,first_name\n", "named twice"),
        ("name,security\n", "no 'first_name' column"),
        (
            HEADER + "\n" + "A,B,https://a.example/x\n" * (MAX_ROWS + 1),
            f"more than {MAX_ROWS} rows",
        ),
    ],
    ids=["empty", "unknown-column", "repeated-column", "no-first-name", "too-many-rows"],
)
def test_a_list_that_cannot_be_read_is_refused_whole(text: str, problem: str) -> None:
    with pytest.raises(ListRefused, match=problem):
        rows(text)


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("Acme,Casey,not-a-url", "sources.security"),
        ("Acme,,https://acme.example/security", "recipient.first_name"),
        ("東京,Casey,https://acme.example/security", "no key can be made from the name"),
        ("Acme,Casey", "2 cells for 3 columns"),
    ],
    ids=["bad-url", "no-first-name", "no-key", "short-row"],
)
def test_a_row_with_problems_is_shown_and_not_written(line: str, problem: str) -> None:
    (row,) = rows(f"{HEADER}\n{line}\n")
    assert row.status == "refused" and row.account is None
    assert any(problem in p for p in row.problems)


def test_a_listing_needs_all_three_columns_and_a_key_is_used_once() -> None:
    header = "name,first_name,listing_service,listing_board"
    (partial,) = rows(f"{header}\nAcme,Casey,lever,acme\n")
    assert partial.problems == ("a careers listing needs all three listing columns: listing_url",)
    first, second = rows(f"{HEADER}\nAcme,Casey,\nAcme,Robin,\n")
    assert first.status == "new"
    assert second.problems == ("key 'acme' is used by line 2",)


def test_a_spreadsheets_byte_order_mark_and_blank_lines_are_ignored() -> None:
    read = rows("﻿" + HEADER + "\n\nAcme,Casey,\n,,\n")
    assert [r.account.key for r in read if r.account] == ["acme"]


def imported(text: str, path: Path) -> list[str]:
    with ProspectFolder.for_writing(path) as folder:
        return write_new(compare(rows(text), folder), folder)


def compared(text: str, path: Path) -> list[Row]:
    with ProspectFolder.open(path) as folder:
        return compare(rows(text), folder)


def test_existing_files_are_never_replaced(tmp_path: Path) -> None:
    folder = tmp_path / "prospects"
    assert compared(LIST, folder)[0].status == "new" and not folder.exists()
    assert imported(LIST, folder) == ["fernhollow.json", "brightquarry.json", "saltmarsh.json"]
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in folder.iterdir())
    # The same list again changes nothing.
    assert [r.status for r in compared(LIST, folder)] == ["unchanged"] * 3
    assert imported(LIST, folder) == []
    # A changed row, or the same account under another file name, is a conflict.
    changed = LIST.replace("Casey,per-casey", "Kasey,per-casey")
    (folder / "elsewhere.json").write_text((folder / "saltmarsh.json").read_text())
    (folder / "saltmarsh.json").unlink()
    statuses = [(r.status, r.problems) for r in compared(changed, folder)]
    assert statuses[0] == ("conflict", ("fernhollow.json differs; it is left as it is",))
    assert statuses[2] == ("conflict", ("elsewhere.json holds it",))
    assert imported(changed, folder) == []
    assert "Casey" in (folder / "fernhollow.json").read_text()


def test_a_folder_swapped_after_the_preview_is_not_followed(tmp_path: Path) -> None:
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    outside.mkdir()
    with ProspectFolder.for_writing(approved) as folder:
        previewed = compare(rows(LIST), folder)
        approved.rename(tmp_path / "original")
        approved.symlink_to(outside, target_is_directory=True)
        write_new(previewed, folder)
    assert list(outside.iterdir()) == []
    assert len(list((tmp_path / "original").iterdir())) == 3
    # The swapped path itself is refused, since it is a symlink now.
    with pytest.raises(FolderRefused, match="not a folder the harness may use"):
        ProspectFolder.for_writing(approved)


def test_a_folder_others_can_read_is_refused_for_writing(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(FolderRefused, match="can be read by others"):
        imported(LIST, shared)
    assert list(shared.iterdir()) == []


@pytest.mark.parametrize(
    "text",
    ['name,first_name\nAcme,"Casey\nOther,Robin\n', 'name,first_name\nAcme,"Casey"x\n'],
    ids=["unterminated-quote", "junk-after-quote"],
)
def test_malformed_csv_is_refused_whole_rather_than_merged(text: str) -> None:
    with pytest.raises(ListRefused, match="not readable CSV"):
        rows(text)


def test_the_import_command_previews_then_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "prospects"
    argv = [
        "import",
        str(KESTREL / "scope.json"),
        str(KESTREL / "prospects.csv"),
        "--prospects",
        str(folder),
        "--strategy-repo",
        str(Path(__file__).resolve().parent.parent),
        "--strategy-path",
        "examples/synthetic/kestrel-security/inputs/strategy/security.toml",
    ]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "acc-fernhollow" in out and "listing lever:fernhollow, to Casey" in out
    assert "3 new, 0 unchanged, 0 conflict, 0 refused" in out
    assert "Nothing written" in out and not folder.exists()
    assert main([*argv, "--write"]) == 0
    assert "Wrote 3 prospect files" in capsys.readouterr().out
    saved = json.loads((folder / "brightquarry.json").read_text())
    assert saved["recipient"]["email"] == "morgan.achebe@brightquarry.example"
    bad = tmp_path / "bad.csv"
    bad.write_text("name,first_name,securty\nAcme,Casey,x\n", encoding="utf-8")
    assert main([*argv[:2], str(bad), *argv[3:]]) == 1
    assert "unknown columns: 'securty'" in capsys.readouterr().out
