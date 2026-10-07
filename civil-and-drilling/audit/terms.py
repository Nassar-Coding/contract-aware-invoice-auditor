"""G1-verified terms as typed, read-only lookups for G3 pricing (both contracts).

Every value is an exact Decimal parsed from the verified specification string (spec/terms_*.yaml,
spec/instruments.yaml) and carries its source (table/instrument id and page) so a calculation trace can cite it.
Nothing here decides entitlement; it only answers "what does the verified term say".
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from .common import spec_lib


def D(s) -> Decimal:
    return Decimal(str(s).replace(",", "").strip())


def _date(s) -> dt.date:
    return dt.date.fromisoformat(str(s))


@dataclass(frozen=True)
class Val:
    value: Decimal
    source: str


@dataclass
class Instrument:
    id: str
    issued: dt.date
    effective: dt.date
    retrospective: bool
    page: int
    rate_rows: list = field(default_factory=list)        # (code, row_effective date, to Decimal)
    monthly_code: str | None = None
    monthly: list = field(default_factory=list)          # ('YYYY-MM', Decimal) ascending
    discount: dict | None = None                         # {pct, codes, from}
    term_end: dt.date | None = None


def _instruments(contract: str) -> list[Instrument]:
    out = []
    for i in spec_lib.load_instruments()["instruments"]:
        if i["contract"] != contract:
            continue
        ins = Instrument(i["id"], _date(i["issued"]), _date(i["effective"]), bool(i.get("retrospective")), i["page"])
        ins.rate_rows = [(r["code"], _date(r["row_effective"]), D(r["to"])) for r in i.get("rate_rows", [])]
        if i.get("monthly_rows"):
            ins.monthly_code = i["monthly_rows"]["code"]
            ins.monthly = sorted((m, D(v)) for m, v in i["monthly_rows"]["rows"])
        if i.get("discount"):
            d = i["discount"]
            ins.discount = {"pct": D(d["pct"]), "codes": set(d["codes"]), "from": _date(d["from"])}
        end = i.get("completion_extended_to") or i.get("expiry_extended_to")
        ins.term_end = _date(end) if end else None
        out.append(ins)
    return sorted(out, key=lambda x: x.issued)


def _params(contract: str) -> dict:
    return {p["id"].split(".", 2)[2]: p for p in spec_lib.load_terms(contract)["parameters"]}


class _Base:
    contract: str
    instruments: list[Instrument]
    sch1: dict

    def rate_candidates(self, code: str, date: dt.date, submitted: dt.date | None):
        """Rate history applicable to `date`: Sch 1, then every instrument row/month in issue order.

        An instrument substituting a rate from before its own date of issue (retrospective) is ignored for an
        invoice/application submitted before that date of issue (CW 31A, DDS 36A)."""
        month = f"{date:%Y-%m}"
        cands = []
        for order, ins in enumerate(self.instruments, 1):
            if ins.retrospective and submitted is not None and submitted < ins.issued:
                continue
            rows = [(eff, to) for c, eff, to in ins.rate_rows if c == code and eff <= date]
            if rows:
                eff, to = max(rows)
                cands.append((order, eff, to, f"{ins.id} p{ins.page} row {code} from {eff}"))
            if ins.monthly_code == code:
                ms = [(m, v) for m, v in ins.monthly if m <= month]
                if ms:
                    m, v = ms[-1]
                    cands.append((order, _date(m + "-01"), v, f"{ins.id} p{ins.page} monthly {code} {m}"
                                  + ("" if m == month else f" (carried forward to {month})")))
        return cands

    def submission_regimes(self) -> list:
        """Every admissible submission regime when the submission date is not stated (FD08): an instrument
        substituting a rate retrospectively is ignored for a submission before its issue (CW 31A, DDS 36A), so what the
        date decides is how many retrospective issue dates follow it. One representative submission per regime: the
        day before each retrospective issue date, and None (after every issue). An early submission is admissible:
        G3-D1 keeps a line's local value whatever its submission timing."""
        issues = sorted({ins.issued for ins in self.instruments if ins.retrospective})
        return [d - dt.timedelta(days=1) for d in issues] + [None]

    def discount(self, code: str, date: dt.date):
        """The selected-item discount in force on `date`: the latest-issued instrument whose discount covers it."""
        best = None
        for ins in self.instruments:
            d = ins.discount
            if d and code in d["codes"] and d["from"] <= date:
                best = Val(d["pct"], f"{ins.id} p{ins.page} {d['pct']}% from {d['from']}")
        return best


class CwTerms(_Base):
    contract = "CW"

    def __init__(self):
        t = spec_lib.load_terms("CW")["tables"]
        p = _params("CW")
        self.sch1 = {r[0]: {"description": r[1], "unit": r[2], "rate": D(r[4]), "series": r[0][0]} for r in t["CW.T01_SCH1"]["rows"]}
        self.zones = {r[0]: D(r[2]) for r in t["CW.T02_ZONES"]["rows"]}
        self.zone_series = set(t["CW.T02_ZONES"]["applies_to_series"])
        self.areas = {r[0]: r[1] for r in t["CW.T03_WORK_AREAS"]["rows"]}
        self.smi_items = {r[0]: D(r[1]) for r in t["CW.T04_SMI_ITEMS"]["rows"]}
        self.smi = {r[0]: D(r[1]) for r in t["CW.T05_SMI"]["rows"]}
        self.smi_base = D("100.00")          # Sch 2A (p21): 'base 100.00 at 5 January 2025'
        self.usd_items = {r[0]: D(r[1]) for r in t["CW.T06_USD_ITEMS"]["rows"]}
        self.fx = {r[0]: D(r[1]) for r in t["CW.T07_FX"]["rows"]}
        self.ground = {r[0]: D(r[2]) for r in t["CW.T08_GROUND_FACTORS"]["rows"]}
        self.ground_items = {r[0] for r in t["CW.T09_GROUND_ITEMS"]["rows"]}
        self.night = {r[0]: D(r[1]) for r in t["CW.T10_NIGHT"]["rows"]}
        self.rest = {r[0]: D(r[1]) for r in t["CW.T11_REST"]["rows"]}
        self.banded = {r[0] for r in t["CW.T12_BANDS"]["rows"]}
        self.band_pcts = {r[0]: [D(r[4]), D(r[5]), D(r[6])] for r in t["CW.T12_BANDS"]["rows"]}   # pct_band1..3
        self.band_edges = {r[0]: (D(r[2]), D(r[3])) for r in t["CW.T12_BANDS"]["rows"]}          # band1_to, band2_to
        self.limited = {r[0] for r in t["CW.T13_DAILY_LIMITS"]["rows"]}
        self.excluded = {r[0] for r in t["CW.T14_EXCLUSIONS"]["rows"]}
        self.surveyed = {r[0] for r in t["CW.T15_SURVEYED"]["rows"]}
        self.records = {r[0]: r[2] for r in t["CW.T16_RECORDS"]["rows"]}
        self.hourly = {c for c, v in self.sch1.items() if v["unit"] == "hour"}
        self.weekly_record = {c for c, v in self.sch1.items() if v["unit"] == "week" and c in self.records}
        self.instruments = _instruments("CW")
        self.commencement = _date(p["commencement"]["value"])
        self.completion = max(i.term_end for i in self.instruments if i.term_end)      # as extended (A2)
        self.window_days = int(p["submission_window_days"]["value"])
        self.g2_after = _date(p["g2_after"]["value"])
        self.night_suppressed_above = D(p["night_suppressed_zone_factor_above"]["value"])
        self.rest_days = {x.strip() for x in p["rest_days"]["value"].split(",")}
        self.survey_tolerance = D(p["survey_tolerance_pct"]["value"])
        self.first_hour = D(p["first_hour_excluded"]["value"])
        self.weekly_min_days = int(p["weekly_min_days"]["value"])
        self.unrecorded_ground = p["unrecorded_ground"]["value"]


class DdsTerms(_Base):
    contract = "DDS"

    def __init__(self):
        t = spec_lib.load_terms("DDS")["tables"]
        p = _params("DDS")
        self.sch1 = {}
        for code, desc, unit, rate in t["DDS.T01_SCH1"]["rows"]:
            self.sch1[code] = {"description": desc, "unit": unit, "rate": D(rate) if rate[0].isdigit() else None, "rate_text": rate}
        self.depth_bands = [(D(r[1]), D(r[2]) if r[2] else None, D(r[3]), r[0]) for r in t["DDS.T02_DEPTH_BANDS"]["rows"]]
        self.rsi_items = {r[0]: D(r[1]) for r in t["DDS.T04_RSI_ITEMS"]["rows"]}
        self.rsi = {r[0]: D(r[1]) for r in t["DDS.T05_RSI"]["rows"]}
        self.rsi_base = D("100.00")          # Sch 2C (p18): 'base 100.00'
        self.sar = {r[0]: D(r[1]) for r in t["DDS.T06_SAR_VALUES"]["rows"]}
        self.fx = {r[0]: D(r[1]) for r in t["DDS.T07_FX"]["rows"]}
        self.section = {r[0]: D(r[1]) for r in t["DDS.T08_SECTION_FACTORS"]["rows"]}
        self.section_rated = {r[0] for r in t["DDS.T09_SECTION_RATED"]["rows"]}
        self.class_factor = {r[0]: D(r[1]) for r in t["DDS.T10_CLASS_FACTORS"]["rows"]}
        self.class_rated = {r[0] for r in t["DDS.T11_CLASS_RATED"]["rows"]}
        self.standby = {r[0]: (None if r[1] == "NC" else D(r[1])) for r in t["DDS.T12_STANDBY"]["rows"]}
        self.limited = {r[0] for r in t["DDS.T13_DAILY_LIMITS"]["rows"]}
        self.once_per_well = {r[0] for r in t["DDS.T14_ONCE_PER_WELL"]["rows"]}
        self.sch5 = {r[0]: r[1] for r in t["DDS.T18_SCH5_CODES"]["rows"]}
        self.sch8 = {r[0]: r[1] for r in t["DDS.T23_SCH8"]["rows"]}
        self.instruments = _instruments("DDS")
        self.commencement = _date(p["commencement"]["value"])
        self.expiry = max(i.term_end for i in self.instruments if i.term_end)          # as extended (A2)
        self.window_days = int(p["submission_window_days"]["value"])
        self.dd120_min = D(p["dd120_min_hours"]["value"])
        self.first_hour = D(p["first_hour_excluded"]["value"])
        self.metre_tolerance = D(p["metre_tolerance_pct"]["value"])
        self.lih_step_hours = D("25")        # DDS.P.lih_depreciation: 1% per complete 25 hours, max 50%
        self.lih_cap_pct = D("50")
        self.lih_pct_per_step = D("1")


_CACHE: dict = {}


def cw() -> CwTerms:
    if "CW" not in _CACHE:
        _CACHE["CW"] = CwTerms()
    return _CACHE["CW"]


def dds() -> DdsTerms:
    if "DDS" not in _CACHE:
        _CACHE["DDS"] = DdsTerms()
    return _CACHE["DDS"]
