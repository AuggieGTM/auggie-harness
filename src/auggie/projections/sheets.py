"""Write a projection to a Google Sheet with the customer's service account (ADR 0018).

Only the managed ``auggie: …`` tabs are touched: missing ones are added, then
each is cleared and rewritten as raw text. Other tabs are never read, changed
or removed. Needs the ``auggie-harness[google]`` extra.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import requests
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2 import service_account
from requests.adapters import BaseAdapter

from auggie.contracts import CustomerScope
from auggie.credentials import CredentialError, CredentialFiles
from auggie.external import require_external_calls

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
KEY = "google-service-account"
"""Name of the service account's JSON key file in the scope's credential folder."""

MANAGED_PREFIX = "auggie: "
TIMEOUT_SECONDS = 30.0

Sheet = Mapping[str, Sequence[Sequence[str]]]
"""Tab name to rows, header first: the shape of a projection workbook."""


class SheetsError(Exception):
    """Google refused or failed a Sheets call. Carries the HTTP status, not the reply."""


class SheetsApi(Protocol):
    """The few Sheets calls a projection needs."""

    def sheet_titles(self, spreadsheet_id: str) -> list[str]: ...

    def add_sheets(self, spreadsheet_id: str, titles: Sequence[str]) -> None: ...

    def clear(self, spreadsheet_id: str, title: str) -> None: ...

    def update(self, spreadsheet_id: str, title: str, rows: Sequence[Sequence[str]]) -> None: ...


def _range(title: str) -> str:
    """The A1 range of a whole tab, quoted for the URL path."""
    return urllib.parse.quote("'" + title.replace("'", "''") + "'", safe="")


class GoogleSheetsApi:
    """Sheets API v4 over an authorized ``requests`` session."""

    def __init__(self, session: requests.Session) -> None:
        self._session = session

    def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if not url.startswith(SHEETS_API + "/"):
            raise ValueError(f"requests may only go to {SHEETS_API}")
        try:
            reply = self._session.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
        except requests.RequestException:
            raise SheetsError(f"the Sheets call to {method} {url} failed to complete") from None
        if not reply.ok:
            raise SheetsError(f"Google answered HTTP {reply.status_code} to {method} {url}")
        try:
            body = reply.json() if reply.content else {}
        except ValueError:
            raise SheetsError(f"Google's reply to {method} {url} was not JSON") from None
        return body if isinstance(body, dict) else {}

    def sheet_titles(self, spreadsheet_id: str) -> list[str]:
        body = self._call(
            "GET", f"{SHEETS_API}/{spreadsheet_id}", params={"fields": "sheets.properties.title"}
        )
        return [
            str(sheet.get("properties", {}).get("title", ""))
            for sheet in body.get("sheets", [])
            if isinstance(sheet, dict)
        ]

    def add_sheets(self, spreadsheet_id: str, titles: Sequence[str]) -> None:
        requests_ = [{"addSheet": {"properties": {"title": title}}} for title in titles]
        self._call(
            "POST", f"{SHEETS_API}/{spreadsheet_id}:batchUpdate", json={"requests": requests_}
        )

    def clear(self, spreadsheet_id: str, title: str) -> None:
        self._call("POST", f"{SHEETS_API}/{spreadsheet_id}/values/{_range(title)}:clear", json={})

    def update(self, spreadsheet_id: str, title: str, rows: Sequence[Sequence[str]]) -> None:
        self._call(
            "PUT",
            f"{SHEETS_API}/{spreadsheet_id}/values/{_range(title)}",
            params={"valueInputOption": "RAW"},
            json={"majorDimension": "ROWS", "values": [list(row) for row in rows]},
        )


def service_account_session(
    scope: CustomerScope,
    credentials: CredentialFiles,
    *,
    adapter: BaseAdapter | None = None,
) -> AuthorizedSession:
    """A session that signs in as the scope's service account and refreshes its own tokens.

    ``adapter`` replaces HTTPS for both the token and the Sheets calls, for tests.
    """
    key = credentials.secret(scope, KEY)
    try:
        info = json.loads(key.reveal())
        # google-auth is only partly annotated.
        account = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=SCOPES
        )
    except (ValueError, KeyError, TypeError):
        raise CredentialError(
            f"cannot use credential {scope.credential_namespace!r}/{KEY!r}: "
            "it is not a service account key"
        ) from None
    refresh_session = requests.Session()
    session = AuthorizedSession(  # type: ignore[no-untyped-call]
        account, auth_request=Request(session=refresh_session)
    )
    if adapter is not None:
        refresh_session.mount("https://", adapter)
        session.mount("https://", adapter)
    return session


def write_sheet(
    workbook: Sheet, *, scope: CustomerScope, spreadsheet_id: str, api: SheetsApi
) -> list[str]:
    """Write every tab of ``workbook`` into the spreadsheet. Returns the tabs written."""
    require_external_calls(scope)
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", spreadsheet_id):
        raise ValueError("not a Google spreadsheet ID")
    unmanaged = [title for title in workbook if not title.startswith(MANAGED_PREFIX)]
    if unmanaged:
        raise ValueError(f"only {MANAGED_PREFIX!r} tabs may be written, not {unmanaged}")
    existing = set(api.sheet_titles(spreadsheet_id))
    missing = [title for title in workbook if title not in existing]
    if missing:
        api.add_sheets(spreadsheet_id, missing)
    for title, rows in workbook.items():
        api.clear(spreadsheet_id, title)
        api.update(spreadsheet_id, title, rows)
    return list(workbook)
