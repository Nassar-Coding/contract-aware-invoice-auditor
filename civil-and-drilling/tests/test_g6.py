"""G6 containment checks: each can fail (negative controls), on engine-level fixtures."""
from decimal import Decimal

import g6_review as R
import test_g5_falsification as U
from audit.g3_core import Check

D = Decimal


def _full(line):
    """A fixture line with every G3 check recorded (a pass), as a real line carries them."""
    _k, _v, g = line
    have = {c.check for c in g.g3.checks}
    for chk in ("term", "window", "evidence", "quantity", "rate", "arithmetic"):
        if chk not in have:
            g.g3.checks.append(Check(chk, "pass", "R", "c"))
    return line


def _cw(lines, total):
    lines = [_full(x) for x in lines]
    t = D(total)
    ret = (t * D("0.05")).quantize(D("0.01"), rounding="ROUND_DOWN")
    return U.Invoice("CW", "X", {"application_total": t, "retention": ret, "net_payable": t - ret, "adjustment": D("0"),
                                 "retention_released": D("0")}, lines)


def _outcome(inv):
    e = U._engine([inv])
    return e.outcome(inv)


def test_supported_pass_has_no_reason():
    inv = _cw([U._line("CW", "X-01", "A.11.010", "100.00", "100.00")], "100.00")
    o = _outcome(inv)
    assert o["flagged"] == 0 and R.unsupported_reasons(o, inv) == set()


def test_unresolved_rate_check_nothing_completes_is_an_unsupported_pass():
    k, v, g = U._line("CW", "X-01", "A.11.010", "100.00", "100.00")
    g.g3.checks.append(Check("rate", "unresolved", "CW-R10", "Cl.27", "rate_differs"))
    g.g3.checks += [Check(c, "pass", "R", "c") for c in ("term", "window", "evidence", "quantity", "arithmetic")]
    g.r.unit_rate = None                         # no rate carried for any scenario: G5 cannot complete it
    inv = _cw([(k, v, g)], "100.00")
    o = _outcome(inv)
    assert o["flagged"] == 0
    assert any("left unresolved and not completed later" in x for x in R.unsupported_reasons(o, inv))
    g.r.unit_rate = D("3.85")                    # every scenario carries a rate: G5 completes it per scenario
    assert R.unsupported_reasons(o, inv) == set()


def test_unvalued_line_and_unformed_total_are_reported():
    k, v, g = U._line("CW", "X-01", "A.11.010", "100.00", None)
    g.r.payable, g.r.amount, g.r.amount_status = None, None, "unresolved"
    inv = _cw([(k, v, g)], "100.00")
    o = _outcome(inv)
    why = R.unsupported_reasons(o, inv)
    assert "total not formed (EXPORT-U)" in why and "a line with no established payability" in why


def test_unresolved_state_without_a_carrying_scenario_is_reported():
    k, v, g = U._line("CW", "X-01", "A.11.010", "100.00", "100.00")
    g.add("band_rate", "unresolved", "CW-R10", "Cl.27", "rate_differs", "open")
    inv = _cw([(k, v, g)], "100.00")
    o = _outcome(inv)
    assert any("unresolved with no scenario carrying it" in x for x in R.unsupported_reasons(o, inv))
