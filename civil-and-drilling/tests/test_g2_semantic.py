"""Review finding 3: the independent semantic review checks derived MEANINGS by name, and the sample is enforced.

Each negative control plants one semantic error (or drops one annotation) in a copy and asserts the check fails.
"""
import copy
import json
import types
from pathlib import Path

import pytest

import compare_blind_g2 as cb
import semantic_review_g2 as sr
from audit import build

SAMPLE = json.loads((sr.DIR / "sample.json").read_text())


@pytest.fixture(scope="module")
def world():
    return build.build()


def _world_with(world, **over):
    base = {"ddr_by_file": dict(world.ddr_by_file), "dds_links": dict(world.dds_links), "cw": dict(world.cw)}
    for k, (key, obj) in over.items():
        base[k][key] = obj
    return types.SimpleNamespace(**base)


def test_semantic_review_complete_and_agrees(world):
    res = sr.run(world)
    assert res["failures"] == []
    assert {k: (res[k]["annotated"], res[k]["sampled"]) for k in ("ddr", "lines", "civil")} == {
        "ddr": (31, 31), "lines": (84, 84), "civil": (45, 45)}
    assert set(SAMPLE["must_lines"]) <= set(SAMPLE["lines"])


def test_wrong_tool_code_fails(world):
    f = next(f for f in SAMPLE["ddr"] if "gamma tool" in world.ddr_by_file[f].tools_in_hole)
    d = copy.deepcopy(world.ddr_by_file[f])
    d.tools_in_hole["gamma tool"] = "LH-714"                    # the loss code outside the loss context
    fails = sr.run(_world_with(world, ddr_by_file=(f, d)))["failures"]
    assert fails == [f"ddr {f} in_the_hole:gamma tool: review 'LW-410' parser 'LH-714'"]


def test_wrong_crew_count_fails(world):
    f = SAMPLE["ddr"][0]
    d = copy.deepcopy(world.ddr_by_file[f])
    d.crew["DD-101"] = d.crew["DD-101"] + 1
    fails = sr.run(_world_with(world, ddr_by_file=(f, d)))["failures"]
    assert len(fails) == 1 and fails[0].startswith(f"ddr {f} crew:DD-101:")


def test_mislabelled_civil_number_fails(world):
    t = next(t for t in SAMPLE["civil"] if "dia_mm" in world.cw[t].attributes)
    r = copy.deepcopy(world.cw[t])
    r.attributes = {"depth_m" if k == "dia_mm" else k: v for k, v in r.attributes.items()}   # right number, wrong name
    fails = sr.run(_world_with(world, cw=(t, r)))["failures"]
    assert sorted(fails) == sorted([f"civil {t} attribute:depth_m: review None parser '{r.attributes['depth_m']}'",
                                    f"civil {t} attribute:dia_mm: review '{r.attributes['depth_m']}' parser None"])


def test_wrong_invoice_to_tool_fact_fails(world):
    l = copy.deepcopy(world.dds_links["MDS-00022-030"])
    l.semantic["tool_in_hole"] = False                                # the DD-121 defect found in review, on one link
    fails = sr.run(_world_with(world, dds_links=("MDS-00022-030", l)))["failures"]
    assert fails == ["lines MDS-00022-030 tool_in_hole: review True parser False"]


def test_missing_sample_fails(world):
    ann = {k: sr._load(k) for k in sr.FIELDS}
    dropped = ann["lines"].pop(0)["line_ref"]
    fails = sr.run(world, annotations=ann)["failures"]
    assert fails == [f"lines: sampled {dropped} has no annotation"]


def test_missing_field_fails(world):
    ann = {k: sr._load(k) for k in sr.FIELDS}
    del ann["civil"][0]["candidate_items"]
    with pytest.raises(KeyError):                                 # the comparator cannot read what is missing ...
        sr.run(world, annotations=ann)
    assert sr.completeness("civil", ann["civil"], SAMPLE["civil"]) == [   # ... and completeness names it
        f"civil: {ann['civil'][0]['ticket']} lacks fields ['candidate_items']"]


def test_transcription_check_enforces_its_sample(tmp_path, monkeypatch):
    src = cb.BLIND
    for f in ("sample.json", "cw_annotations.jsonl"):
        (tmp_path / f).write_text((src / f).read_text())
    rows = (src / "dds_annotations.jsonl").read_text().splitlines()
    (tmp_path / "dds_annotations.jsonl").write_text("\n".join(rows[1:]) + "\n")
    monkeypatch.setattr(cb, "BLIND", tmp_path)
    assert cb.completeness() == [f"drilling: sampled {json.loads(rows[0])['file']} has no annotation"]
