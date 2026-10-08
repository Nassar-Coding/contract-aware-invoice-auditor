"""G0/G1 gate: the real specification passes, and each class of defect makes it fail (negative controls)."""
import shutil

import pytest
import yaml

import spec_lib as sl
import verify_spec


def test_real_spec_passes(capsys):
    assert verify_spec.main() == 0
    out = capsys.readouterr().out
    assert out.count("PASS ") == 10 and "FAIL" not in out


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    spec = tmp_path / "spec"
    verif = tmp_path / "verification"
    shutil.copytree(sl.SPEC, spec)
    verif.mkdir()
    shutil.copy(sl.VERIF / "second_pass_log.yaml", verif / "second_pass_log.yaml")
    shutil.copy(sl.VERIF / "param_rule_log.yaml", verif / "param_rule_log.yaml")
    shutil.copytree(sl.VERIF / "param_rule_packets", verif / "param_rule_packets")
    for f in ("section_readings.jsonl", "section_dispositions.yaml"):
        if (sl.VERIF / f).exists():
            shutil.copy(sl.VERIF / f, verif / f)
    monkeypatch.setattr(sl, "SPEC", spec)
    monkeypatch.setattr(sl, "VERIF", verif)
    return spec


def edit(path, fn):
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def run(capsys):
    rc = verify_spec.main()
    return rc, capsys.readouterr().out


def test_changed_rate_invalidates_verification_and_pricing_gate(sandbox, capsys):
    def f(d):
        d["tables"]["CW.T01_SCH1"]["rows"][0][4] = "3.86"   # A.11.010 3.85 -> 3.86
    edit(sandbox / "terms_cw.yaml", f)
    rc, out = run(capsys)
    assert rc == 1
    assert "CW.T01_SCH1: content changed since verification" in out
    assert "CW.T01_SCH1 feeds pricing but is not verified" in out


def test_changed_instrument_rate_is_caught(sandbox, capsys):
    def f(d):
        d["instruments"][-1]["rate_rows"][0]["to"] = "406.00"   # DDS A3 DD-120 416.00 -> 406.00
    edit(sandbox / "instruments.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "DDS.A3: content changed since verification" in out


def test_section_without_owner_fails(sandbox, capsys):
    def f(d):
        d["sections"] = [x for x in d["sections"] if x["pages"] != [27]]
    edit(sandbox / "sections_cw.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "pages without any section entry [27]" in out


def test_guideline_check_without_owner_fails(sandbox, capsys):
    def f(d):
        d["checks"][9]["DDS"] = []
    edit(sandbox / "guideline_checks.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "check 10 has no DDS owner" in out


def test_open_question_needs_two_alternatives_and_scope(sandbox, capsys):
    def f(d):
        # the defect: an OPEN question with one alternative (Q1 is resolved at G5, so the control re-opens it itself)
        d["questions"][0]["status"] = "open"
        d["questions"][0]["alternatives"] = d["questions"][0]["alternatives"][:1]
        d["questions"][1]["scope_ref"] = []
    edit(sandbox / "open_questions.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "Q1: fewer than two bounded alternatives" in out and "Q2: no affected scope" in out


def test_rule_without_page_reference_fails(sandbox, capsys):
    def f(d):
        d["rules"][0]["sources"] = ["somewhere"]
    edit(sandbox / "rules.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "CW-R01: source without page/clause reference" in out


def test_unverified_log_entry_blocks_pricing(sandbox, capsys):
    path = sl.VERIF / "second_pass_log.yaml"
    d = yaml.safe_load(path.read_text())
    d["entries"]["DDS.T12_STANDBY"]["status"] = "pending"
    path.write_text(yaml.safe_dump(d, sort_keys=False))
    rc, out = run(capsys)
    assert rc == 1 and "DDS.T12_STANDBY feeds pricing but is not verified" in out


def test_regenerating_log_never_recertifies_a_changed_table(sandbox, monkeypatch, capsys):
    import sys
    import record_verification
    for sub in ("blind", "ocr"):
        shutil.copytree(sl.ROOT / "verification" / sub, sl.VERIF / sub, ignore=shutil.ignore_patterns("crops_*"))
    shutil.copy(sl.ROOT / "verification" / "reading_comparison.json", sl.VERIF / "reading_comparison.json")

    def f(d):
        d["tables"]["DDS.T05_RSI"]["rows"][5][1] = "103.70"
    edit(sandbox / "terms_dds.yaml", f)
    monkeypatch.setattr(sys, "argv", ["record_verification.py"])
    assert record_verification.main() == 0
    log = yaml.safe_load((sl.VERIF / "second_pass_log.yaml").read_text())["entries"]
    assert log["DDS.T05_RSI"]["status"] == "pending"
    assert log["DDS.T01_SCH1"]["status"] == "verified"
    capsys.readouterr()
    rc, out = run(capsys)
    assert rc == 1 and "DDS.T05_RSI feeds pricing but is not verified" in out


# ---- parameters and rules are content-verified like tables --------------------------------------------------
def test_vat_15_to_16_fails(sandbox, capsys):
    def f(d):
        next(p for p in d["parameters"] if p["id"] == "DDS.P.vat_pct")["value"] = "16"
    edit(sandbox / "terms_dds.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "DDS.P.vat_pct: content changed since its second reading" in out


def test_edited_rule_fails(sandbox, capsys):
    def f(d):
        r = next(r for r in d["rules"] if r["id"] == "DDS-R07")
        r["title"] = r["title"].replace("six-hour", "eight-hour")
    edit(sandbox / "rules.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "DDS-R07: content changed since its second reading" in out


def test_edited_rule_source_or_case_fails(sandbox, capsys):
    def f(d):
        r = next(r for r in d["rules"] if r["id"] == "CW-R07")
        r["validation"][0] = "102.50% payable as measured"
    edit(sandbox / "rules.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "CW-R07: content changed since its second reading" in out


def test_edited_parameter_provision_fails(sandbox, capsys):
    def f(d):
        next(p for p in d["parameters"] if p["id"] == "DDS.P.circulating_hour_rounding")["page"] = 13
    edit(sandbox / "terms_dds.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "DDS.P.circulating_hour_rounding: content changed since its second reading" in out


def test_missing_reading_fails(sandbox, capsys):
    log = sl.VERIF / "param_rule_log.yaml"
    data = yaml.safe_load(log.read_text())
    del data["entries"]["CW.P.retention_pct"]
    log.write_text(yaml.safe_dump(data, sort_keys=False))
    rc, out = run(capsys)
    assert rc == 1 and "CW.P.retention_pct: no second reading recorded" in out


def test_recorder_refuses_reading_of_an_older_statement(tmp_path, monkeypatch):
    """A reading made before an edit does not certify the edited item: record() leaves it pending."""
    import param_rule_verification as prv
    spec, verif = tmp_path / "spec", tmp_path / "verification"
    shutil.copytree(sl.SPEC, spec)
    shutil.copytree(sl.VERIF / "param_rule_readings", verif / "param_rule_readings")
    shutil.copytree(sl.VERIF / "param_rule_packets", verif / "param_rule_packets")
    shutil.copy(sl.VERIF / "param_rule_dispositions.yaml", verif / "param_rule_dispositions.yaml")
    shutil.copytree(sl.VERIF / "ocr", verif / "ocr")
    monkeypatch.setattr(sl, "SPEC", spec)
    monkeypatch.setattr(sl, "VERIF", verif)
    for k, v in {"READINGS": verif / "param_rule_readings", "LOG": verif / "param_rule_log.yaml",
                 "DISPOSITIONS": verif / "param_rule_dispositions.yaml", "OCR": verif / "ocr"}.items():
        monkeypatch.setattr(prv, k, v)
    def f(d):
        next(p for p in d["parameters"] if p["id"] == "DDS.P.vat_pct")["value"] = "16"
    edit(spec / "terms_dds.yaml", f)
    assert prv.record([]) == 0
    e = yaml.safe_load((verif / "param_rule_log.yaml").read_text())["entries"]["DDS.P.vat_pct"]
    assert e["status"] == "pending" and e["read_current_statement"] is False and e["value_equivalent"] is False


def test_section_classification_edit_after_reading_fails(sandbox, capsys):
    def f(d):
        sec = next(x for x in d["sections"] if x["pages"] == [9, 10])
        sec["not_material"] = "technical obligations only"
    edit(sandbox / "sections_dds.yaml", f)
    rc, out = run(capsys)
    assert rc == 1 and "('DDS', 'Part V Technical T1-T16'): classification changed since its second reading" in out


def test_recorder_document_quotes_resolve_in_another_checkout(tmp_path, monkeypatch):
    """Document quotes are recorded relative to the input snapshot, so they still match when the snapshot
    lives elsewhere (paths are resolved on the pinned snapshot of this checkout)."""
    import json
    import snapshot
    import param_rule_verification as prv
    moved = tmp_path / "elsewhere" / "inputs"
    quotes = []
    for f in sorted((sl.VERIF / "param_rule_readings").glob("*.jsonl")):
        for ln in f.read_text().splitlines():
            for q in json.loads(ln).get("quotes", []):
                if q.get("document"):
                    quotes.append(q)
    assert quotes
    for q in quotes:
        rel = q["document"]
        (moved / rel).parent.mkdir(parents=True, exist_ok=True)
        (moved / rel).write_text((snapshot.DEFAULT_SNAPSHOT / rel).read_text())
    monkeypatch.setattr(snapshot, "DEFAULT_SNAPSHOT", moved)
    assert all(prv.document_match(q["document"], q["text"]) == 1.0 for q in quotes)
    monkeypatch.setattr(snapshot, "DEFAULT_SNAPSHOT", tmp_path / "missing")
    assert all(prv.document_match(q["document"], q["text"]) == 0.0 for q in quotes)      # control: no snapshot -> no match
