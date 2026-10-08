"""FD07 closure, DDS side: the PD-210 allocation domain is continuous.

No source fixes a metre granularity for placing a partial quantity across depth bands (DDS Cl.23 p6, R3 p14, 25A p35,
Sch 2 p17; Cl.17-18 fix cents, not metres), so a nondegenerate domain is stated symbolically - no step, no count, not
enumerable, not exhaustive, unresolved, owner G5 - with exact amount bounds, and its traces are witnesses. A domain the
constraints reduce to one allocation is that allocation alone. Every fixture runs the production G2 parser on the DDS-S72
packet (masked signatures restored by g3_case_compare.unmask), G3 and the output serialization (LineResult.to_json);
the expected values below are the FD07 closure tables, and every witness is replayed with independent arithmetic."""
import copy
import json
import re
from decimal import ROUND_HALF_EVEN, Decimal

import pytest

import g3_case_compare as gcc
import verify_g3 as vg

RATE = {"1": Decimal("42.35"), "2": Decimal("58.15")}          # Schedule 2 (p17), USD per metre
READER = gcc.VOCAB_V3["DDS"]                                   # the findings vocabulary of the independent readers
BOTH = {"depths_differ_from_quantity", "band_crossing_not_split"}


def fixture(q: str, amount: str, depths: tuple[str, str] | None = None, rate: str | None = None):
    """DDS-S72 with only the charged quantity and claim amount (and, for C, the report and charge depths together;
    for the billing variation, the stated rate) changed; every other field and all evidence as in the packet."""
    c = copy.deepcopy(gcc.load_cases()["DDS-S72"])
    c["line"]["quantity"], c["line"]["amount"] = q, amount
    if rate is not None:
        c["line"]["unit_rate"] = rate
    if depths:
        c["line"]["depth_from_m"], c["line"]["depth_to_m"] = depths
        c["report"] = c["report"].replace("Depth start (m MD): 1450", f"Depth start (m MD): {depths[0]}") \
                                 .replace("Depth end (m MD): 1550", f"Depth end (m MD): {depths[1]}")
        assert f"Depth start (m MD): {depths[0]}" in c["report"] and f"Depth end (m MD): {depths[1]}" in c["report"]
    return gcc.engine_result(c)


def serialized(r) -> dict:
    return json.loads(json.dumps(r.to_json(), default=str))


def domain(r) -> dict | None:
    return next((c["domain"] for c in serialized(r)["conditions"] if c.get("domain")), None)


def cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


# test: (quantity spellings, claim amount, depths, allowed, per-band domain or None for the singleton, bounds or value,
#        findings in the readers' vocabulary)
FIXTURES = {
    "A": (["98"], "4150.30", None, "98", [("48", "50"), ("48", "50")], ("4908.70", "4940.30"), BOTH),
    "A-spelling": (["98", "98.0", "98.00"], "4150.30", None, "98", [("48", "50"), ("48", "50")], ("4908.70", "4940.30"), BOTH),
    "B": (["98.5", "98.50"], "4171.48", None, "98.5", [("48.5", "50"), ("48.5", "50")], ("4937.77", "4961.48"), BOTH),
    "C": (["2.2"], "93.17", ("1499", "1502"), "2.2", [("0.2", "1"), ("1.2", "2")], ("112.13", "124.77"), BOTH),
    "D": (["100"], "4235.00", None, "100", None, "5025.00", {"band_crossing_not_split"}),
    "E": (["101"], "4277.35", None, "101", [("50", "51"), ("50", "51")], ("5067.35", "5083.15"), BOTH),
    "F": (["101.01"], "4277.77", None, "100", None, "5025.00", BOTH | {"quantity_above_report"}),
}


def continuous_closure(r, allowed: str, parts: list, bounds: tuple) -> None:
    """The continuous-domain semantics for a nondegenerate domain, on the serialized result."""
    s = serialized(r)
    assert (s["amount"], s["unit_rate"], s["amount_status"], s["payable"]) == (None, None, "conditional", True), s["amount"]
    assert Decimal(s["allowed_quantity"]) == Decimal(allowed)
    dom = domain(r)
    assert dom is not None, "no allocation domain stated"
    assert {k: dom.get(k) for k in ("mode", "step_m", "count", "enumerable", "exhaustive", "state", "owner")} == {
        "mode": "continuous", "step_m": None, "count": None, "enumerable": False, "exhaustive": False,
        "state": "unresolved", "owner": "G5"}, f"domain claimed finite or complete: {dom}"
    assert "not_listed" not in dom and "listed" not in dom
    assert Decimal(dom["sum_m"]) == Decimal(allowed)
    assert [(Decimal(p["min_m"]), Decimal(p["max_m"])) for p in dom["parts"]] == [(Decimal(a), Decimal(b)) for a, b in parts]
    assert [Decimal(p["rate"]) for p in dom["parts"]] == [RATE[p["band"]] for p in dom["parts"]]
    assert all("Sch 2 p17" in p["rate_source"] for p in dom["parts"]) and "half to even" in dom["rounding"]
    assert dom["charged_m"] and dom["report_m"] and all(p["measured_capacity_m"] for p in dom["parts"])
    assert dom["amount_bounds_usd"] == {"min": bounds[0], "max": bounds[1], "kind": "exact"}
    dims = {c["dimension"]: c["owner"] for c in s["conditions"]}
    assert dims == {"tolerance": "G5", "nomination": "G5"}
    assert len(s["alternatives"]) >= 2 and all(k.startswith("tolerance:") for k in s["alternatives"])
    assert sorted(dom["samples"]) == sorted(k.split(":", 1)[1] for k in s["alternatives"])
    assert "not the selected amount" in dom["samples_role"] and "unresolved, owner G5" in dom["remainder"]
    assert not any("every admissible result carried" in x for x in s["readings"])


def singleton(r, allowed: str, value: str) -> None:
    s = serialized(r)
    assert (s["amount"], s["unit_rate"], s["amount_status"], s["payable"]) == (value, None, "conditional", True)
    assert Decimal(s["allowed_quantity"]) == Decimal(allowed) and s["alternatives"] == {} and domain(r) is None
    assert [c["dimension"] for c in s["conditions"]] == ["nomination"]           # no allocation condition
    parts = [(x["label"].split(":")[0], Decimal(x["quantity"])) for x in s["trace"] if x["op"] == "part"]
    assert parts == [("band 1", Decimal(50)), ("band 2", Decimal(50))]            # the one allocation, exhaustive


@pytest.mark.parametrize("test", FIXTURES)
def test_fixture(test):
    spellings, amount, depths, allowed, parts, value, findings = FIXTURES[test]
    rs = [fixture(q, amount, depths) for q in spellings]
    for r in rs:
        assert set(r.findings) & READER == findings
        assert [c.status for c in r.checks if c.check == "arithmetic"] == ["pass"]
        if parts is None:
            singleton(r, allowed, value)
        else:
            continuous_closure(r, allowed, parts, value)
        assert vg.x3({"DDS": {"case": r}}) == []
    assert all(domain(r) == domain(rs[0]) for r in rs)                            # the spelling never changes the domain
    assert all({c["dimension"]: c["owner"] for c in serialized(r)["conditions"]} ==
               {c["dimension"]: c["owner"] for c in serialized(rs[0])["conditions"]} for r in rs)
    assert all(sorted(r.findings) == sorted(rs[0].findings) for r in rs)


# ---------------------------------------------------------------------------------------------------------- witnesses
def with_witness(r, qs, total=None, rates=None, key="tolerance:witness (test)"):
    """A correctly formed witness trace for allocation `qs` added in memory: the engine's own trace steps up to the parts,
    each part at its band rate in cents half to even, and their sum; `total` or `rates` corrupt it on purpose."""
    r = copy.deepcopy(r)
    base = next(iter(r.alternatives.values()))["trace"] if r.alternatives else r.trace
    steps = [s for s in base if s["op"] not in ("part", "sum_parts")]
    tpl = [s for s in base if s["op"] == "part"]
    vals = []
    for s, q in zip(tpl, qs):
        band, pa, pb = re.search(r"band (\d): (\d+(?:\.\d+)?)-(\d+(?:\.\d+)?) m", s["label"]).groups()
        rate = (rates or RATE)[band]
        v = q * rate
        label = re.sub(r", [\d.]+ m charged \(25A\)$", "", s["label"])
        label += "" if q == Decimal(pb) - Decimal(pa) else f", {q} m charged (25A)"
        step = {**s, "label": label, "quantity": str(q), "rate": str(rate), "value": str(cents(v))}
        step.pop("round", None)
        if v != cents(v):
            step["round"] = "half_even"
        steps.append(step)
        vals.append(cents(v))
    amt = Decimal(total) if total is not None else sum(vals, Decimal(0))
    steps.append({"op": "sum_parts", "label": "amount = sum of depth-band parts", "value": str(amt),
                  "source": "Cl.23 (p6): each band part priced at its own rate"})
    r.alternatives[key + ", " + " + ".join(f"{q} m in band {i + 1}" for i, q in enumerate(qs))] = {
        "unit_rate": None, "allowed_quantity": r.allowed_quantity, "amount": amt, "trace": steps}
    if r.amount is not None:                     # a singleton: the witness is its own trace
        r.alternatives = {}
        r.trace, r.amount = steps, amt
    return r


FX = {k: (v[0][0], v[1], v[2]) for k, v in FIXTURES.items()}
WITNESSES = [("A", "49.5", "48.5", "2096.32", "2820.28", "4916.60"), ("A", "49.75", "48.25", "2106.91", "2805.74", "4912.65"),
             ("B", "49.75", "48.75", "2106.91", "2834.81", "4941.72"), ("B", "49.99985", "48.50015", "2117.49", "2820.28", "4937.77"),
             ("B", "48.5", "50", "2053.98", "2907.50", "4961.48"), ("C", "0.55", "1.65", "23.29", "95.95", "119.24"),
             ("C", "0.333", "1.867", "14.10", "108.57", "122.67"), ("D", "50", "50", "2117.50", "2907.50", "5025.00"),
             ("E", "50.5", "50.5", "2138.68", "2936.58", "5075.26")]


@pytest.mark.parametrize("test, q1, q2, p1, p2, total", WITNESSES)
def test_witness_is_admissible_and_replays(test, q1, q2, p1, p2, total):
    r = fixture(*FX[test])
    q1, q2 = Decimal(q1), Decimal(q2)
    assert (cents(q1 * RATE["1"]), cents(q2 * RATE["2"])) == (Decimal(p1), Decimal(p2))      # independent replay
    assert Decimal(p1) + Decimal(p2) == Decimal(total)
    _spellings, _amt, _d, allowed, parts, value, _f = FIXTURES[test]
    assert q1 + q2 == Decimal(allowed)                                           # membership: the sum ...
    if parts is None:
        assert (q1, q2) == (Decimal(50), Decimal(50)) and Decimal(total) == Decimal(value) == r.amount
    else:                                                                        # ... and the coupled bounds
        assert all(Decimal(a) <= q <= Decimal(b) for q, (a, b) in zip((q1, q2), parts))
        assert Decimal(value[0]) <= Decimal(total) <= Decimal(value[1])
    w = with_witness(r, (q1, q2))
    assert vg.x3({"DDS": {"case": w}}) == []                                     # accepted, whether sampled or not


def test_b_rounded_minimum_is_not_at_the_vertex():
    """50 / 48.5 m (the unrounded minimum's vertex) is 4937.78; 49.99985 / 48.50015 m is 4937.77, the exact minimum."""
    vertex = cents(50 * RATE["1"]) + cents(Decimal("48.5") * RATE["2"])
    assert vertex == Decimal("4937.78") > Decimal(domain(fixture(*FX["B"]))["amount_bounds_usd"]["min"])


def test_billing_variation_never_selects_an_allocation():
    """Only A's billed rate and amount vary - including the in-domain 4916.60 and amounts outside the bounds."""
    base = fixture(*FX["A"])
    for rate, amount in [("42.35", "4916.60"), ("50.17", "4916.60"), ("42.35", "4908.69"), ("42.35", "4940.31"),
                         ("58.15", "5698.70"), ("1.00", "98.00"), ("60.00", "9999.99")]:
        r = fixture("98", amount, None, rate)
        assert domain(r) == domain(base) and r.allowed_quantity == base.allowed_quantity
        assert (r.amount, r.amount_status, r.unit_rate) == (None, "conditional", None)
        assert set(r.alternatives) == set(base.alternatives)                     # no allocation chosen from the bill
        assert vg.x3({"DDS": {"case": r}}) == []


def test_serialized_output_keeps_the_unresolved_domain():
    s = serialized(fixture(*FX["A"]))
    text = json.dumps(s)
    assert '"count": null' in text and '"step_m": null' in text and '"not_listed"' not in text
    assert s["amount"] is None and any(c["dimension"] == "tolerance" and c["owner"] == "G5" for c in s["conditions"])


# ------------------------------------------------------------------------------------------ X3 rejections
def x3(r) -> list[str]:
    return vg.x3({"DDS": {"case": r}})


def test_x3_accepts_the_valid_off_grid_witness():
    assert x3(with_witness(fixture(*FX["A"]), (Decimal("49.5"), Decimal("48.5")))) == []


@pytest.mark.parametrize("name, build, want", [
    ("wrong total", lambda r: with_witness(r, (Decimal("49.5"), Decimal("48.6"))), "does not sum to the allowed 98 m"),
    ("out of bounds", lambda r: with_witness(r, (Decimal("50.1"), Decimal("47.9"))), "is outside the domain's bounds"),
    ("corrupted rate", lambda r: with_witness(r, (Decimal("49.5"), Decimal("48.5")), rates={**RATE, "1": Decimal("42.36")}),
     "PD-210 part rate 42.36 is not band 1's 42.35"),
    ("wrong cents", lambda r: with_witness(r, (Decimal("49.5"), Decimal("48.5")), total="4916.61"),
     "amount 4916.61, band parts in cents half to even give 4916.60"),
])
def test_x3_rejects_invalid_witnesses(name, build, want):
    errs = x3(build(fixture(*FX["A"])))
    assert any(want in e for e in errs), errs


def _tol(r):
    return next(c for c in r.conditions if c["dimension"] == "tolerance")


def _three_point(r):
    """The earlier claim: a complete domain of the three whole-metre allocations."""
    old = set(r.alternatives)
    for q1 in ("50", "49", "48"):
        r = with_witness(r, (Decimal(q1), Decimal(98) - Decimal(q1)), key=f"tolerance:{q1}")
    r.alternatives = {k: v for k, v in r.alternatives.items() if k not in old}
    _tol(r)["domain"].update(mode="enumerated", step_m="1", count=3, enumerable=True, exhaustive=True, listed=3,
                             not_listed="none")
    return r


def _drop_condition(r):
    r = copy.deepcopy(r)
    r.conditions = [c for c in r.conditions if c["dimension"] != "tolerance"]
    return r


def _owner(r, owner):
    r = copy.deepcopy(r)
    _tol(r)["owner"] = owner
    _tol(r)["domain"]["owner"] = owner
    return r


def _state(r):
    r = copy.deepcopy(r)
    _tol(r)["domain"]["state"] = "resolved"
    return r


def _bounds(r, lo, hi, kind="exact"):
    r = copy.deepcopy(r)
    _tol(r)["domain"]["amount_bounds_usd"] = {"min": lo, "max": hi, "kind": kind}
    return r


@pytest.mark.parametrize("name, mutate, want", [
    ("complete three-allocation domain", _three_point, "PD-210 allocation domain is continuous and unresolved"),
    ("condition removed", _drop_condition, "without an owned 'tolerance' condition (G5)"),
    ("owner removed", lambda r: _owner(r, ""), "without an owned 'tolerance' condition (G5)"),
    ("state resolved", _state, "PD-210 allocation domain is continuous and unresolved"),
    ("bounds exclude 4940.30", lambda r: _bounds(r, "4908.70", "4940.29"), "exclude the admissible allocation"),
    ("bounds exclude 4916.60", lambda r: _bounds(with_witness(r, (Decimal("49.5"), Decimal("48.5"))), "4916.61", "4940.30",
                                                 "conservative"), "exclude the admissible allocation"),
    ("inexact 'exact' bounds", lambda r: _bounds(r, "4908.69", "4940.30"), "are not the domain's lowest and highest"),
    ("single amount", lambda r: dataclass_set(r, amount=Decimal("4916.60")), "given the single amount 4916.60"),
])
def test_x3_rejects_a_mutated_continuous_result(name, mutate, want):
    r = fixture(*FX["A"])
    assert x3(r) == []
    errs = x3(mutate(r))
    assert any(want in e for e in errs), errs


def dataclass_set(r, **kw):
    r = copy.deepcopy(r)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_x3_rejects_a_singleton_carried_as_a_domain():
    """D: 100 m on 50 + 50 m forces 50/50; a result carrying it as an unresolved domain is wrong."""
    d = fixture(*FX["D"])
    a = fixture(*FX["A"])
    bad = copy.deepcopy(d)
    bad.conditions = copy.deepcopy(a.conditions)
    errs = x3(bad)
    assert any("allocation domain stated where the depths place every metre" in e for e in errs), errs
