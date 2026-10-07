"""G3 shared core: calculation traces, rounding, check results and the per-line result (both contracts).

A Trace is a list of steps, each recording the operation, its operand(s), the result and the source it relies on.
tools/verify_g3.py replays every trace with its own arithmetic, so each amount's explanation is checked rather than
trusted. Only local entitlement is decided here; anything needing other lines or invoices is listed as a G4
dependency on the result, never applied.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ROUNDING = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}


def q(x: Decimal, mode: str) -> Decimal:
    return x.quantize(CENT, rounding=ROUNDING[mode])


class Trace:
    """Exact, replayable calculation steps. `value` after each step is the running result."""

    def __init__(self):
        self.steps: list[dict] = []
        self.value: Decimal | None = None

    def start(self, label: str, value: Decimal, source: str) -> Decimal:
        self.value = value
        self.steps.append({"op": "start", "label": label, "value": str(value), "source": source})
        return value

    def mul(self, label: str, factor: Decimal, source: str) -> Decimal:
        self.value = self.value * factor
        self.steps.append({"op": "mul", "label": label, "factor": str(factor), "value": str(self.value), "source": source})
        return self.value

    def div(self, label: str, divisor: Decimal, source: str) -> Decimal:
        self.value = self.value / divisor
        self.steps.append({"op": "div", "label": label, "divisor": str(divisor), "value": str(self.value), "source": source})
        return self.value

    def round(self, label: str, mode: str, source: str) -> Decimal:
        self.value = q(self.value, mode)
        self.steps.append({"op": "round", "label": label, "mode": mode, "value": str(self.value), "source": source})
        return self.value

    def note(self, label: str, source: str, **facts) -> None:
        self.steps.append({"op": "note", "label": label, "source": source, **{k: str(v) for k, v in facts.items()}})

    def amount(self, quantity: Decimal, rate: Decimal, source: str) -> Decimal:
        v = quantity * rate
        self.steps.append({"op": "amount", "label": "amount = quantity x rate", "quantity": str(quantity), "rate": str(rate),
                           "value": str(v), "source": source})
        return v

    def part(self, label: str, quantity: Decimal, rate: Decimal, source: str, mode: str | None = None) -> Decimal:
        """A separately priced part; with `mode`, a fraction of a cent is rounded in that mode (recorded as `round`)."""
        v = quantity * rate
        step = {"op": "part", "label": label, "quantity": str(quantity), "rate": str(rate), "value": str(v), "source": source}
        if mode:                            # stated in cents; `round` records a fraction of a cent actually rounded
            if v != q(v, mode):
                step["round"] = mode
            v = q(v, mode)
            step["value"] = str(v)
        self.steps.append(step)
        return v

    def total(self, label: str, source: str) -> Decimal:
        parts = [Decimal(s["value"]) for s in self.steps if s["op"] == "part"]
        v = sum(parts, Decimal("0"))
        self.steps.append({"op": "sum_parts", "label": label, "value": str(v), "source": source})
        return v


@dataclass(frozen=True)
class Inputs:
    """What G2 hands G3 besides the typed values: where the line, its header and its evidence document come
    from, and the fields of that document G2 could not establish (its unresolved queue). An engine never defaults a
    missing input: it records it with this provenance (drilling guidelines principle 3, check 12) and leaves any value
    that depends on it unresolved."""
    line_src: str | None = None             # "file:line" of the claim row
    header_src: str | None = None           # "file:line" of its application/invoice header
    doc_src: str | None = None              # path of the record/report the line cites
    doc_gaps: frozenset = frozenset()       # fields of that document in G2's unresolved queue ("Date", "A.Status", ...)
    unindexed_reports: bool = False         # some report file has no indexable Report number (G2 queue)
    doc_repeated: frozenset = frozenset()   # of those, fields written twice: G2 keeps a value, but which is right is open
    report_copies: tuple = ()               # other delivered files carrying the cited Report number (G2 indexes one)
    header_copies: tuple = ()               # "file:line" of each header row carrying the line's header number, when
    #                                         more than one does (G2's input assertion: ids unique)


def empty(v) -> bool:
    """A value G2 left empty: None (typed fields, unparsed evidence) or a blank string (untyped claim fields)."""
    return v is None or (isinstance(v, str) and not v.strip())


def headers_by_id(rows) -> tuple[dict, dict]:
    """(header number -> its row, for numbers one row carries; header number -> the "file:line" of each row, for numbers
    several rows carry). A line whose number several headers carry takes no fact from any of them: which one
    it belongs to is not established."""
    n = Counter(h.ident for h in rows)
    return ({h.ident: h for h in rows if n[h.ident] == 1},
            {k: tuple(f"{h.source.path}:{h.source.line}" for h in rows if h.ident == k) for k, c in n.items() if c > 1})


def result_keys(rows) -> list[str]:
    """The key of each claim line's result in a batch (and of its Inputs): its line_ref; a line with no line_ref, or with
    one another line repeats, by its source position (file:line) - so no line's result or provenance ever replaces
    another's (which of two lines sharing a reference is a duplicate charge is G4's, never decided here)."""
    n = Counter(r.values.get("line_ref") or None for r in rows)
    keys, seen = [], set()
    for r in rows:
        ref = r.values.get("line_ref") or None
        k = ref if ref is not None and n[ref] == 1 else f"{r.source.path}:{r.source.line}"
        if k in seen:
            k = f"{k}#{len(keys) + 1}"
        seen.add(k)
        keys.append(k)
    return keys


@dataclass
class Check:
    check: str                  # identity | term | window | period | unit | evidence | identification | quantity | rate | arithmetic | status
    status: str                 # pass | finding | unresolved | n/a
    rule: str
    clause: str
    finding: str | None = None
    detail: str = ""


@dataclass
class LineResult:
    contract: str
    line_ref: str
    code: str
    family: str | None = None             # the quantity route the engine valued it under (spec/g3_code_families.yaml)
    checks: list[Check] = field(default_factory=list)
    unit_rate: Decimal | None = None
    rates: dict = field(default_factory=dict)   # label -> contract rate under each admissible alternative the rate check
    #                                             formed, whatever the line's payability ("" when single; empty: none formed)
    allowed_quantity: Decimal | None = None
    amount: Decimal | None = None
    payable: bool | None = None
    amount_status: str = "determined"      # determined | not_payable | alternatives | conditional | deferred | unresolved
    reasons: list[str] = field(default_factory=list)
    alternatives: dict = field(default_factory=dict)   # "dim:value|dim:value" -> {unit_rate, allowed_quantity, amount, trace}
    conditions: list = field(default_factory=list)     # {dimension, owner, basis}: why the value is not single, and who decides
    g4_dependencies: list[str] = field(default_factory=list)
    readings: list[str] = field(default_factory=list)   # decisions / question readings applied
    trace: list[dict] = field(default_factory=list)
    ctx: str | None = None

    @property
    def findings(self) -> list[str]:
        return sorted({c.finding for c in self.checks if c.status == "finding" and c.finding})

    @property
    def unresolved(self) -> list[str]:
        """Findings that cannot be established without information G3 does not have (G4 state, unsupplied documents)."""
        return sorted({c.finding for c in self.checks if c.status == "unresolved" and c.finding})

    def condition(self, dimension: str, owner: str, basis: str) -> None:
        if not any(c["dimension"] == dimension for c in self.conditions):
            self.conditions.append({"dimension": dimension, "owner": owner, "basis": basis})

    def add(self, check, status, rule, clause, finding=None, detail=""):
        self.checks.append(Check(check, status, rule, clause, finding, str(detail)))

    def input_gap(self, field: str, status: str, finding: str, clause: str, why: str, src: str | None) -> None:
        """An input G2 left empty or could not establish, recorded with its provenance - never replaced by a default."""
        self.add("input", status, "G3-IN", clause, finding, f"{field}: {why} [{src or 'source not given'}]")

    def to_json(self) -> dict:
        s = lambda v: None if v is None else str(v)  # noqa: E731
        return {"contract": self.contract, "line_ref": self.line_ref, "code": self.code, "family": self.family, "unit_rate": s(self.unit_rate),
                "rates": {k: s(v) for k, v in self.rates.items()},
                "allowed_quantity": s(self.allowed_quantity), "amount": s(self.amount), "payable": self.payable,
                "amount_status": self.amount_status, "findings": self.findings, "unresolved": self.unresolved,
                "reasons": self.reasons, "conditions": self.conditions,
                "alternatives": {k: {kk: s(vv) if isinstance(vv, Decimal) else vv for kk, vv in v.items()} for k, v in self.alternatives.items()},
                "g4_dependencies": self.g4_dependencies, "readings": self.readings,
                "checks": [c.__dict__ for c in self.checks], "trace": self.trace, "ctx": self.ctx}
