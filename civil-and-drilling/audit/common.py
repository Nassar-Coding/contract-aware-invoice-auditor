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


# A signature line establishes one of three things (FD01; DDS Cl.15 p5, Cl.37 p8, Sch 5 p24; CW Cl.46-47 p8, P22 p14):
#  signed   - a personal name in the form the records use (initials and surname, or given name and surname);
#  unsigned - the line is missing, a placeholder (underscores/blank), or says in words that nobody signed;
#  unknown  - any other text ('??', 'illegible', digits ...): it does not establish approval, and it does not establish
#             its absence either. Arbitrary nonempty text is never promoted to approval.
NON_SIGNING_WORDS = {"unsigned", "not", "no", "none", "nil", "n/a", "na", "pending", "awaiting", "tbc", "tba", "void",
                     "refused", "missing", "absent", "blank", "declined", "withheld", "outstanding"}
_NAME_WORD = r"[A-Z][a-z]+(?:['’-][A-Z]?[a-z]+)*"
SIGNATURE_NAME = re.compile(rf"^(?:(?:[A-Z]\.\s?)+|{_NAME_WORD}\s)(?:{_NAME_WORD}|[A-Z][a-z]*-[A-Z][a-z]+)(?:\s{_NAME_WORD})*$")


def signature_state(value: str | None) -> str:
    """'signed', 'unsigned' or 'unknown' for the text on a signature line (None: the line is missing)."""
    if value is None or PLACEHOLDER.match(value):
        return "unsigned"
    words = re.findall(r"[A-Za-z/]+", value.lower())
    if words and any(w in NON_SIGNING_WORDS for w in words) and not SIGNATURE_NAME.match(value.strip()):
        return "unsigned"
    if SIGNATURE_NAME.match(value.strip()) and not any(w in NON_SIGNING_WORDS for w in words):
        return "signed"
    return "unknown"


def is_signature(value: str | None) -> bool:
    return signature_state(value) == "signed"
