from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_public_boundary", ROOT / "scripts" / "check_public_boundary.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boundary = _load()

# Built by concatenation so this file never contains a matching literal.
SESSION_LINK = "https://sageox" + ".ai/c/" + "ses_" + "0123abcd-4567-0000"
SESSION_TRAILER = "SageOx" + "-Session: " + SESSION_LINK
DASHBOARD = "https://sageox" + ".ai/" + "team/anything"
TEAM_ID = "team_" + "abc1defgh2"
PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----"
PERSONAL_EMAIL = "someone" + "@" + "personal-domain.net"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@needs_git
def test_repository_history_is_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    inside = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"], capture_output=True
    )
    if inside.returncode != 0:
        pytest.skip("not a git checkout")
    monkeypatch.chdir(ROOT)
    assert boundary.check_history(boundary.load_denylist()) == []


@needs_git
def test_history_check_finds_deleted_private_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "notes.md").write_text("Kickoff with Example Customer Co.\n")
    _git(tmp_path, "add", "notes.md")
    _git(tmp_path, "commit", "-q", "-m", "add notes")
    _git(tmp_path, "rm", "-q", "notes.md")
    _git(tmp_path, "commit", "-q", "-m", "remove notes")
    monkeypatch.chdir(tmp_path)
    problems = boundary.check_history(["example customer"])
    assert len(problems) == 1
    assert problems[0].startswith("notes.md (")
    assert problems[0].endswith("contains private term #1 from the denylist")


def test_local_state_and_env_files_cannot_be_committed() -> None:
    blocked = [".sageox/config.json", "nested/.claude/settings.json", ".env", "app/.env.local"]
    assert len(boundary.path_problems(blocked)) == len(blocked)
    allowed = ["src/auggie/__init__.py", "AGENTS.md", ".env.example", "docs/environment.md"]
    assert boundary.path_problems(allowed) == []


@pytest.mark.parametrize(
    "text",
    [SESSION_LINK, SESSION_TRAILER, SESSION_TRAILER.lower(), DASHBOARD, TEAM_ID, PRIVATE_KEY],
)
def test_private_references_and_credentials_are_caught(text: str) -> None:
    assert boundary.text_problems("sample", text, terms=[])


def test_public_mentions_are_allowed() -> None:
    text = (
        "Maintainers use [SageOx](https://sageox.ai). Run `ox agent prime`. "
        "team_members and repo_url are ordinary names. "
        "Co-Authored-By: Claude <noreply@anthropic.com>; jordan@northstar.example."
    )
    assert boundary.text_problems("sample", text, terms=[]) == []


def test_personal_email_addresses_are_caught() -> None:
    problems = boundary.text_problems("sample", f"Contact {PERSONAL_EMAIL}", terms=[])
    assert problems == ["sample: email address at non-public domain 'personal-domain.net'"]


def test_denylisted_terms_are_caught_without_echoing_them() -> None:
    problems = boundary.text_problems(
        "sample", "Notes about Example Customer Co.", ["example customer"]
    )
    assert problems == ["sample: contains private term #1 from the denylist"]
