"""Review finding 4: every derived fact references a run context covering code and reviewed inputs; disclosed
conflicts are registered with an owning gate and treatment; Q11 uses the records-based depth bound."""
import json
import shutil
from pathlib import Path

import pytest
import yaml

import verify_g2
from audit import build, provenance

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def world():
    return build.build()


def _copy_inputs(dst: Path) -> None:
    for g in provenance.CODE_GLOBS:
        for p in ROOT.glob(g):
            (dst / p.relative_to(ROOT)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, dst / p.relative_to(ROOT))
    for f in provenance.CODE_FILES + provenance.REVIEWED_INPUTS + provenance.EVIDENCE_IDENTITY:
        (dst / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, dst / f)


def test_every_fact_references_the_run_context(world):
    ctx = world.run_context
    facts = provenance.facts(world)
    assert len(facts) > 200_000 and {f.ctx for f in facts} == {ctx["id"]}
    assert json.loads((build.OUT / "run_context.json").read_text()) == ctx
    assert verify_g2.provenance_check(world) == []


def test_code_only_change_changes_the_context(tmp_path):
    _copy_inputs(tmp_path)
    before = provenance.run_context(tmp_path)
    assert before == provenance.run_context(ROOT)
    f = tmp_path / "audit" / "links.py"
    f.write_text(f.read_text() + "\n# a code-only change\n")
    after = provenance.run_context(tmp_path)
    assert after["id"] != before["id"]
    assert after["reviewed_inputs"] == before["reviewed_inputs"] and after["snapshot"] == before["snapshot"]


def test_reviewed_input_change_changes_the_context(tmp_path):
    _copy_inputs(tmp_path)
    before = provenance.run_context(tmp_path)
    f = tmp_path / "spec" / "evidence_dds.yaml"
    f.write_text(f.read_text().replace("tool_code: DD-120", "tool_code: DD-121"))
    assert provenance.run_context(tmp_path)["id"] != before["id"]


def test_fact_without_context_fails(world):
    f = world.dds_links["MDS-00022-030"]
    old, f.ctx = f.ctx, None
    try:
        assert verify_g2.provenance_check(world) == ["Link without the run context"]
    finally:
        f.ctx = old


def _register():
    return yaml.safe_load((ROOT / "spec" / "carried_items.yaml").read_text())


def _ids():
    rules = {r["id"] for r in yaml.safe_load((ROOT / "spec" / "rules.yaml").read_text())["rules"]}
    qs = {q["id"] for q in yaml.safe_load((ROOT / "spec" / "open_questions.yaml").read_text())["questions"]}
    return rules, qs


def test_register_covers_every_disclosed_item(world):
    reg = _register()
    assert verify_g2.carried_items_check(world, reg, *_ids()) == []
    assert {i["id"]: i["owner_gate"] for i in reg["items"]} == {"CI-02": "G3", "CI-03": "G3"}
    ci01 = next(i for i in reg["resolved"] if i["id"] == "CI-01")          # resolved at G3: tool-history corroboration
    assert len(ci01["idents"]) == 27 and ci01["resolved_at"] == "G3"


def test_unregistered_conflict_fails(world):
    reg = _register()
    reg["items"] = [i for i in reg["items"] if i["id"] != "CI-02"]
    errs = verify_g2.carried_items_check(world, reg, *_ids())
    assert errs == ["conflict gyro_surveys_without_part_C DDR_NGP-WS-009_20251211.txt is not registered in spec/carried_items.yaml",
                    "line MDS-00954-021 lacks its Schedule 5 part C and is not registered"]


def test_unregistered_missing_part_line_fails(world):
    reg = _register()
    ci03 = next(i for i in reg["items"] if i["id"] == "CI-03")
    ci03["lines"] = ["MDS-00876-062"]
    assert verify_g2.carried_items_check(world, reg, *_ids()) == [
        "line MDS-01393-036 lacks its Schedule 5 part D and is not registered"]


def test_q11_uses_the_records_based_bound():
    scopes = json.loads((ROOT / "spec" / "question_scopes.json").read_text())["Q11_DDS"]
    assert scopes["max_physical_depth_increment_per_well_all_dates"] == 6026
    assert scopes["well_with_max_physical_depth_increment"] == "NGP-BD-027"
    assert scopes["negative_daily_depth_increments"] == 0 and scopes["wells_at_or_above_40000_under_any_reading"] == 0
    q11 = next(q for q in yaml.safe_load((ROOT / "spec" / "open_questions.yaml").read_text())["questions"] if q["id"] == "Q11")
    assert "6,026" in q11["observation"] and "Max billed" not in q11["observation"]
    d4 = next(d for d in yaml.safe_load((ROOT / "spec" / "open_questions.yaml").read_text())["decisions"] if d["id"] == "D4")
    assert d4["scope_key"] == "max_physical_depth_increment_per_well_all_dates"


def test_stale_missing_part_entry_fails(world):
    """Stale-entry check: a registered missing-part line whose part is now present must fail."""
    import copy
    w2 = copy.copy(world)
    w2.dds_links = dict(world.dds_links)
    for ref in ("MDS-00876-062", "MDS-01393-036"):
        l = copy.deepcopy(world.dds_links[ref])
        l.semantic["required_part_present"] = True
        w2.dds_links[ref] = l
    errs = verify_g2.carried_items_check(w2, _register(), *_ids())
    assert errs == ["CI-03: registered line MDS-00876-062 no longer lacks its Schedule 5 part",
                    "CI-03: registered line MDS-01393-036 no longer lacks its Schedule 5 part"]
