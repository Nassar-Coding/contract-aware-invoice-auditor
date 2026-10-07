"""Shared helpers for the G0/G1 specification files (loading, canonical hashing, exact numbers)."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec"
VERIF = ROOT / "verification"

TERMS_FILES = {"CW": "terms_cw.yaml", "DDS": "terms_dds.yaml"}
GATED_USES = {"pricing", "eligibility", "evidence", "quantity"}  # tables that can feed valuation
ALL_USES = GATED_USES | {"coverage", "example", "consistency"}


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_terms(contract: str) -> dict:
    return load_yaml(SPEC / TERMS_FILES[contract])


def load_instruments() -> dict:
    return load_yaml(SPEC / "instruments.yaml")


def canonical_hash(obj) -> str:
    """Stable SHA-256 of a YAML-loaded structure (key order and whitespace independent)."""
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def table_content(table: dict) -> dict:
    """The part of a table a second verification certifies: rows, columns, extra numeric blocks and notes."""
    keys = ("columns", "rows", "totals", "discount_example", "applies_to_series", "not_applied_to_series", "note_on_page")
    return {k: table[k] for k in keys if k in table}


def instrument_content(inst: dict) -> dict:
    keys = ("issued", "effective", "rate_rows", "monthly_rows", "discount", "completion_extended_to",
            "expiry_extended_to", "extension_days_as_printed", "incorporated_clause", "adjustment_wording", "unaffected")
    return {k: inst[k] for k in keys if k in inst}


def dec(s: str) -> Decimal:
    """Exact Decimal from a printed number such as '1,480.00'. Never goes through float."""
    return Decimal(s.replace(",", "").strip())


def is_number(s) -> bool:
    if not isinstance(s, str):
        return False
    t = s.replace(",", "").strip()
    if not t:
        return False
    try:
        Decimal(t)
    except Exception:
        return False
    return t[0].isdigit() or t[0] == "-"


def numeric_cells(table: dict) -> list[tuple[int, str, str]]:
    """(row_index, column, printed value) for every numeric cell of a table."""
    out = []
    cols = table.get("columns", [])
    for i, row in enumerate(table.get("rows", [])):
        for c, v in zip(cols, row):
            if is_number(v):
                out.append((i, c, v))
    return out


def all_tables():
    """Yield (contract, table_id, table) for every table in both terms files."""
    for c in ("CW", "DDS"):
        for tid, t in load_terms(c)["tables"].items():
            yield c, tid, t
