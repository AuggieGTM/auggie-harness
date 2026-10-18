"""Sign a seller in to Gmail once, and save the token the harness reads (ADR 0021).

A deployment step, like installing the Treg token: the seller runs it in their
own terminal, outside any agent session, and the harness never runs it. It opens
Google's consent page in the browser, signs in through a loopback redirect with
PKCE, and saves the result only if Google granted exactly ``gmail.send`` and
returned a refresh token. Nothing it prints contains a token, even when the
sign-in fails.

    uv run --with google-auth-oauthlib python scripts/gmail_sign_in.py \\
        <scope.json> <client_secret.json>

``client_secret.json`` is the Desktop app OAuth client downloaded from Google
Cloud. The token is saved as ``gmail-token`` in the scope's credential folder,
replacing whatever was there.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import stat
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

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


SignIn = Callable[[Mapping[str, Any], str], Signed]


def google_sign_in(client: Mapping[str, Any], scope: str) -> Signed:
    """Run Google's installed-app flow: loopback redirect, PKCE, consent asked every time."""
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

    flow = InstalledAppFlow.from_client_config(dict(client), scopes=[scope])
    signed: Signed = flow.run_local_server(
        host="localhost", port=0, prompt="consent", timeout_seconds=SIGN_IN_SECONDS
    )
    return signed


def desktop_client(client_file: Path) -> Mapping[str, Any]:
    """The client file's Desktop app configuration, and nothing else from it."""
    try:
        client = json.loads(client_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SignInRefused("the OAuth client file cannot be read as JSON") from None
    installed = client.get("installed") if isinstance(client, dict) else None
    if set(client) != {"installed"} or not isinstance(installed, dict):
        raise SignInRefused("the OAuth client must be a Desktop app client, and only that")
    if not all(isinstance(installed.get(key), str) for key in ("client_id", "client_secret")):
        raise SignInRefused("the Desktop app client has no client ID or secret")
    return {"installed": installed}


def sign_in(
    scope_file: Path,
    client_file: Path,
    *,
    root: Path | None = None,
    run: SignIn = google_sign_in,
) -> Path:
    """Sign in, check what Google granted, and save the token. Returns the saved path."""
    scope = CustomerScope.model_validate_json(scope_file.read_text(encoding="utf-8"))
    client = desktop_client(client_file)
    try:
        signed = run(client, SEND_SCOPE)
        granted = signed.granted_scopes
        refresh_token = signed.refresh_token
        token_file = signed.to_json()
    except Exception:
        # Not chained or shown: OAuth errors can carry Google's description and token values.
        raise SignInRefused("Google's sign-in did not complete; nothing was saved") from None
    scopes = set(granted.split() if isinstance(granted, str) else granted or ())
    if scopes != {SEND_SCOPE}:
        raise SignInRefused("Google did not grant exactly gmail.send; nothing was saved")
    if not refresh_token:
        raise SignInRefused("Google returned no refresh token; nothing was saved")
    folder = (root or Path(os.environ.get("AUGGIE_CREDENTIALS_DIR", DEFAULT_ROOT))) / (
        scope.credential_namespace
    )
    return _save(folder, token_file)


def _save(folder: Path, content: str) -> Path:
    """Write ``content`` as the folder's token, replacing any file or link there.

    The folder itself must be a real folder owned by this user: a link there could
    send the token into another scope's folder.
    """
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise SignInRefused(f"{folder} is not a real folder of yours; nothing was saved")
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".{CREDENTIAL}.{secrets.token_hex(8)}"
    try:
        os.fchmod(directory, 0o700)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(content)
            # Replaces a file or link at the token's path, never following it.
            os.replace(temporary, CREDENTIAL, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory)
            raise
    finally:
        os.close(directory)
    return folder / CREDENTIAL


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    logging.disable(logging.CRITICAL)  # the OAuth libraries may log token exchanges
    try:
        saved = sign_in(Path(argv[0]), Path(argv[1]))
    except SignInRefused as refused:
        print(f"stopped   {refused}", file=sys.stderr)
        return 1
    except Exception as error:
        print(
            f"stopped   the sign-in failed ({type(error).__name__}); nothing was saved",
            file=sys.stderr,
        )
        return 1
    print(f"saved     {saved}: it can send as the account you chose; it was not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
