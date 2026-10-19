"""Outcomes taken in from a CSV (ADR 0028): each row a draft, what happened and when,
previewed against the Store before anything is recorded."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Literal, cast, get_args

from auggie.contracts import Actor, RecordRef
from auggie.contracts.events import OutcomeKind
from auggie.pipeline import OutcomeReport, delivery_state, record_outcome
from auggie.store import Appended, RecordNotFound, Store

COLUMNS = ("draft", "outcome", "occurred_at")
MAX_ROWS = 1000
KINDS: tuple[str, ...] = get_args(OutcomeKind)

Status = Literal["new", "recorded", "refused"]


class OutcomesRefused(ValueError):
    """The list as a whole cannot be read: nothing in it is recorded."""


@dataclass(frozen=True)
class OutcomeRow:
    line: int
    draft_id: str
    outcome: str
    occurred_at: datetime | None
    status: Status = "new"
    problems: tuple[str, ...] = ()


def _when(text: str) -> datetime:
    """An ISO 8601 date (taken as midnight UTC) or a time with its zone."""
    if len(text) == 10:
        day = date.fromisoformat(text)
        return datetime(day.year, day.month, day.day, tzinfo=UTC)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("a time needs its zone, such as Z or +01:00")
    return moment.astimezone(UTC)


def read_outcomes(text: str) -> list[OutcomeRow]:
    """Each row of an outcomes CSV, checked on its own: the columns are exactly ``draft``,
    ``outcome`` and ``occurred_at``."""
    reader = csv.reader(io.StringIO(text.removeprefix("﻿")), strict=True)
    try:
        header = [cell.strip() for cell in next(reader)]
        if sorted(header) != sorted(COLUMNS):
            raise OutcomesRefused(f"the columns must be {', '.join(COLUMNS)}")
        rows: list[OutcomeRow] = []
        seen: dict[tuple[str, str, str], int] = {}
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            if len(rows) == MAX_ROWS:
                raise OutcomesRefused(f"the list has more than {MAX_ROWS} rows")
            line = reader.line_num
            if len(cells) != len(header):
                rows.append(
                    OutcomeRow(line, "", "", None, "refused", (f"{len(cells)} cells for 3",))
                )
                continue
            values = {c: v.strip() for c, v in zip(header, cells, strict=True)}
            problems = []
            if values["outcome"] not in KINDS:
                problems.append(f"outcome must be one of {', '.join(KINDS)}")
            moment = None
            try:
                moment = _when(values["occurred_at"])
            except (ValueError, OverflowError) as error:
                problems.append(f"occurred_at: {error}")
            # The same moment written in another zone is the same report.
            when = moment.isoformat() if moment else values["occurred_at"]
            key = (values["draft"], values["outcome"], when)
            first = seen.setdefault(key, line)
            if first != line:
                problems.append(f"repeats line {first}")
            status: Status = "refused" if problems else "new"
            rows.append(
                OutcomeRow(
                    line, values["draft"], values["outcome"], moment, status, tuple(problems)
                )
            )
    except StopIteration:
        raise OutcomesRefused("the list is empty") from None
    except csv.Error as error:
        raise OutcomesRefused(f"the list is not readable CSV: {error}") from None
    return rows


def check(
    rows: Sequence[OutcomeRow], store: Store, now: Callable[[], datetime]
) -> list[OutcomeRow]:
    """Each new row against the Store: the draft exists and was delivered, the outcome comes
    after its delivery and not after now, and it is not already recorded."""
    checked = []
    for row in rows:
        if row.status != "new" or row.occurred_at is None:
            checked.append(row)
            continue
        problem = _problem(store, row, row.occurred_at, now())
        if problem == "recorded":
            checked.append(replace(row, status="recorded"))
        elif problem is not None:
            checked.append(replace(row, status="refused", problems=(problem,)))
        else:
            checked.append(row)
    return checked


def _problem(store: Store, row: OutcomeRow, at: datetime, now: datetime) -> str | None:
    try:
        draft = store.latest_artifact(row.draft_id)
    except (RecordNotFound, ValueError):
        return "no such draft in this scope"
    if draft.recipient_id is None:
        return "a brief has no delivery"
    sent = delivery_state(store, row.draft_id, draft.recipient_id).sent
    if sent is None:
        return "never delivered"
    if at < sent.occurred_at:
        return f"before it was delivered, at {sent.occurred_at.isoformat()}"
    if at > now:
        return "in the future"
    artifact = RecordRef(kind="artifact", id=row.draft_id)
    for event in store.list_events(type="outcome.recorded"):
        if (
            artifact in event.refs
            and event.attributes.get("outcome") == row.outcome
            and event.occurred_at == at
        ):
            return "recorded"
    return None


def record(rows: Sequence[OutcomeRow], store: Store, reporter: str) -> list[Appended]:
    """Record each new row as the next report for its draft, unless an equal report, the same
    outcome at the same moment, is already recorded."""
    by = Actor(kind="human", id=reporter)
    recorded = []
    for row in rows:
        if row.status != "new" or row.occurred_at is None:
            continue
        artifact = RecordRef(kind="artifact", id=row.draft_id)
        reports = [e for e in store.list_events(type="outcome.recorded") if artifact in e.refs]
        if any(
            e.attributes.get("outcome") == row.outcome and e.occurred_at == row.occurred_at
            for e in reports
        ):
            continue  # recorded since the preview, or by an equivalent row
        earlier = len(reports)
        report = OutcomeReport(
            outcome=cast(OutcomeKind, row.outcome),
            reported_by=by,
            occurred_at=row.occurred_at,
            sequence=earlier + 1,
        )
        recorded.append(record_outcome(store, artifact_id=row.draft_id, report=report))
    return recorded


def describe(row: OutcomeRow) -> str:
    when = row.occurred_at.isoformat() if row.occurred_at else "-"
    detail = f" {'; '.join(row.problems)}" if row.problems else ""
    draft = row.draft_id or "-"
    outcome = row.outcome or "-"
    return f"line {row.line:<5} {draft:<40} {outcome:<15} {when} {row.status}{detail}"
