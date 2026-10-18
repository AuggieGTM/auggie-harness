"""Sign a seller in to Gmail once, and save the token the harness reads (ADR 0021).

A deployment step, like installing the Treg token: the seller runs it in their
own terminal, outside any agent session, and the harness never runs it. It opens
Google's consent page in the browser, signs in through a loopback redirect with
PKCE, and saves the result only if Google granted exactly ``gmail.send`` and
returned a refresh token. Nothing it prints contains a token.

    uv run --with google-auth-oauthlib python scripts/gmail_sign_in.py \\
        <scope.json> <client_secret.json>

``client_secret.json`` is the Desktop app OAuth client downloaded from Google
Cloud. The token is saved as ``gmail-token`` in the scope's credential folder,
replacing whatever was there.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from auggie.contracts import CustomerScope
from auggie.credentials import DEFAULT_ROOT
from auggie.delivery.gmail import CREDENTIAL, SEND_SCOPE

SIGN_IN_SECONDS = 300


class SignInRefused(Exception):
    """The sign-in did not produce a token the harness may use. Nothing was saved."""


class Signed(Protocol):
    """What a finished sign-in yields: google.oauth2.credentials.Credentials, in practice."""

    @property
    def refresh_token(self) -> str | None: ...

    @property
    def granted_scopes(self) -> Iterable[str] | str | None: ...

    def to_json(self) -> str: ...


SignIn = Callable[[Path, str], Signed]


def google_sign_in(client_file: Path, scope: str) -> Signed:
    """Run Google's installed-app flow: loopback redirect, PKCE, consent asked every time."""
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=[scope])
    signed: Signed = flow.run_local_server(
        host="localhost", port=0, prompt="consent", timeout_seconds=SIGN_IN_SECONDS
    )
    return signed


def sign_in(
    scope_file: Path,
    client_file: Path,
    *,
    root: Path | None = None,
    run: SignIn = google_sign_in,
) -> Path:
    """Sign in, check what Google granted, and save the token. Returns the saved path."""
    scope = CustomerScope.model_validate_json(scope_file.read_text(encoding="utf-8"))
    client = json.loads(client_file.read_text(encoding="utf-8"))
    if not isinstance(client, dict) or "installed" not in client:
        raise SignInRefused("the OAuth client must be a Desktop app client")
    signed = run(client_file, SEND_SCOPE)
    granted = signed.granted_scopes
    scopes = set(granted.split() if isinstance(granted, str) else granted or ())
    if scopes != {SEND_SCOPE}:
        raise SignInRefused("Google did not grant exactly gmail.send; nothing was saved")
    if not signed.refresh_token:
        raise SignInRefused("Google returned no refresh token; nothing was saved")
    folder = (root or Path(os.environ.get("AUGGIE_CREDENTIALS_DIR", DEFAULT_ROOT))) / (
        scope.credential_namespace
    )
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{CREDENTIAL}.", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(signed.to_json())
        target = folder / CREDENTIAL
        os.replace(temporary, target)  # replaces a file or link at the path, never follows it
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        saved = sign_in(Path(argv[0]), Path(argv[1]))
    except SignInRefused as refused:
        print(f"stopped   {refused}", file=sys.stderr)
        return 1
    print(f"saved     {saved}: it can send as the account you chose; it was not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
