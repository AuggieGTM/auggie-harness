from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(
    [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")],
    key=lambda path: str(path),
)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize("doc", DOCS, ids=lambda path: str(path.relative_to(ROOT)))
def test_relative_links_resolve(doc: Path) -> None:
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        path = (doc.parent / target.split("#", 1)[0]).resolve()
        assert path.exists(), f"{doc.relative_to(ROOT)} links to missing {target}"


GUIDE = ROOT / "docs" / "first-prospect.md"


def _token_install() -> str:
    """The guide's token-install snippet, exactly as a reader would paste it."""
    text = GUIDE.read_text(encoding="utf-8")
    blocks = [m.group(1) for m in re.finditer(r"```bash\n(.*?)```", text, re.DOTALL)]
    (snippet,) = [block for block in blocks if "read -rs TOKEN" in block]
    return snippet.replace("<credential_namespace>", "demo-ns")


def _paste(home: Path, typed: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _token_install()],
        input=typed,
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home), **env},
        check=False,
    )


bash_only = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@bash_only
def test_token_install_writes_a_private_file_over_a_link(tmp_path: Path) -> None:
    folder = tmp_path / ".config" / "auggie" / "credentials" / "demo-ns"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("unrelated\n")
    (folder / "treg-token").symlink_to(outside)

    assert _paste(tmp_path, "synthetic-token-0000\n").returncode == 0

    token = folder / "treg-token"
    assert not token.is_symlink()
    assert token.read_text() == "synthetic-token-0000\n"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert outside.read_text() == "unrelated\n"
    assert sorted(path.name for path in folder.iterdir()) == ["treg-token"]


@bash_only
def test_aborted_token_install_leaves_other_files_alone(tmp_path: Path) -> None:
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("keep me\n")

    # No token typed, and T already names a file in the pasting shell.
    assert _paste(tmp_path, "", T=str(unrelated)).returncode != 0

    assert unrelated.read_text() == "keep me\n"
    folder = tmp_path / ".config" / "auggie" / "credentials" / "demo-ns"
    assert list(folder.iterdir()) == []
