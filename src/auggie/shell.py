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
from auggie.external import require_external_calls
from auggie.judgment.rules import SERVICES
from auggie.learning import learn_view
from auggie.pipeline import read_proof_pages, reads_listing, record_page, record_snapshot
from auggie.queries import (
    account_ids,
    accounts_view,
    brief_view,
    find_view,
    hiring_view,
    proofs_view,
    stack_view,
)
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
  hiring [<account>]            each account's newest listing read, counted by your families
  proofs                        each proof in your strategy, and whether a draft may quote it
  learn                         what each delivery led to, by play, pain, proof and criterion
reading (asks before any paid or external call)
  fetch <account> <url>         read one page and record it as evidence
  hiring <account> --read       read the account's careers listing now, free
  proofs --read                 read each proof's page through Treg and record it
  research <account>            research from the account's prospect file
  research --hiring [--again]   read listings free, then research the accounts hiring
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


def _hiring(session: Session, args: list[str]) -> None:
    if session.strategy is None:
        raise ValueError("start the shell with --strategy-repo and --strategy-path to use hiring")
    read = "--read" in args
    rest = [arg for arg in args if arg != "--read"]
    if len(rest) > 1 or (read and not rest):
        raise ValueError("usage: hiring [<account>] [--read]")
    account_id = rest[0] if rest else None
    if read and account_id is not None and not _read_listing(session, account_id):
        return
    rows = hiring_view(session.store, session.strategy.strategy, account_id, session.prospects)
    for line in [line for row in rows for line in render_hiring(row)] or ["no listing read yet"]:
        session.out(line)


def _read_listing(session: Session, account_id: str) -> bool:
    """Read one account's careers listing now and record the snapshot, after asking if live."""
    prospect = session.prospects.get(account_id)
    listing = prospect.careers_listing if prospect else None
    if prospect is None or listing is None:
        raise ValueError(f"no prospect file names a careers listing for {account_id!r}")
    reader = session.listings()
    if reader is None:
        raise ValueError("this session has no way to read listings")
    if session.live:
        require_external_calls(session.store.scope)  # refused before asking
        if not _yes(session, f"the careers listing on {listing.service}, free; go ahead?"):
            session.out("nothing read")
            return False
    run_id = operate.new_run_id(operate.utc_now())
    snapshot = record_snapshot(
        session.store,
        reader=reader,
        account=prospect,
        snapshot_id=f"snap-{prospect.key}-{run_id}",
        run_id=run_id,
    )
    session.out(f"recorded  {snapshot.snapshot_id} {snapshot.status} {listing.url}")
    return True


def _learn(session: Session, args: list[str]) -> None:
    if args:
        raise ValueError("usage: learn")
    for line in render_learn(learn_view(session.store)):
        session.out(line)


def _proofs(session: Session, args: list[str]) -> None:
    if session.strategy is None:
        raise ValueError("start the shell with --strategy-repo and --strategy-path to use proofs")
    if [arg for arg in args if arg != "--read"]:
        raise ValueError("usage: proofs [--read]")
    if "--read" in args and not _read_proofs(session, session.strategy):
        return
    # A rehearsal judges as of when its responses were recorded, as research does.
    at = (session.research_clock or session.now)()
    rows = proofs_view(session.store, session.strategy.strategy, at)
    for line in [line for row in rows for line in render_proof(row)] or ["no proofs"]:
        session.out(line)


def _read_proofs(session: Session, strategy: LoadedStrategy) -> bool:
    """Read each proof's page and record it, after asking if live."""
    pages = {str(proof.url) for proof in strategy.strategy.proofs.by_id.values()}
    if not pages:
        raise ValueError("the strategy holds no proofs")
    acquisition = session.acquisition()
    if session.live:
        require_external_calls(session.store.scope)  # refused before asking
        if not _yes(session, f"{len(pages)} pages through Treg, {_cost(len(pages))}; go ahead?"):
            session.out("nothing read")
            return False
    run_id = operate.new_run_id(operate.utc_now())
    verdicts = read_proof_pages(
        session.store, strategy=strategy, acquisition=acquisition, run_id=run_id, now=session.now
    )
    for verdict in verdicts:
        evidence = verdict.evidence
        reason = "" if verdict.admitted else f" ({verdict.rejected_for})"
        session.out(
            f"recorded  {evidence.evidence_id} {evidence.status}{reason} {evidence.source_url}"
        )
    return True


# Reading


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'' if n == 1 else 's'}"


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
    if "--hiring" in args:
        rest = [arg for arg in args if arg not in ("--hiring", "--again")]
        if rest:
            raise ValueError("usage: research --hiring [--again]")
        _research_hiring(session, again="--again" in args)
        return
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


def _research_hiring(session: Session, *, again: bool) -> None:
    """Screen every prospect by hiring, free, then research the ones that passed under one cap,
    asking once before each part when live, then offer to review the drafts (ADR 0030)."""
    from auggie.acquisition.treg import CEILING_MICRO_USD

    strategy = session.strategy
    if strategy is None:
        raise ValueError("start the shell with --strategy-repo and --strategy-path to research")
    prospects = list(session.prospects.values())
    reader = session.listings()
    if reader is None:
        raise ValueError("this session has no way to read listings")
    operate.screen_criteria(strategy)  # refused before asking, if there is nothing to screen by
    listed = len(operate.to_screen(session.store, prospects, again=again))
    if session.live and listed:
        require_external_calls(session.store.scope)  # refused before asking
        if not _yes(session, f"read {_count(listed, 'careers listing')}, free; go ahead?"):
            session.out("nothing read")
            return
    screened = operate.screen(
        session.store,
        strategy=strategy,
        prospects=prospects,
        reader=reader,
        run_id=operate.new_run_id(operate.utc_now()),
        again=again,
    )
    for found in screened:
        session.out(f"{found.status:<12} {found.account_id:<28} {found.detail}")
    passed = {found.account_id for found in screened if found.status == "passed"}
    chosen = [p for p in prospects if p.account_id in passed]
    session.out(f"{len(chosen)} of {len(screened)} passed the screen")
    if not chosen:
        return
    acquisition = session.acquisition()
    budget = getattr(acquisition, "budget", None)
    if session.live:
        pages = sum(k in p.sources for p in chosen for k in strategy.strategy.sources)
        cap = (
            f", under this command's limit of ${budget.limit_micro_usd / 1_000_000:.3f}"
            if budget
            else ""
        )
        if not _yes(
            session,
            f"research {_count(len(chosen), 'account')}: {_count(pages, 'page')} through Treg, "
            f"{_cost(pages)}{cap}; go ahead?",
        ):
            session.out("nothing researched")
            return
    lines = operate.research_list(
        session.store,
        strategy=strategy,
        prospects=chosen,
        acquisition=acquisition,
        operator=session.me,
        budget=budget,
        page_ceiling_micro_usd=CEILING_MICRO_USD,
        again=again,
        now=session.research_clock or operate.utc_now,
        listings=session.listings(),
    )
    for line in lines:
        session.out(f"{line.status:<12} {line.account_id:<28} {line.detail}")
    waiting = operate.waiting_drafts(session.store)
    if waiting and _yes(session, f"{_count(len(waiting), 'draft')} waiting; review them now?"):
        operate.review_waiting(session.store, reviewer=session.me, ask=session.ask, out=session.out)


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
    "hiring": _hiring,
    "proofs": _proofs,
    "learn": _learn,
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


def render_hiring(row: Mapping[str, Any]) -> list[str]:
    head = (
        f"{row['account_id']}: {SERVICES[row['service']]} listing read {_date(row['read_at'])} "
        f"({row['snapshot_id']}), {row['status']}"
    )
    if row["status"] == "failed":
        return [f"{head}: {row['failure_reason']} Nothing is counted."]
    if row["status"] == "inconsistent":
        return [f"{head}: its replies did not agree, so nothing is counted."]
    bound = "at least " if row["status"] == "partial" else ""
    if row["relevant"] is None:
        return [f"{head}: {bound}{row['roles']} roles; the strategy defines no hiring families"]
    # A partial read is a lower bound, and so is every count taken from it.
    lines = [
        f"{head}: {bound}{row['roles']} roles, {bound}{row['relevant']} of them in your families"
    ]
    for family in row["families"]:
        levels = ", ".join(f"{level} {n}" for level, n in family["levels"].items())
        lines.append(f"    {family['label']}: {bound}{family['roles']} ({levels})")
        lines.extend(f"      {e['title']} {e['url']}" for e in family["examples"])
    for pattern in row["patterns"]:
        lines.append(
            f"    holds (inference): {pattern['label']} in {pattern['family_label']}: "
            f"{pattern['reason']} [{', '.join(pattern['snapshots'])}]"
        )
        lines.extend(f"      {e['title']} {e['url']}" for e in pattern["examples"])
    return lines


def render_learn(view: Mapping[str, Any]) -> list[str]:
    lines = [
        f"{view['deliveries']} deliveries. A rate is positive deliveries over settled ones "
        "(positive or negative); deliveries still waiting do not count."
    ]
    for strategy in view["strategies"]:
        lines.append(f"under {strategy['strategy']}: {strategy['deliveries']} deliveries")
        lines.extend(_learned(strategy))
    return lines


def _learned(view: Mapping[str, Any]) -> list[str]:
    lines = []
    for group in ("plays", "pains", "proofs", "criteria"):
        rows = view[group]
        if not rows:
            continue
        lines.append(f" {group}")
        for row in rows:
            rate = f"{row['rate']:.0%}" if row["rate"] is not None else "none settled"
            lines.append(
                f"  {row['key']:<28} {row['delivered']} delivered: {row['positive']} positive, "
                f"{row['negative']} negative ({row['opted_out']} opted out), {row['bounced']} "
                f"bounced, {row['waiting']} waiting; rate {rate}"
            )
    return lines


def render_proof(row: Mapping[str, Any]) -> list[str]:
    head = f"{row['proof']} ({row['customer']}, {row['kind']}): "
    if row["usable"]:
        head += f"usable, a vendor claim read {_date(row['read_at'])} ({row['evidence_id']})"
    else:
        head += f"not usable: {row['problem']}"
    lines = [head, f'    "{row["claim"]}"', f"    {row['url']}"]
    if row["caveat"]:
        lines.append(f"    caveat: {row['caveat']}")
    return lines


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
