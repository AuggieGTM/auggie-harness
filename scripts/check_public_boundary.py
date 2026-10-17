"""Keep private material out of this public repository (ADR 0010, ADR 0011).

Checks paths, file contents, commit messages and author emails for SageOx state, links to and
identifiers of private SageOx material, credentials, personal email addresses, and private terms.

Private terms (customer names, personal email addresses) come from a local file that is never
committed: $AUGGIE_PUBLIC_DENYLIST, or ~/.config/auggie/public-denylist.txt. One term per line;
blank lines and lines starting with # are ignored; matching is case-insensitive. CI loads the
same list from a repository secret. Problems name a term by its line number, never its text.

Usage:
  check_public_boundary.py --history        every path, file version and commit reachable from a ref
  check_public_boundary.py --staged         staged paths and contents (pre-commit hook)
  check_public_boundary.py --message FILE   one commit message (commit-msg hook)
  check_public_boundary.py --install-hooks  install the two local hooks above
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

FORBIDDEN_DIRS = frozenset({".sageox", ".claude"})

# Each pattern is written so that this file does not match itself.
PATTERNS = (
    # Any SageOx link other than the bare home page, on any subdomain.
    re.compile(r"(?:[\w-]+\.)*sageox\.ai/[\w%-]", re.IGNORECASE),
    re.compile(r"sageox-session\s*:", re.IGNORECASE),
    re.compile(r"co-authored-by:\s*sageox", re.IGNORECASE),
    # SageOx identifiers: sessions, recordings, repos, attachments, conversations, teams, users.
    re.compile(r"\b(?:ses|rec|repo|att|cnv)_[0-9a-f]{8}-?[0-9a-f]{4}", re.IGNORECASE),
    re.compile(r"\bteam_(?=[a-z0-9]*\d)[a-z0-9]{8,}\b", re.IGNORECASE),
    re.compile(r"\busr_(?=[a-z0-9]*\d)[a-z0-9]{16,}\b", re.IGNORECASE),
    # Credentials.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
)

EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
PUBLIC_EMAIL_DOMAINS = re.compile(
    r"(?:^|\.)(?:example|test|invalid|localhost)$"
    r"|^example\.(?:com|net|org)$"
    r"|^users\.noreply\.github\.com$"
    r"|^anthropic\.com$",
    re.IGNORECASE,
)

HOOK_MARKER = "# auggie public-boundary guard"
HOOKS = {
    "pre-commit": "--staged",
    "commit-msg": '--message "$1"',
}


def load_denylist() -> list[str]:
    configured = os.environ.get("AUGGIE_PUBLIC_DENYLIST")
    path = Path(configured) if configured else Path.home() / ".config/auggie/public-denylist.txt"
    if not path.is_file():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def path_problems(paths: Iterable[str]) -> list[str]:
    problems = []
    for path in paths:
        parts = PurePosixPath(path).parts
        if FORBIDDEN_DIRS.intersection(parts):
            problems.append(f"{path}: maintainer-local state must not be committed")
        elif re.fullmatch(r"\.env(?:\..+)?", parts[-1]) and parts[-1] != ".env.example":
            problems.append(f"{path}: environment files must not be committed")
    return problems


def text_problems(where: str, text: str, terms: Sequence[str]) -> list[str]:
    problems = [
        f"{where}: matches private pattern {pattern.pattern!r}"
        for pattern in PATTERNS
        if pattern.search(text)
    ]
    problems += [
        f"{where}: email address at non-public domain {domain!r}"
        for domain in sorted({match.lower() for match in EMAIL.findall(text)})
        if not PUBLIC_EMAIL_DOMAINS.search(domain)
    ]
    lowered = text.lower()
    problems += [
        f"{where}: contains private term #{index} from the denylist"
        for index, term in enumerate(terms, start=1)
        if term.lower() in lowered
    ]
    return problems


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _blobs(shas: Sequence[str]) -> dict[str, bytes]:
    """Read many objects at once; returns the blobs among them."""
    if not shas:
        return {}
    output = subprocess.run(
        ["git", "cat-file", "--batch"],
        input="\n".join(shas).encode() + b"\n",
        check=True,
        capture_output=True,
    ).stdout
    blobs: dict[str, bytes] = {}
    position = 0
    while position < len(output):
        header_end = output.index(b"\n", position)
        sha, kind, size = output[position:header_end].decode().split()
        start = header_end + 1
        if kind == "blob":
            blobs[sha] = output[start : start + int(size)]
        position = start + int(size) + 1
    return blobs


def check_history(terms: Sequence[str]) -> list[str]:
    paths: dict[str, str] = {}
    for line in _git("rev-list", "--objects", "--all").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths.setdefault(sha, path)
    problems = path_problems(sorted(set(paths.values())))
    for sha, content in _blobs(list(paths)).items():
        where = f"{paths[sha]} ({sha[:7]})"
        problems += text_problems(where, content.decode("utf-8", errors="replace"), terms)
    log = _git("log", "--all", "--format=%H%x00%ae %ce%x00%B%x1e")
    for record in filter(str.strip, log.split("\x1e")):
        sha, _, rest = record.strip().partition("\0")
        emails, _, message = rest.partition("\0")
        problems += text_problems(f"commit {sha[:7]} authorship", emails, terms)
        problems += text_problems(f"commit {sha[:7]} message", message, terms)
    return problems


def check_staged(terms: Sequence[str]) -> list[str]:
    staged = _git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").split("\0")
    staged = [path for path in staged if path]
    problems = path_problems(staged)
    for path in staged:
        problems += text_problems(path, _git("show", f":{path}"), terms)
    return problems


def check_message(path: Path, terms: Sequence[str]) -> list[str]:
    return text_problems("commit message", path.read_text(encoding="utf-8"), terms)


def install_hooks() -> list[str]:
    hooks_dir = Path(_git("rev-parse", "--git-path", "hooks").strip())
    script = 'exec python3 "$(git rev-parse --show-toplevel)/scripts/check_public_boundary.py"'
    problems = []
    for name, arguments in HOOKS.items():
        hook = hooks_dir / name
        if hook.exists() and HOOK_MARKER not in hook.read_text(encoding="utf-8"):
            problems.append(f"{hook}: exists and is not ours; add the check to it by hand")
            continue
        hook.write_text(f"#!/bin/sh\n{HOOK_MARKER}\n{script} {arguments}\n", encoding="utf-8")
        hook.chmod(0o755)
        print(f"installed {hook}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--history", action="store_true")
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--message", type=Path)
    mode.add_argument("--install-hooks", action="store_true")
    args = parser.parse_args(argv)

    terms = load_denylist()
    if args.install_hooks:
        problems = install_hooks()
    elif args.history:
        problems = check_history(terms)
    elif args.staged:
        problems = check_staged(terms)
    else:
        problems = check_message(args.message, terms)

    for problem in problems:
        print(f"public-boundary: {problem}", file=sys.stderr)
    if problems:
        print("public-boundary: blocked. See ADR 0010 and ADR 0011.", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
