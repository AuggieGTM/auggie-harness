"""The Sheets writer, against an in-memory API and a fake Google over HTTPS (ADR 0018)."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from requests.adapters import BaseAdapter

from auggie.contracts import CustomerScope
from auggie.credentials import CredentialError, CredentialFiles
from auggie.external import ExternalCallRefused
from auggie.projections.sheets import (
    GoogleSheetsApi,
    SheetsError,
    service_account_session,
    write_sheet,
)
from tests.conftest import Scenario, variant

SPREADSHEET = "synthetic-spreadsheet-0000000001"
WORKBOOK: Mapping[str, Sequence[Sequence[str]]] = {
    "auggie: accounts": (("account_id", "outcome"), ("acc-northstar", "qualified")),
    "auggie: drafts": (("artifact_id",), ("=HYPERLINK(1)",)),
}


@pytest.fixture
def scope(scenario: Scenario) -> CustomerScope:
    return CustomerScope.model_validate(
        variant(scenario.scope, execution={"delivery": "simulated", "external_providers": True})
    )


class MemorySheets:
    """An in-memory spreadsheet with one tab the customer made themselves."""

    def __init__(self) -> None:
        self.tabs: dict[str, list[list[str]]] = {"Notes": [["keep me"]]}
        self.calls: list[tuple[str, str]] = []

    def sheet_titles(self, spreadsheet_id: str) -> list[str]:
        self.calls.append(("titles", spreadsheet_id))
        return list(self.tabs)

    def add_sheets(self, spreadsheet_id: str, titles: Sequence[str]) -> None:
        self.calls.append(("add", ",".join(titles)))
        for title in titles:
            self.tabs[title] = []

    def clear(self, spreadsheet_id: str, title: str) -> None:
        self.calls.append(("clear", title))
        self.tabs[title] = []

    def update(self, spreadsheet_id: str, title: str, rows: Sequence[Sequence[str]]) -> None:
        self.calls.append(("update", title))
        self.tabs[title] = [list(row) for row in rows]


def test_managed_tabs_are_added_cleared_and_rewritten(scope: CustomerScope) -> None:
    sheets = MemorySheets()
    sheets.tabs["auggie: accounts"] = [["stale", "rows"], ["from", "before"]]
    written = write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=sheets)
    assert written == ["auggie: accounts", "auggie: drafts"]
    assert sheets.tabs["auggie: accounts"] == [
        ["account_id", "outcome"],
        ["acc-northstar", "qualified"],
    ]
    assert sheets.tabs["Notes"] == [["keep me"]]
    assert ("add", "auggie: drafts") in sheets.calls
    assert not any(title == "Notes" for _, title in sheets.calls)


def test_writing_twice_gives_the_same_sheet(scope: CustomerScope) -> None:
    sheets = MemorySheets()
    write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=sheets)
    first = {title: rows[:] for title, rows in sheets.tabs.items()}
    write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=sheets)
    assert sheets.tabs == first


def test_refusals_happen_before_any_call(scope: CustomerScope, scenario: Scenario) -> None:
    sheets = MemorySheets()
    with pytest.raises(ExternalCallRefused):
        write_sheet(WORKBOOK, scope=scenario.scope, spreadsheet_id=SPREADSHEET, api=sheets)
    with pytest.raises(ValueError, match="spreadsheet ID"):
        write_sheet(WORKBOOK, scope=scope, spreadsheet_id="../evil", api=sheets)
    with pytest.raises(ValueError, match="only 'auggie: ' tabs"):
        write_sheet({"Notes": (("x",),)}, scope=scope, spreadsheet_id=SPREADSHEET, api=sheets)
    assert sheets.calls == []


# The real client, against a fake Google


class FakeGoogle(BaseAdapter):
    """Answers Google's token endpoint and the Sheets API from memory, and keeps requests."""

    def __init__(self, sheets_status: int = 200) -> None:
        super().__init__()
        self.requests: list[requests.PreparedRequest] = []
        self.sheets_status = sheets_status

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: bytes | str | tuple[bytes | str, bytes | str] | None = None,
        proxies: Mapping[str, str] | None = None,
    ) -> requests.Response:
        self.requests.append(request)
        reply = requests.Response()
        reply.request = request
        reply.url = request.url or ""
        reply.headers["Content-Type"] = "application/json"
        if (request.url or "").startswith("https://oauth2.googleapis.com/token"):
            reply.status_code = 200
            body: dict[str, Any] = {
                "access_token": "synthetic-access-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        elif request.method == "GET":
            reply.status_code = self.sheets_status
            body = {"sheets": [{"properties": {"title": "Notes"}}]}
        else:
            reply.status_code = self.sheets_status
            body = {}
        reply._content = json.dumps(body).encode()
        return reply

    def close(self) -> None:
        pass


@pytest.fixture
def service_account(tmp_path: Path, scope: CustomerScope) -> CredentialFiles:
    """A service account key generated for this test only; no key is ever committed."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    info = {
        "type": "service_account",
        "project_id": "demo-msp",
        "private_key_id": "synthetic-key-1",
        "private_key": pem,
        "client_email": "projection@demo-msp.iam.example",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    (folder / "google-service-account").write_text(json.dumps(info), encoding="utf-8")
    (folder / "google-service-account").chmod(0o600)
    return CredentialFiles(tmp_path)


def test_the_service_account_signs_in_and_writes_raw_values(
    scope: CustomerScope, service_account: CredentialFiles
) -> None:
    google = FakeGoogle()
    session = service_account_session(scope, service_account, adapter=google)
    write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=GoogleSheetsApi(session))

    token, *calls = google.requests
    assert token.url == "https://oauth2.googleapis.com/token"
    assert "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in str(token.body)
    assert all(c.headers["Authorization"] == "Bearer synthetic-access-token" for c in calls)
    assert all(
        (c.url or "").startswith(f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET}")
        for c in calls
    )
    methods = [
        (c.method, urllib.parse.unquote(urllib.parse.urlsplit(c.url or "").path)) for c in calls
    ]
    base = f"/v4/spreadsheets/{SPREADSHEET}"
    assert methods == [
        ("GET", base),
        ("POST", f"{base}:batchUpdate"),
        ("POST", f"{base}/values/'auggie: accounts':clear"),
        ("PUT", f"{base}/values/'auggie: accounts'"),
        ("POST", f"{base}/values/'auggie: drafts':clear"),
        ("PUT", f"{base}/values/'auggie: drafts'"),
    ]
    writes = [c for c in calls if c.method == "PUT"]
    assert all("valueInputOption=RAW" in (c.url or "") for c in writes)
    assert json.loads(writes[1].body or b"")["values"] == [["artifact_id"], ["=HYPERLINK(1)"]]
    added = json.loads(calls[1].body or b"")["requests"]
    assert [r["addSheet"]["properties"]["title"] for r in added] == [
        "auggie: accounts",
        "auggie: drafts",
    ]


def test_a_refused_call_raises_with_its_status_only(
    scope: CustomerScope, service_account: CredentialFiles
) -> None:
    session = service_account_session(scope, service_account, adapter=FakeGoogle(403))
    with pytest.raises(SheetsError, match="HTTP 403"):
        write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=GoogleSheetsApi(session))


def test_the_client_talks_only_to_the_sheets_api() -> None:
    with pytest.raises(ValueError, match="only go to"):
        GoogleSheetsApi(requests.Session())._call("GET", "https://northstar.example/x")


@pytest.mark.parametrize("content", ["not json", json.dumps({"type": "service_account"})])
def test_a_file_that_is_not_a_service_account_key_is_refused_without_showing_it(
    content: str, scope: CustomerScope, tmp_path: Path
) -> None:
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    (folder / "google-service-account").write_text(content, encoding="utf-8")
    (folder / "google-service-account").chmod(0o600)
    with pytest.raises(CredentialError, match="not a service account key") as caught:
        service_account_session(scope, CredentialFiles(tmp_path))
    assert content not in str(caught.value)
