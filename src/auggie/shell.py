"""The research shell: ask about a scope's accounts, read one more page, run the flow (ADR 0025).

One command at a time, each run to its end, and nothing in the background.
Answers come from the scope's records. Every paid or external call says what
it will call and the most it can spend, and waits for a yes. Approvals and
edits are the person's own, made here at their terminal.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast, get_args

from auggie import operate
from auggie.accounts import Account
from auggie.acquisition import Acquisition
from auggie.acquisition.careers import ListingReader
from auggie.acquisition.treg import CEILING_MICRO_USD
from auggie.contracts import sha256_digest
from auggie.contracts.events import OutcomeKind
from auggie.delivery import Sender
from auggie.pipeline import reads_listing, record_page
from auggie.queries import account_ids, accounts_view, brief_view, find_view, stack_view
from auggie.store import Store
from auggie.strategy import LoadedStrategy

Print = Callable[[str], None]
Found = Literal["sent", "not-sent"]
Ask = Callable[[str], str]

PROMPT = "auggie> "

HELP = """\
questions (answers from the records; nothing is read or spent)
  accounts                      every account, its judgment and its drafts
  brief <account>               the judgment, its findings and citations, evidence, drafts
  find [<account>] <words>      stored excerpts that hold these words
  stack [<account>]             where admitted pages name the strategy's technologies
reading (asks before any paid call)
  fetch <account> <url>         read one page and record it as evidence
  research <account>            research from the account's prospect file
the flow (yours alone)
  review <draft>   revise <draft>   deliver <draft>
  outcome <draft> <kind>        record what happened: {kinds}
  resolve <draft> sent|not-sent record what your Sent folder showed for an unknown send
  help   quit"""


@dataclass
class Session:
    """What one shell session works with. ``acquisition``, ``listings`` and ``sender`` are built
    per command, so each command is its own run with its own spending limit."""

    store: Store
    me: str
    ask: Ask
    out: Print
    acquisition: Callable[[], Acquisition]
    live: bool
    """Whether reads go to a paid provider (Treg) and the listing services, rather than
    recorded responses."""
    listings: Callable[[], ListingReader | None] = lambda: None
    prospects: Mapping[str, Account] = field(default_factory=dict)
    strategy: LoadedStrategy | None = None
    sender: Callable[[], Sender | None] = lambda: None
    edit: Callable[[str], str] = lambda text: text
    now: Callable[[], datetime] = operate.utc_now
    research_clock: Callable[[], datetime] | None = None


def run(session: Session) -> int:
    """Read and run commands until ``quit`` or the end of input."""
    session.out("auggie shell for " + session.store.scope.customer_id + ". Type help.")
    while True:
        try:
            line = session.ask(PROMPT)
        except EOFError:
            session.out("")
            return 0
        try:
            words = shlex.split(line)
        except ValueError as error:
            session.out(f"stopped   {error}")
            continue
        if not words:
            continue
        name, args = words[0], words[1:]
        if name in ("quit", "exit"):
            return 0
        command = COMMANDS.get(name)
        if command is None:
            session.out(f"unknown command {name!r}; type help")
            continue
        try:
            command(session, args)
        except Exception as error:
            session.out(f"stopped   {type(error).__name__}: {error}")


def _yes(session: Session, question: str) -> bool:
    return session.ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _need(args: list[str], count: int, usage: str) -> None:
    if len(args) != count:
        raise ValueError(f"usage: {usage}")


def _known(session: Session, account_id: str) -> None:
    if account_id not in account_ids(session.store, session.prospects):
        raise ValueError(f"no account {account_id!r} here; add its prospect file first")


# Questions


def _accounts(session: Session, args: list[str]) -> None:
    _need(args, 0, "accounts")
    rows = accounts_view(session.store, session.prospects)
    if not rows:
        session.out("no accounts yet; add prospect files, then research one")
    for row in rows:
        session.out(render_account(row))


def _brief(session: Session, args: list[str]) -> None:
    _need(args, 1, "brief <account>")
    for line in render_brief(brief_view(session.store, args[0], session.prospects)):
        session.out(line)


def _find(session: Session, args: list[str]) -> None:
    if not args:
        raise ValueError("usage: find [<account>] <words>")
    account = None
    if len(args) > 1 and args[0] in account_ids(session.store, session.prospects):
        account, args = args[0], args[1:]
    rows = find_view(session.store, " ".join(args), account)
    if not rows:
        session.out("no stored excerpt holds those words")
    for row in rows:
        session.out(render_find(row))


def _stack(session: Session, args: list[str]) -> None:
    if session.strategy is None:
        raise ValueError("start the shell with --strategy-repo and --strategy-path to use stack")
    if len(args) > 1:
        raise ValueError("usage: stack [<account>]")
    if session.strategy.strategy.signals.stack is None:
        session.out("the strategy defines no stack terms")
        return
    rows = stack_view(session.store, session.strategy.strategy, args[0] if args else None)
    if not rows:
        session.out("no admitted page names the strategy's technologies")
    for row in rows:
        session.out(render_stack(row))


# Reading


def _cost(pages: int) -> str:
    return f"at most ${pages * CEILING_MICRO_USD / 1_000_000:.3f}"


def _fetch(session: Session, args: list[str]) -> None:
    _need(args, 2, "fetch <account> <url>")
    account_id, url = args
    _known(session, account_id)
    acquisition = session.acquisition()  # refuses a scope without external calls, before asking
    if session.live and not _yes(session, f"one page through Treg, {_cost(1)}; go ahead?"):
        session.out("nothing read")
        return
    run_id = operate.new_run_id(operate.utc_now())
    prospect = session.prospects.get(account_id)
    # The prospect's short key keeps the ID readable; otherwise a digest keeps it bounded.
    key = prospect.key if prospect else sha256_digest(account_id).removeprefix("sha256:")[:12]
    verdict = record_page(
        session.store,
        acquisition=acquisition,
        account_id=account_id,
        evidence_id=f"ev-{key}-page-{run_id}",
        url=url,
        run_id=run_id,
        now=session.now,
    )
    evidence = verdict.evidence
    reason = "" if verdict.admitted else f" ({verdict.rejected_for})"
    session.out(f"recorded  {evidence.evidence_id} {evidence.status}{reason} {evidence.source_url}")


def _research(session: Session, args: list[str]) -> None:
    _need(args, 1, "research <account>")
    prospect = session.prospects.get(args[0])
    if prospect is None:
        raise ValueError(f"no prospect file for {args[0]!r}")
    if session.strategy is None:
        raise ValueError("start the shell with --strategy-repo and --strategy-path to research")
    kinds = [k for k in session.strategy.strategy.sources if k in prospect.sources]
    acquisition = session.acquisition()
    listing = prospect.careers_listing
    reads = reads_listing(session.strategy, prospect) and listing is not None
    also = f", and the careers listing on {listing.service}, free" if reads and listing else ""
    if session.live and not _yes(
        session, f"{len(kinds)} pages through Treg, {_cost(len(kinds))}{also}; go ahead?"
    ):
        session.out("nothing read")
        return
    result = operate.research_prospect(
        session.store,
        strategy=session.strategy,
        prospect=prospect,
        acquisition=acquisition,
        operator=session.me,
        now=session.research_clock or operate.utc_now,
        listings=session.listings(),
    )
    decision = result.decision
    session.out(f"judged    {decision.account_id} {decision.outcome} ({decision.decision_id})")
    if result.draft is not None:
        session.out(f"draft     {result.draft.ref.artifact_id}: review it with review")
    else:
        session.out(f"no draft  {result.stopped}")


# The flow


def _review(session: Session, args: list[str]) -> None:
    _need(args, 1, "review <draft>")
    approvals = operate.review_draft(
        session.store, draft_id=args[0], reviewer=session.me, ask=session.ask, out=session.out
    )
    for approval in approvals:
        session.out(f"recorded  {approval.scope} {approval.decision} ({approval.approval_id})")


def _revise(session: Session, args: list[str]) -> None:
    _need(args, 1, "revise <draft>")
    revised = operate.revise_draft(
        session.store, draft_id=args[0], author=session.me, edit=session.edit, out=session.out
    )
    if revised is not None:
        session.out(f"revise    {args[0]} has a new version; review it with review")


def _deliver(session: Session, args: list[str]) -> None:
    _need(args, 1, "deliver <draft>")
    scope = session.store.scope
    if scope.execution.delivery == "live" and not _yes(
        session, f"send one email from your {scope.execution.platform} mailbox; go ahead?"
    ):
        session.out("nothing sent")
        return
    delivered = operate.deliver_draft(session.store, draft_id=args[0], sender=session.sender())
    again = "" if delivered.created else " (already delivered; nothing sent again)"
    event = delivered.event
    if event.type == "delivery.simulated":
        done = "simulated hand-off"
    elif event.attributes.get("resolved") is True:
        done = "recorded as sent, from what you found in your Sent folder"
    else:
        done = f"sent, receipt {event.attributes.get('receipt')}"
    session.out(f"deliver   {args[0]} {done}{again}")


def _outcome(session: Session, args: list[str]) -> None:
    _need(args, 2, "outcome <draft> <kind>")
    if args[1] not in get_args(OutcomeKind):
        raise ValueError(f"an outcome is one of {', '.join(get_args(OutcomeKind))}")
    operate.report_outcome(
        session.store,
        draft_id=args[0],
        outcome=cast(OutcomeKind, args[1]),
        reporter=session.me,
    )
    session.out(f"learn     {args[0]} {args[1]} recorded")


def _resolve(session: Session, args: list[str]) -> None:
    _need(args, 2, "resolve <draft> sent|not-sent")
    if args[1] not in ("sent", "not-sent"):
        raise ValueError("resolve a send as sent or not-sent")
    operate.resolve_delivery(
        session.store,
        draft_id=args[0],
        found=cast(Found, args[1]),
        reporter=session.me,
    )
    session.out(f"deliver   {args[0]} recorded as {args[1]}; it is never sent again")


def _help(session: Session, args: list[str]) -> None:
    session.out(HELP.format(kinds=", ".join(get_args(OutcomeKind))))


COMMANDS: dict[str, Callable[[Session, list[str]], None]] = {
    "help": _help,
    "accounts": _accounts,
    "brief": _brief,
    "find": _find,
    "stack": _stack,
    "fetch": _fetch,
    "research": _research,
    "review": _review,
    "revise": _revise,
    "deliver": _deliver,
    "outcome": _outcome,
    "resolve": _resolve,
}


# Text renderings of the views, shared with the single commands


def _date(value: Any) -> str:
    return f"{value:%Y-%m-%d}" if isinstance(value, datetime) else str(value)


def render_account(row: Mapping[str, Any]) -> str:
    judged = row["qualification"]
    verdict = f"{judged['outcome']} {_date(judged['decided_at'])}" if judged else "not researched"
    drafts = ", ".join(f"{d['draft_id']} ({d['delivery']})" for d in row["drafts"]) or "no drafts"
    name = f" {row['name']}" if row["name"] else ""
    return f"{row['account_id']}{name}: {verdict}; {row['evidence']} evidence; {drafts}"


def render_brief(view: Mapping[str, Any]) -> list[str]:
    name = f" ({view['name']})" if view["name"] else ""
    lines = [f"{view['account_id']}{name}"]
    judged = view["qualification"]
    if judged is None:
        lines.append("  not researched yet")
    else:
        lines.append(
            f"  judged {judged['outcome']} on {_date(judged['decided_at'])} under "
            f"{judged['strategy']} ({judged['decision_id']})"
        )
        for f in judged["findings"]:
            cited = f["evidence_ids"] + f["snapshot_ids"]
            cites = f" [{', '.join(cited)}]" if cited else ""
            found = f"{f['criterion']} ({f['basis']}): {f['rationale']}{cites}"
            lines.append(f"    {f['assessment']:<13} {found}")
    lines.append("  evidence")
    for e in view["evidence"]:
        read = _date(e["observed_at"])
        state = f"{e['status']}, {e['validation']}, read {read}"
        lines.append(f"    {e['evidence_id']} {state}: {e['source_url']}")
    lines.append("  drafts")
    for d in view["drafts"]:
        decided = (
            ", ".join(f"{a['scope']} {a['decision']}" for a in d["approvals"]) or "not reviewed"
        )
        lines.append(f"    {d['draft_id']}: {decided}; {d['delivery']}")
    return lines


def render_find(row: Mapping[str, Any]) -> str:
    state = "" if row["validation"] == "admitted" else f", {row['validation']}"
    return (
        f"{row['evidence_id']} ({row['account_id']}, read {_date(row['observed_at'])}{state}) "
        f'"{row["quote"]}"'
    )


def render_stack(row: Mapping[str, Any]) -> str:
    near = f' near "{row["near"]}"' if row["near"] else ""
    return (
        f"{row['label']} named{near} on {row['source_url']} ({row['evidence_id']}, read "
        f'{_date(row["observed_at"])}): "{row["quote"]}"'
    )


def as_json(view: Any) -> str:
    """A view as JSON for a coding agent. Dates become ISO 8601 text.

    No view exports a recipient's person record or its email address, which review shows.
    Quotes are page text with whitespace collapsed, and rationales are as stored; neither is
    filtered, so either may hold an address the page itself contains.
    """
    return json.dumps(
        view,
        indent=2,
        default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v),
    )
