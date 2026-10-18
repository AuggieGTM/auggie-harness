"""The Gmail sign-in script, with Google's flow replaced (no browser, no network)."""

from __future__ import annotations

import importlib.util
import json
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from auggie.delivery.gmail import SEND_SCOPE
from tests.conftest import NORTHSTAR

ROOT = Path(__file__).resolve().parent.parent
REFRESH_TOKEN = "synthetic-refresh-token-0000"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gmail_sign_in", ROOT / "scripts" / "gmail_sign_in.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load()


@dataclass
class Signed:
    refresh_token: str | None = REFRESH_TOKEN
    granted_scopes: Iterable[str] | str | None = (SEND_SCOPE,)

    def to_json(self) -> str:
        return json.dumps(
            {
                "client_id": "synthetic-client.apps.googleusercontent.example",
                "client_secret": "synthetic-client-secret-0000",
                "refresh_token": self.refresh_token,
                "scopes": [SEND_SCOPE],
            }
        )


def client(tmp_path: Path, kind: str = "installed") -> Path:
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({kind: {"client_id": "synthetic-client"}}), encoding="utf-8")
    return path


def folder(tmp_path: Path) -> Path:
    return tmp_path / "credentials" / "demo-msp"


def test_a_gmail_send_token_is_saved_privately(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = script.sign_in(
        NORTHSTAR / "scope.json",
        client(tmp_path),
        root=tmp_path / "credentials",
        run=lambda _client, _scope: Signed(),
    )
    assert saved == folder(tmp_path) / "gmail-token"
    assert json.loads(saved.read_text())["refresh_token"] == REFRESH_TOKEN
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder(tmp_path).stat().st_mode) == 0o700
    assert sorted(p.name for p in folder(tmp_path).iterdir()) == ["gmail-token"]
    assert REFRESH_TOKEN not in capsys.readouterr().out


def test_signing_in_again_replaces_a_link_without_following_it(tmp_path: Path) -> None:
    folder(tmp_path).mkdir(parents=True, mode=0o700)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("untouched\n")
    (folder(tmp_path) / "gmail-token").symlink_to(elsewhere)
    saved = script.sign_in(
        NORTHSTAR / "scope.json",
        client(tmp_path),
        root=tmp_path / "credentials",
        run=lambda _client, _scope: Signed(),
    )
    assert not saved.is_symlink() and elsewhere.read_text() == "untouched\n"


@pytest.mark.parametrize(
    ("signed", "refused"),
    [
        (Signed(granted_scopes=(SEND_SCOPE, "openid")), "exactly gmail.send"),
        (Signed(granted_scopes=None), "exactly gmail.send"),
        (Signed(refresh_token=None), "no refresh token"),
    ],
    ids=["broader", "unknown-grant", "no-refresh-token"],
)
def test_nothing_is_saved_unless_google_granted_exactly_gmail_send(
    signed: Signed, refused: str, tmp_path: Path
) -> None:
    with pytest.raises(script.SignInRefused, match=refused):
        script.sign_in(
            NORTHSTAR / "scope.json",
            client(tmp_path),
            root=tmp_path / "credentials",
            run=lambda _client, _scope: signed,
        )
    assert not folder(tmp_path).exists()


def test_only_a_desktop_app_client_is_used(tmp_path: Path) -> None:
    with pytest.raises(script.SignInRefused, match="Desktop app"):
        script.sign_in(
            NORTHSTAR / "scope.json",
            client(tmp_path, kind="web"),
            root=tmp_path / "credentials",
            run=lambda _client, _scope: Signed(),
        )


def test_the_sign_in_asks_google_for_gmail_send_only(tmp_path: Path) -> None:
    asked: list[str] = []

    def run(_client: Path, scope: str) -> Signed:
        asked.append(scope)
        return Signed(granted_scopes=scope)

    script.sign_in(NORTHSTAR / "scope.json", client(tmp_path), root=tmp_path / "c", run=run)
    assert asked == [SEND_SCOPE]
