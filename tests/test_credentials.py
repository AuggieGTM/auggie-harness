"""Credential files stay private and inside their customer namespace."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from auggie import credentials
from auggie.contracts import CustomerScope
from auggie.credentials import CredentialError, CredentialFiles, Secret

VALUE = "invented-test-secret"


def scope(namespace: str = "demo-credentials") -> CustomerScope:
    return CustomerScope(
        customer_id=namespace,
        synthetic=True,
        data_namespace=f"{namespace}-data",
        credential_namespace=namespace,
        knowledge_namespace=f"{namespace}-knowledge",
    )


def secret_file(root: Path, namespace: str = "demo-credentials", name: str = "token") -> Path:
    folder = root / namespace
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / name
    path.write_text(VALUE, encoding="utf-8")
    path.chmod(0o600)
    return path


def refused(root: Path, name: str = "token", customer: CustomerScope | None = None) -> None:
    customer = customer or scope()
    with pytest.raises(CredentialError) as caught:
        CredentialFiles(root).secret(customer, name)
    message = str(caught.value)
    assert repr(customer.credential_namespace) in message
    assert repr(name) in message
    assert VALUE not in message


def test_a_valid_secret_is_read_without_caching(tmp_path: Path) -> None:
    path = secret_file(tmp_path)
    files = CredentialFiles(tmp_path)
    assert files.root == tmp_path
    assert files.secret(scope(), "token").reveal() == VALUE
    path.write_text("replacement", encoding="utf-8")
    assert files.secret(scope(), "token").reveal() == "replacement"


def test_environment_sets_the_root_but_an_explicit_root_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_root = tmp_path / "environment"
    explicit = tmp_path / "explicit"
    secret_file(env_root)
    secret_file(explicit).write_text("explicit-value", encoding="utf-8")
    monkeypatch.setenv("AUGGIE_CREDENTIALS_DIR", str(env_root))
    assert CredentialFiles().root == env_root
    assert CredentialFiles().secret(scope(), "token").reveal() == VALUE
    assert CredentialFiles(explicit).secret(scope(), "token").reveal() == "explicit-value"
    monkeypatch.delenv("AUGGIE_CREDENTIALS_DIR")
    monkeypatch.setattr(credentials, "DEFAULT_ROOT", env_root)
    assert CredentialFiles().root == env_root


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (" key \n", " key "),
        ("key\r\n", "key"),
        ("key\n\n", "key\n"),
        ("\tkey\r", "\tkey\r"),
        (" ", " "),
        ("clé", "clé"),
    ],
)
def test_only_one_trailing_newline_is_stripped(tmp_path: Path, content: str, expected: str) -> None:
    secret_file(tmp_path).write_bytes(content.encode("utf-8"))
    assert CredentialFiles(tmp_path).secret(scope(), "token").reveal() == expected


@pytest.mark.parametrize(
    "name", ["", ".", "..", "../other/token", "/token", "a/b", "A", "a\n", "a" * 65]
)
def test_bad_names_and_path_escapes_are_refused(tmp_path: Path, name: str) -> None:
    secret_file(tmp_path)
    refused(tmp_path, name)


def test_the_longest_valid_name_is_allowed(tmp_path: Path) -> None:
    name = "a" + "_.-" * 21
    secret_file(tmp_path, name=name)
    assert CredentialFiles(tmp_path).secret(scope(), name).reveal() == VALUE


def test_missing_folders_and_files_are_refused(tmp_path: Path) -> None:
    refused(tmp_path)
    (tmp_path / scope().credential_namespace).mkdir(mode=0o700)
    refused(tmp_path)


def test_a_namespace_must_be_a_directory(tmp_path: Path) -> None:
    (tmp_path / scope().credential_namespace).write_text(VALUE)
    refused(tmp_path)


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o620, 0o602, 0o610, 0o601, 0o666])
def test_group_and_other_file_permissions_are_refused(tmp_path: Path, mode: int) -> None:
    secret_file(tmp_path).chmod(mode)
    refused(tmp_path)


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_group_and_other_writable_folders_are_refused(tmp_path: Path, mode: int) -> None:
    secret_file(tmp_path).parent.chmod(mode)
    refused(tmp_path)


@pytest.mark.parametrize("content", [b"", b"\n", b"\r\n", VALUE.encode() + b"\xff"])
def test_empty_and_non_utf8_secrets_are_refused(tmp_path: Path, content: bytes) -> None:
    secret_file(tmp_path).write_bytes(content)
    refused(tmp_path)


def test_symlinked_files_even_inside_the_namespace_are_refused(tmp_path: Path) -> None:
    path = secret_file(tmp_path)
    path.rename(path.with_name("target"))
    path.symlink_to(path.with_name("target"))
    refused(tmp_path)


def test_symlinked_folders_are_refused(tmp_path: Path) -> None:
    path = secret_file(tmp_path)
    target = tmp_path / "other"
    path.parent.rename(target)
    path.parent.symlink_to(target, target_is_directory=True)
    refused(tmp_path)


def test_a_resolved_path_cannot_escape_the_namespace(tmp_path: Path) -> None:
    other = secret_file(tmp_path, "other")
    path = secret_file(tmp_path)
    path.unlink()
    path.symlink_to(other)
    refused(tmp_path)


def test_a_directory_or_fifo_cannot_be_read_as_a_secret(tmp_path: Path) -> None:
    path = secret_file(tmp_path)
    path.unlink()
    path.mkdir(mode=0o700)
    refused(tmp_path)
    path.rmdir()
    os.mkfifo(path, mode=0o600)
    refused(tmp_path)


def test_another_owner_is_refused(tmp_path: Path) -> None:
    if os.getuid() != 0:
        pytest.skip("changing file ownership requires root")
    path = secret_file(tmp_path)
    os.chown(path, 1, -1)
    refused(tmp_path)


def test_two_scopes_only_read_their_own_secrets(tmp_path: Path) -> None:
    secret_file(tmp_path)
    secret_file(tmp_path, "other", "other-token")
    files = CredentialFiles(tmp_path)
    assert files.secret(scope(), "token").reveal() == VALUE
    assert files.secret(scope("other"), "other-token").reveal() == VALUE
    refused(tmp_path, "other-token")
    refused(tmp_path, "token", scope("other"))


def test_even_an_unvalidated_namespace_cannot_escape(tmp_path: Path) -> None:
    customer = scope().model_copy(update={"credential_namespace": "../other"})
    refused(tmp_path, customer=customer)


def test_secret_formatting_never_shows_the_value() -> None:
    secret = Secret(VALUE)
    assert str(secret) == repr(secret) == f"{secret}" == "***"
    assert "%s" % secret == "%r" % secret == "***"  # noqa: UP031 -- test percent formatting
    assert f"{secret!r}" == "***"
