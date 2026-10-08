"""Shared G2 primitives: provenance, exact parsing, the unresolved queue and the conflict list.

Rules: money and quantities enter as exact Decimal from the original string; a blank stays None
(never zero); every required field that cannot be established is recorded as an Unresolved entry with its
source location and reason; contradictions inside the evidence are recorded as Conflict entries. Nothing
in this package prices, applies an entitlement rule, classifies or totals anything.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import snapshot  # noqa: E402
import spec_lib  # noqa: E402

SNAPSHOT = Path(os.environ.get("INVOICE_SNAPSHOT", snapshot.DEFAULT_SNAPSHOT))
PLACEHOLDER = re.compile(r"^[_\s]*$")


@dataclass(frozen=True)
class Source:
    """Where a value came from: snapshot-relative file, 1-based line number, raw text of that line."""
    path: str
    line: int
    raw: str


@dataclass
class Unresolved:
    """A required field that could not be established. Never replaced by a default."""
    kind: str
    ident: str
    field: str
    reason: str
    source: Source | None = None

    ctx: str | None = None                      # run context id (audit.provenance)

@dataclass
class Conflict:
    """Two statements in the evidence that disagree. Recorded, never reconciled silently."""
    kind: str
    ident: str
    check: str
    detail: str

    ctx: str | None = None                      # run context id (audit.provenance)

@dataclass
class Queue:
    items: list[Unresolved] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    def add(self, kind, ident, fld, reason, source=None):
        self.items.append(Unresolved(kind, ident, fld, reason, source))
        return None

    def conflict(self, kind, ident, check, detail):
        self.conflicts.append(Conflict(kind, ident, check, detail))

    def has(self, ident, fld) -> bool:
        return any(u.ident == ident and u.field == fld for u in self.items)


def dec(raw: str | None) -> Decimal | None:
    """Exact Decimal from a printed string; None for blank. Raises on garbage (the caller queues it)."""
    if raw is None or raw.strip() == "":
        return None
    v = Decimal(raw.replace(",", "").strip())
    if not v.is_finite():
        raise InvalidOperation(raw)
    return v


def iso_date(raw: str) -> dt.date:
    return dt.date.fromisoformat(raw.strip())


def dmy_mon(raw: str) -> dt.date:
    return dt.datetime.strptime(raw.strip(), "%d-%b-%Y").date()


def dmy_slash(raw: str) -> dt.date:
    return dt.datetime.strptime(raw.strip(), "%d/%m/%Y").date()


def is_signature(value: str | None) -> bool:
    return value is not None and not PLACEHOLDER.match(value)
