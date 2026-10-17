"""Write a projection to a Google Sheet with the customer's service account (ADR 0018).

Only the four managed tabs are touched: missing ones are added, then each is
cleared and rewritten as raw text. Other tabs are never read, changed or
removed. Every request goes to Google's token endpoint or the Sheets API and
nowhere else, never follows a redirect, and fails without quoting Google's
reply. Needs the ``auggie-harness[google]`` extra.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import google.auth.transport
import requests
from google.auth.exceptions import TransportError
from google.oauth2 import service_account
from requests.adapters import BaseAdapter

from auggie.contracts import CustomerScope
from auggie.credentials import CredentialError, CredentialFiles
from auggie.external import require_external_calls
from auggie.projections import TABS, Workbook

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
KEY = "google-service-account"
"""Name of the service account's JSON key file in the scope's credential folder."""

TIMEOUT_SECONDS = 30.0


class SheetsError(Exception):
    """Google refused or failed a call. Says which call, never what Google replied."""


class SheetsApi(Protocol):
    """The few Sheets calls a projection needs."""

    def sheet_titles(self, spreadsheet_id: str) -> list[str]: ...

    def add_sheets(self, spreadsheet_id: str, titles: Sequence[str]) -> None: ...

    def clear(self, spreadsheet_id: str, title: str) -> None: ...

    def update(self, spreadsheet_id: str, title: str, rows: Sequence[Sequence[str]]) -> None: ...


class _TokenReply(google.auth.transport.Response):
    def __init__(self, reply: requests.Response) -> None:
        self._reply = reply

    @property
    def status(self) -> int:
        return self._reply.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._reply.headers

    @property
    def data(self) -> bytes:
        return self._reply.content


class _TokenEndpoint(google.auth.transport.Request):
    """Carries google-auth's token request to Google only: no redirects, no logging."""

    def __init__(self, session: requests.Session) -> None:
        self._session = session

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> _TokenReply:
        if url != TOKEN_URI:
            raise TransportError("only Google's token endpoint may be called")  # type: ignore[no-untyped-call]
        try:
            reply = self._session.request(
                method,
                url,
                data=body,
                headers=dict(headers or {}),
                timeout=timeout or TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise TransportError("the token request did not complete") from None  # type: ignore[no-untyped-call]
        return _TokenReply(reply)


class ServiceAccount:
    """A customer's service account, signed in on demand with short-lived tokens."""

    def __init__(self, account: service_account.Credentials, session: requests.Session) -> None:
        self._account = account
        self.session = session

    def authorization(self) -> dict[str, str]:
        if not self._account.valid:
            try:
                self._account.refresh(_TokenEndpoint(self.session))  # type: ignore[no-untyped-call]
            except Exception:
                # Any failure here, google-auth's or a parser's, may quote the token reply.
                raise SheetsError("could not sign in as the service account") from None
        token = self._account.token
        if not isinstance(token, str) or not re.fullmatch(r"[!-~]+", token):
            raise SheetsError("could not sign in as the service account")
        return {"Authorization": f"Bearer {token}"}


def sign_in(
    scope: CustomerScope, credentials: CredentialFiles, *, adapter: BaseAdapter | None = None
) -> ServiceAccount:
    """Load the scope's service account key. Nothing is sent until the first call.

    ``adapter`` replaces HTTPS, for tests.
    """
    key = credentials.secret(scope, KEY)
    refused = CredentialError(
        f"cannot use credential {scope.credential_namespace!r}/{KEY!r}: "
        "it is not a service account key for Google's token endpoint"
    )
    try:
        info = json.loads(key.reveal())
    except ValueError:
        raise refused from None
    if not isinstance(info, dict) or info.get("token_uri", TOKEN_URI) != TOKEN_URI:
        raise refused
    try:
        account = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            {**info, "token_uri": TOKEN_URI}, scopes=SCOPES
        )
    except (ValueError, KeyError, TypeError):
        raise refused from None
    session = requests.Session()
    if adapter is not None:
        session.mount("https://", adapter)
    return ServiceAccount(account, session)


def _range(title: str) -> str:
    """The A1 range of a whole tab, quoted for the URL path."""
    return urllib.parse.quote("'" + title.replace("'", "''") + "'", safe="")


class GoogleSheetsApi:
    """Sheets API v4, as the customer's service account."""

    def __init__(self, account: ServiceAccount) -> None:
        self._account = account

    def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        if not url.startswith(SHEETS_API + "/"):
            raise ValueError(f"requests may only go to {SHEETS_API}")
        headers = self._account.authorization()
        try:
            reply = self._account.session.request(
                method,
                url,
                headers=headers,
                timeout=TIMEOUT_SECONDS,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException:
            raise SheetsError(f"the call {method} {url} did not complete") from None
        if not 200 <= reply.status_code < 300:
            raise SheetsError(f"Google answered HTTP {reply.status_code} to {method} {url}")
        try:
            body = reply.json() if reply.content else {}
        except ValueError:
            raise SheetsError(f"Google's reply to {method} {url} was not JSON") from None
        return body if isinstance(body, dict) else {}

    def sheet_titles(self, spreadsheet_id: str) -> list[str]:
        url = f"{SHEETS_API}/{spreadsheet_id}"
        sheets = self._call("GET", url, params={"fields": "sheets.properties.title"}).get(
            "sheets", []
        )
        titles: list[str] = []
        for sheet in sheets if isinstance(sheets, list) else [None]:
            properties = sheet.get("properties") if isinstance(sheet, dict) else None
            title = properties.get("title") if isinstance(properties, dict) else None
            if not isinstance(title, str):
                raise SheetsError(f"Google's list of tabs from GET {url} was malformed")
            titles.append(title)
        return titles

    def add_sheets(self, spreadsheet_id: str, titles: Sequence[str]) -> None:
        additions = [{"addSheet": {"properties": {"title": title}}} for title in titles]
        self._call(
            "POST", f"{SHEETS_API}/{spreadsheet_id}:batchUpdate", json={"requests": additions}
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


def write_sheet(
    workbook: Workbook, *, scope: CustomerScope, spreadsheet_id: str, api: SheetsApi
) -> list[str]:
    """Write every tab of ``workbook`` into the spreadsheet. Returns the tabs written."""
    require_external_calls(scope)
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", spreadsheet_id):
        raise ValueError("not a Google spreadsheet ID")
    unmanaged = [title for title in workbook if title not in TABS]
    if unmanaged:
        raise ValueError(f"only the managed tabs {list(TABS)} may be written, not {unmanaged}")
    existing = set(api.sheet_titles(spreadsheet_id))
    missing = [title for title in workbook if title not in existing]
    if missing:
        api.add_sheets(spreadsheet_id, missing)
    for title, rows in workbook.items():
        api.clear(spreadsheet_id, title)
        api.update(spreadsheet_id, title, rows)
    return list(workbook)
