from __future__ import annotations

import re
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
