"""The Sheets writer, against an in-memory API and a fake Google over HTTPS (ADR 0018)."""

from __future__ import annotations

import json
import logging
import traceback
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
from auggie.projections import Workbook
from auggie.projections.sheets import GoogleSheetsApi, SheetsError, sign_in, write_sheet
from tests.conftest import Scenario, variant

SPREADSHEET = "synthetic-spreadsheet-0000000001"
WORKBOOK: Workbook = {
    "auggie: accounts": (("account_id", "outcome"), ("acc-northstar", "qualified")),
    "auggie: drafts": (("artifact_id",), ("=HYPERLINK(1)",)),
}
GOOGLE_SECRET = "synthetic-google-reply-secret"


@pytest.fixture
def scope(scenario: Scenario) -> CustomerScope:
    return CustomerScope.model_validate(
        variant(scenario.scope, execution={"delivery": "simulated", "external_providers": True})
    )


class MemorySheets:
    """An in-memory spreadsheet holding tabs the customer made themselves."""

    def __init__(self) -> None:
        self.tabs: dict[str, list[list[str]]] = {
            "Notes": [["keep me"]],
            "auggie: personal notes": [["keep me too"]],
        }
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
    assert sheets.tabs["auggie: personal notes"] == [["keep me too"]]
    assert ("add", "auggie: drafts") in sheets.calls


def test_writing_twice_gives_the_same_sheet(scope: CustomerScope) -> None:
    sheets = MemorySheets()
    write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=sheets)
    first = {title: rows[:] for title, rows in sheets.tabs.items()}
    write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=sheets)
    assert sheets.tabs == first


@pytest.mark.parametrize("title", ["Notes", "auggie: personal notes"])
def test_only_the_four_managed_tabs_may_be_written(title: str, scope: CustomerScope) -> None:
    sheets = MemorySheets()
    with pytest.raises(ValueError, match="only the managed tabs"):
        write_sheet({title: (("x",),)}, scope=scope, spreadsheet_id=SPREADSHEET, api=sheets)
    assert sheets.calls == []


def test_refusals_happen_before_any_call(scope: CustomerScope, scenario: Scenario) -> None:
    sheets = MemorySheets()
    with pytest.raises(ExternalCallRefused):
        write_sheet(WORKBOOK, scope=scenario.scope, spreadsheet_id=SPREADSHEET, api=sheets)
    with pytest.raises(ValueError, match="spreadsheet ID"):
        write_sheet(WORKBOOK, scope=scope, spreadsheet_id="../evil", api=sheets)
    assert sheets.calls == []


# The real client, against a fake Google

Answer = tuple[int, dict[str, Any], dict[str, str]]
TOKEN_OK: Answer = (
    200,
    {"access_token": "synthetic-access-token", "expires_in": 3600, "token_type": "Bearer"},
    {},
)
LIST_OK: Answer = (200, {"sheets": [{"properties": {"title": "Notes"}}]}, {})


class FakeGoogle(BaseAdapter):
    """Answers the token endpoint and the Sheets API from memory, and keeps every request."""

    def __init__(
        self, token: Answer = TOKEN_OK, answers: Mapping[str, Answer] | None = None
    ) -> None:
        super().__init__()
        self.token = token
        self.answers = {"GET": LIST_OK, **(answers or {})}
        self.requests: list[requests.PreparedRequest] = []

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
        is_token = (request.url or "").startswith("https://oauth2.googleapis.com/token")
        status, body, headers = (
            self.token if is_token else self.answers.get(request.method or "", (200, {}, {}))
        )
        reply = requests.Response()
        reply.request = request
        reply.url = request.url or ""
        reply.status_code = status
        reply.headers.update({"Content-Type": "application/json", **headers})
        reply._content = json.dumps(body).encode()
        return reply

    def close(self) -> None:
        pass


def key_file(tmp_path: Path, scope: CustomerScope, **changes: Any) -> CredentialFiles:
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
        **changes,
    }
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700, exist_ok=True)
    (folder / "google-service-account").write_text(json.dumps(info), encoding="utf-8")
    (folder / "google-service-account").chmod(0o600)
    return CredentialFiles(tmp_path)


def write(scope: CustomerScope, credentials: CredentialFiles, google: FakeGoogle) -> list[str]:
    api = GoogleSheetsApi(sign_in(scope, credentials, adapter=google))
    return write_sheet(WORKBOOK, scope=scope, spreadsheet_id=SPREADSHEET, api=api)


def test_the_service_account_signs_in_and_writes_raw_values(
    scope: CustomerScope, tmp_path: Path
) -> None:
    google = FakeGoogle()
    write(scope, key_file(tmp_path, scope), google)

    token, *calls = google.requests
    assert token.url == "https://oauth2.googleapis.com/token"
    assert "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in str(token.body)
    assert all(c.headers["Authorization"] == "Bearer synthetic-access-token" for c in calls)
    base = f"/v4/spreadsheets/{SPREADSHEET}"
    paths = [
        (c.method, urllib.parse.unquote(urllib.parse.urlsplit(c.url or "").path)) for c in calls
    ]
    assert paths == [
        ("GET", base),
        ("POST", f"{base}:batchUpdate"),
        ("POST", f"{base}/values/'auggie: accounts':clear"),
        ("PUT", f"{base}/values/'auggie: accounts'"),
        ("POST", f"{base}/values/'auggie: drafts':clear"),
        ("PUT", f"{base}/values/'auggie: drafts'"),
    ]
    assert all((c.url or "").startswith("https://sheets.googleapis.com/") for c in calls)
    writes = [c for c in calls if c.method == "PUT"]
    assert all("valueInputOption=RAW" in (c.url or "") for c in writes)
    assert json.loads(writes[1].body or b"")["values"] == [["artifact_id"], ["=HYPERLINK(1)"]]


def test_a_refused_call_raises_with_its_status_only(scope: CustomerScope, tmp_path: Path) -> None:
    google = FakeGoogle(answers={"GET": (403, {"error": {"message": GOOGLE_SECRET}}, {})})
    with pytest.raises(SheetsError, match="HTTP 403") as caught:
        write(scope, key_file(tmp_path, scope), google)
    assert GOOGLE_SECRET not in str(caught.value)


def test_a_failed_sign_in_quotes_nothing_and_logs_nothing(
    scope: CustomerScope, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    google = FakeGoogle(
        token=(400, {"error": "invalid_grant", "error_description": GOOGLE_SECRET}, {})
    )
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SheetsError, match="could not sign in") as caught:
        write(scope, key_file(tmp_path, scope), google)
    assert GOOGLE_SECRET not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert GOOGLE_SECRET not in caplog.text
    assert len(google.requests) == 1


@pytest.mark.parametrize(
    "token_uri", ["https://sink.example/token", "http://oauth2.googleapis.com/token"]
)
def test_a_key_pointing_elsewhere_is_refused_before_any_request(
    token_uri: str, scope: CustomerScope, tmp_path: Path
) -> None:
    google = FakeGoogle()
    with pytest.raises(CredentialError, match="not a service account key"):
        write(scope, key_file(tmp_path, scope, token_uri=token_uri), google)
    assert google.requests == []


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_never_followed(status: int, scope: CustomerScope, tmp_path: Path) -> None:
    moved = {"Location": "https://sink.example/capture"}
    credentials = key_file(tmp_path, scope)
    token_redirect = FakeGoogle(token=(status, {}, moved))
    with pytest.raises(SheetsError, match="could not sign in"):
        write(scope, credentials, token_redirect)
    assert len(token_redirect.requests) == 1
    write_redirect = FakeGoogle(answers={"PUT": (status, {}, moved)})
    with pytest.raises(SheetsError, match=f"HTTP {status} to PUT"):
        write(scope, credentials, write_redirect)
    assert not any("sink.example" in (r.url or "") for r in write_redirect.requests)
    same_origin = FakeGoogle(
        answers={"GET": (status, {}, {"Location": "https://sheets.googleapis.com/elsewhere"})}
    )
    with pytest.raises(SheetsError, match=f"HTTP {status} to GET"):
        write(scope, credentials, same_origin)
    assert len(same_origin.requests) == 2


@pytest.mark.parametrize(
    "body",
    [{"sheets": 1}, {"sheets": [1]}, {"sheets": [{"properties": []}]}, {"sheets": [{}]}],
)
def test_a_malformed_list_of_tabs_is_refused(
    body: dict[str, Any], scope: CustomerScope, tmp_path: Path
) -> None:
    google = FakeGoogle(answers={"GET": (200, body, {})})
    with pytest.raises(SheetsError, match="list of tabs"):
        write(scope, key_file(tmp_path, scope), google)


def test_the_client_talks_only_to_the_sheets_api(scope: CustomerScope, tmp_path: Path) -> None:
    api = GoogleSheetsApi(sign_in(scope, key_file(tmp_path, scope), adapter=FakeGoogle()))
    with pytest.raises(ValueError, match="only go to"):
        api._call("GET", "https://northstar.example/x")


@pytest.mark.parametrize("content", ["not json", json.dumps({"type": "service_account"})])
def test_a_file_that_is_not_a_service_account_key_is_refused_without_showing_it(
    content: str, scope: CustomerScope, tmp_path: Path
) -> None:
    folder = tmp_path / scope.credential_namespace
    folder.mkdir(mode=0o700)
    (folder / "google-service-account").write_text(content, encoding="utf-8")
    (folder / "google-service-account").chmod(0o600)
    with pytest.raises(CredentialError, match="not a service account key") as caught:
        sign_in(scope, CredentialFiles(tmp_path))
    assert content not in str(caught.value)


@pytest.mark.parametrize(
    "token",
    [
        {"access_token": GOOGLE_SECRET, "expires_in": GOOGLE_SECRET, "token_type": "Bearer"},
        {"access_token": f"{GOOGLE_SECRET}\nInjected: header", "expires_in": 3600},
        {"access_token": 12345, "expires_in": 3600},
        {"expires_in": 3600},
    ],
    ids=["text-expiry", "header-breaking-token", "number-token", "no-token"],
)
def test_a_malformed_token_reply_quotes_nothing_and_stops(
    token: dict[str, Any], scope: CustomerScope, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    google = FakeGoogle(token=(200, token, {}))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SheetsError, match="could not sign in") as caught:
        write(scope, key_file(tmp_path, scope), google)
    assert GOOGLE_SECRET not in str(caught.value) and GOOGLE_SECRET not in caplog.text
    assert GOOGLE_SECRET not in "".join(traceback.format_exception(caught.value))
    assert len(google.requests) == 1
