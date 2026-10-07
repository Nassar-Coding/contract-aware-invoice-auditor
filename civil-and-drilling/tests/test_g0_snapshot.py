"""G0 exit condition, part 1: file hashes and inventory match the pinned snapshot.

The expected numbers below were established from a first reading of the data, independently of the frozen inventory,
so a wrong freeze cannot pass.
"""
import json
from pathlib import Path

import pytest

import snapshot

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inv():
    return json.loads((ROOT / "source" / "inventory.json").read_text())


def test_snapshot_present_and_pinned():
    assert snapshot.DEFAULT_SNAPSHOT.exists(), f"snapshot missing at {snapshot.DEFAULT_SNAPSHOT}; see source/SNAPSHOT.md"
    assert snapshot.git_head(snapshot.DEFAULT_SNAPSHOT) == "aef4924dc32506b4587de8b788b5a947e6beffec"


def test_every_file_hash_matches_manifest_and_pinned_tree():
    errors = snapshot.verify(snapshot.DEFAULT_SNAPSHOT)
    assert errors == []


def test_manifest_covers_whole_snapshot():
    rows = snapshot.read_manifest(ROOT / "source" / "manifest.tsv")
    assert len(rows) == 10330
    assert sum(r[0].startswith("civilwork/records/") for r in rows) == 2169
    assert sum(r[0].startswith("drilling_services/records/") for r in rows) == 8151


def test_plan_population_table(inv):
    c, d = inv["civil"], inv["drilling"]
    assert (c["headers"]["rows"], d["headers"]["rows"]) == (900, 1906)
    assert (c["lines"]["rows"], d["lines"]["rows"]) == (7746, 91244)
    assert (c["distinct_item_codes"], d["distinct_service_codes"]) == (60, 39)
    assert "DS-900" in d["service_codes"]
    assert (c["records"]["files"], d["records"]["files"]) == (2169, 8151)
    assert (c["records"]["total_bytes"], d["records"]["total_bytes"]) == (557687, 7698252)
    assert (c["records"]["largest_bytes"], d["records"]["largest_bytes"]) == (344, 1236)


def test_ids_joins_and_template(inv):
    for k in ("civil", "drilling"):
        assert inv[k]["headers"]["unique_ids"] == inv[k]["headers"]["rows"]
        assert inv[k]["lines"]["unique_line_refs"] == inv[k]["lines"]["rows"]
        assert inv[k]["header_ids_equal_line_ids"] is True
    t = inv["template"]
    assert t["rows"] == t["unique_ids"] == 2806
    assert t["ids_equal_header_ids"] and t["all_other_fields_blank"]
    assert t["columns"] == ["invoice_id", "flagged", "error_category", "expected_total_cents", "billed_total_cents", "confidence"]


def test_observations_later_gates_depend_on(inv):
    c, d = inv["civil"], inv["drilling"]
    assert c["nonzero_adjustment"] == c["nonzero_retention_released"] == d["nonzero_adjustment"] == 0
    assert c["record_refs_without_file"] == ["CT-00126", "MO-00089", "PS-00039"]
    assert c["headers_on_2026_05_12"] == ["PA-00006", "PA-00023", "PA-00380"]
    assert d["headers_on_17_Aug_2026"] == ["MDS-01625"]
    assert c["contract_ref_counts"] == {"CW-2025-0417-CIV": 898, "CW-2024-0417-CIV": 2}
    assert d["contract_ref_counts"] == {"DDS-2025-118": 1903, "DSS-2025-118": 2, "DDS-2025-181": 1}
    assert d["blank_report_ref_codes"] == {"DS-900": 63}


def test_scans_have_no_text_layer_and_one_image_per_page():
    pages = json.loads((ROOT / "source" / "pdf_pages.json").read_text())
    assert pages["CW"]["page_count"] == 43 and pages["DDS"]["page_count"] == 42
    for doc in pages.values():
        assert doc["text_layer_chars"] == 0
        for p in doc["pages"]:
            assert (p["images"], p["width"], p["height"], p["colorspace"]) == (1, 1654, 2338, "DeviceGray")
            assert len(p["samples_sha256"]) == 64
