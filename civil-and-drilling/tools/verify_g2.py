"""G2 exit checks. One line per exit condition.

E1 Every source row/file is accounted for.
E2 Each required field is parsed or explicitly unresolved.
E3 Reference validity and semantic validity are separate.
E4 Reviewed examples cover every record family and encountered wording/layout branch.
Also: blind transcription agrees (S1); per-service tool presence explained (S3); independent semantic review
of derived meanings agrees by name with the sample complete (S4); committed outputs reproduce (S2).

Usage::  python tools/verify_g2.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from audit import build, claims, links, records_cw, records_dds  # noqa: E402
from audit.common import SNAPSHOT  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "g2_reviewed.yaml"
INVENTORY = json.loads((ROOT / "source" / "inventory.json").read_text())


# ---------------------------------------------------------------- branch definitions
def cw_record_branches(r) -> set[str]:
    b = {f"cw.family:{r.family}", f"cw.rule:{r.rule}", "cw.ground:stated" if r.ground else "cw.ground:absent",
         "cw.layout:weekly" if r.week_beginning else "cw.layout:daily"}
    if r.week_beginning:
        b.add(f"cw.days_on:{len(r.days_on)}")
        months = {(d.year, d.month) for d in r.days_on + [r.week_beginning]}
        if len(months) > 1:
            b.add("cw.week_crosses_month")
        if len({y for y, _ in months}) > 1:
            b.add("cw.week_crosses_year")
    if not r.foreman_signed:
        b.add("cw.foreman_placeholder")
    if not r.engineer_signed:
        b.add("cw.engineer_placeholder")
    return b


def ddr_branches(d, conflicts) -> set[str]:
    a, bb = d.parts.get("A", {}), d.parts.get("B", {})
    b = {f"ddr.parts:{''.join(sorted(d.parts))}", f"ddr.status:{a.get('Status')}", f"ddr.section:{a.get('Hole section')}",
         f"ddr.source_carried:{bb.get('Radioactive source carried')}"}
    b |= {f"ddr.tool:{t}" for t in d.tools_in_hole} | {f"ddr.crew:{t}" for t in d.crew_terms}
    if d.lost_tool_term:
        b.add(f"ddr.lost:{d.lost_tool_term}")
    if not d.company_signed:
        b.add("ddr.company_placeholder")
    if not d.driller_signed:
        b.add("ddr.driller_placeholder")
    b |= {f"ddr.conflict:{c}" for c in conflicts.get(d.file, ())}
    return b


def claim_branches(kind, row) -> set[str]:
    v = row.values
    if kind == "cw_headers":
        return {f"cw_header.contract_ref:{v['contract_ref']}"}
    if kind == "dds_headers":
        return {f"dds_header.contract_ref:{v['contract_ref']}"}
    if kind == "cw_lines":
        return {f"cw_line.record_ref:{'set' if v['record_ref'] else 'blank'}", f"cw_line.ground:{'set' if v['ground_class'] else 'blank'}",
                f"cw_line.night:{v['night_work']}"}
    code = v["service_code"]
    return {f"dds_line.kind:{'DS-900' if code == 'DS-900' else 'PD-210' if code == 'PD-210' else 'service'}"}


def link_branches(prefix, link) -> set[str]:
    b = {f"{prefix}.reference:{link.reference}"}
    sem = link.semantic
    for k in ("date_match", "work_date_is_day_on", "required_part_present"):
        if sem.get(k) is False:
            b.add(f"{prefix}.semantic:{k}=False")
    if "tool_in_hole" in sem:            # tool presence outcome by kind of basis (own term / contract substitute / none)
        code = link.line_code
        kind = "none" if sem["tool_basis_code"] is None else ("own" if sem["tool_basis_code"] == code else "substitute")
        b.add(f"{prefix}.tool:{kind}={sem['tool_in_hole']}")
    if "crew_recorded" in sem:
        b.add(f"{prefix}.crew_recorded:{'0' if sem['crew_recorded'] == 0 else '>0'}")
    if "lost_tool_code" in sem:
        b.add(f"{prefix}.lost_tool_code:{'set' if sem['lost_tool_code'] else 'none'}")
    return b


def carried_items_check(w, register: dict, rule_ids: set, question_ids: set) -> list[str]:
    """Every conflict and every missing required Schedule 5 part is registered with owner, rule, question, treatment."""
    errs = []
    reg_conf = {(it["check"], i) for it in register["items"] if it["kind"] == "conflict" for i in it["idents"]}
    reg_lines = {l for it in register["items"] for l in it.get("lines", [])}
    for c in w.queue.conflicts:
        if (c.check, c.ident) not in reg_conf:
            errs.append(f"conflict {c.check} {c.ident} is not registered in spec/carried_items.yaml")
    current = {(c.check, c.ident) for c in w.queue.conflicts}
    errs += [f"registered conflict {c} {i} no longer occurs" for c, i in sorted(reg_conf - current)]
    for ident, l in w.dds_links.items():
        if l.semantic.get("required_part_present") is False and ident not in reg_lines:
            errs.append(f"line {ident} lacks its Schedule 5 part {l.semantic['required_part']} and is not registered")
    for it in register["items"]:              # stale-entry check for missing-part items
        if it["kind"] == "missing_required_part" or it.get("check") == "gyro_surveys_without_part_C":
            for l in it.get("lines", []):
                link = w.dds_links.get(l)
                if link is not None and link.semantic.get("required_part_present") is not False:
                    errs.append(f"{it['id']}: registered line {l} no longer lacks its Schedule 5 part")
    for it in register["items"]:
        if it.get("owner_gate") not in {"G3", "G4", "G5", "G6", "G7"} or not it.get("treatment"):
            errs.append(f"{it['id']}: owner gate or treatment missing")
        errs += [f"{it['id']}: unknown rule {r}" for r in it.get("rules", []) if r not in rule_ids]
        if it.get("question") and it["question"] not in question_ids:
            errs.append(f"{it['id']}: unknown question {it['question']}")
        for l in it.get("lines", []):
            if l not in w.dds_links:
                errs.append(f"{it['id']}: unknown line {l}")
    return errs


def provenance_check(w) -> list[str]:
    """Every derived fact references the run context, and the context covers every module of the audit package."""
    from audit import provenance
    ctx = w.run_context
    errs = [f"{type(f).__name__} without the run context" for f in provenance.facts(w) if f.ctx != ctx["id"]][:5]
    modules = {str(p.relative_to(ROOT)) for p in (ROOT / "audit").glob("*.py")}
    errs += [f"run context does not cover {m}" for m in sorted(modules - set(ctx["code"]))]
    if provenance.run_context() != ctx:
        errs.append("run context not reproducible")
    if json.loads((build.OUT / "run_context.json").read_text()) != ctx:
        errs.append("verification/g2/run_context.json does not match the current code and inputs")
    return errs


# ---------------------------------------------------------------- checks
def main() -> int:
    w = build.build()
    fix = yaml.safe_load(FIXTURES.read_text())
    res = []

    # E1 -------------------------------------------------------------------------------------
    errs = []
    inv = {"cw_headers": INVENTORY["civil"]["headers"]["rows"], "cw_lines": INVENTORY["civil"]["lines"]["rows"],
           "dds_headers": INVENTORY["drilling"]["headers"]["rows"], "dds_lines": INVENTORY["drilling"]["lines"]["rows"]}
    for kind, rows in w.claims.rows.items():
        with (SNAPSHOT / claims.FILES[kind]).open(newline="") as fh:
            raw_rows = sum(1 for _ in csv.reader(fh)) - 1
        if not (len(rows) == raw_rows == inv[kind]):
            errs.append(f"{kind}: loaded {len(rows)}, file {raw_rows}, inventory {inv[kind]}")
        if any(r.source.path != claims.FILES[kind] or r.raw is None for r in rows):
            errs.append(f"{kind}: row without provenance")
        if [r.source.line for r in rows] != list(range(2, len(rows) + 2)):
            errs.append(f"{kind}: CSV line numbers not contiguous")
    for k, ok in w.claims.assertions.items():
        if not ok:
            errs.append(f"input assertion failed: {k}")
    cw_files = sorted(p.stem for p in (SNAPSHOT / "civilwork" / "records").iterdir())
    dds_files = sorted(p.name for p in (SNAPSHOT / "drilling_services" / "records").iterdir())
    if sorted(w.cw) != cw_files or len(cw_files) != INVENTORY["civil"]["records"]["files"]:
        errs.append("civil record files not all parsed")
    if sorted(w.ddr_by_file) != dds_files or len(dds_files) != INVENTORY["drilling"]["records"]["files"]:
        errs.append("drilling report files not all parsed")
    if sorted(d.file for d in w.ddr.values()) != dds_files:
        errs.append("report-number index does not hold every drilling report exactly once")
    if any(rid != d.report for rid, d in w.ddr.items()):
        errs.append("report index not keyed by internal report number")
    acc_cw, acc_dd = links.accounting(w.cw_links, w.cw), links.accounting(w.dds_links, w.ddr)
    if len(w.cw_links) != inv["cw_lines"] or len(w.dds_links) != inv["dds_lines"]:
        errs.append("not every line has a link object")
    res.append((f"E1 every source row/file accounted for: rows {sum(len(v) for v in w.claims.rows.values())} "
                f"(cw 900+7746, dds 1906+91244) with file/line provenance; records cw {len(w.cw)}/2169, dds {len(w.ddr_by_file)}/8151 "
                f"indexed by report number {len(w.ddr)}; files cited cw {acc_cw['cited']}, dds {acc_dd['cited']}, "
                f"unreferenced cw {len(acc_cw['unreferenced'])}, dds {len(acc_dd['unreferenced'])}", errs))

    # E2 -------------------------------------------------------------------------------------
    errs = []
    q = w.queue
    n_fields = 0
    for kind, rows in w.claims.rows.items():
        for r in rows:
            for k in claims.required_fields(kind, r.raw):
                n_fields += 1
                if r.values.get(k) in (None, "") and not q.has(r.ident, k):
                    errs.append(f"{kind} {r.ident}.{k} empty and not queued")
    for t, r in w.cw.items():
        need = {"family": r.family, "Area": r.area, "narrative": r.rule, "Signed (foreman)": r.foreman,
                "Countersigned (Engineer's representative)": r.engineer}
        need.update({"Week beginning": r.week_beginning, "Days on": r.days_on or None} if r.family == "DW" else {"Date": r.date})
        if r.family in ("DX", "PT"):
            need["Ground"] = r.ground
        for k, v in need.items():
            n_fields += 1
            if v is None and not (q.has(t, k) or q.has(t, "title")):
                errs.append(f"cw {t}.{k} empty and not queued")
    for f, d in w.ddr_by_file.items():
        for k in ("report", "contract", "well", "rig", "date", "company_rep", "lead_dd"):
            n_fields += 1
            if getattr(d, k) is None and not any(u.ident == f for u in q.items):
                errs.append(f"ddr {f}.{k} empty and not queued")
        for p, vals in d.parts.items():
            for k in records_dds.part_keys(p):
                n_fields += 1
                if vals.get(k) is None and not q.has(f, f"{p}.{k}"):
                    errs.append(f"ddr {f}.{p}.{k} empty and not queued")
        for t, code in list(d.tools_in_hole.items()) + list(d.tools_in_run.items()):
            if code is None and not any(u.ident == f and "Appendix G" in u.reason for u in q.items):
                errs.append(f"ddr {f}: term {t} unmapped and not queued")
    res.append((f"E2 each required field parsed or explicitly unresolved: {n_fields} required fields checked; "
                f"unresolved queue {len(q.items)}; evidence conflicts kept visible {len(q.conflicts)}", errs))

    # E3 -------------------------------------------------------------------------------------
    errs = []
    for prefix, lk, states in (("cw", w.cw_links, links.CW_REFERENCE_STATES), ("dds", w.dds_links, links.DDS_REFERENCE_STATES)):
        for ident, l in lk.items():
            if l.reference not in states:
                errs.append(f"{prefix} {ident}: unknown reference state {l.reference}")
            if l.reference in links.RESOLVED_STATES and not l.semantic:
                errs.append(f"{prefix} {ident}: resolved without semantic facts")
            if l.reference not in links.RESOLVED_STATES and l.semantic:
                errs.append(f"{prefix} {ident}: semantic facts on an unresolved reference")
            if "reference" in l.semantic:
                errs.append(f"{prefix} {ident}: reference state mixed into semantic facts")
    resolved_but_mismatched = sum(1 for l in list(w.cw_links.values()) + list(w.dds_links.values())
                                  if l.reference == "resolved" and any(v is False for v in l.semantic.values()))
    ref_counts = Counter(l.reference for l in w.cw_links.values()) + Counter(f"dds:{l.reference}" for l in w.dds_links.values())
    res.append((f"E3 reference validity separate from semantic validity: states {dict(sorted(ref_counts.items()))}; "
                f"{resolved_but_mismatched} resolved references carry at least one semantic mismatch (kept as facts)", errs))

    # E4 -------------------------------------------------------------------------------------
    errs = []
    conflicts = {}
    for c in q.conflicts:
        conflicts.setdefault(c.ident, set()).add(c.check)
    corpus, covered = set(), set()
    for r in w.cw.values():
        corpus |= cw_record_branches(r)
    for t in fix["civil_records"]:
        covered |= cw_record_branches(w.cw[t])
    for d in w.ddr_by_file.values():
        corpus |= ddr_branches(d, conflicts)
    for f in fix["drilling_reports"]:
        covered |= ddr_branches(w.ddr_by_file[f], conflicts)
    for kind, rows in w.claims.rows.items():
        by_id = {r.ident: r for r in rows}
        for r in rows:
            corpus |= claim_branches(kind, r)
        for ident in fix["claims"][kind]:
            covered |= claim_branches(kind, by_id[ident])
    codes = {r.ident: r.values.get("service_code") for r in w.claims.rows["dds_lines"]}
    for l in w.dds_links.values():
        l.line_code = codes[l.line_ref]
    for l in w.cw_links.values():
        l.line_code = None
    for prefix, lk in (("cw_link", w.cw_links), ("dds_link", w.dds_links)):
        for l in lk.values():
            corpus |= link_branches(prefix, l)
        for ident in fix["links"][prefix.split("_")[0]]:
            covered |= link_branches(prefix, lk[ident])
    missing = sorted(corpus - covered)
    errs += [f"branch without a reviewed fixture: {m}" for m in missing]
    fams = {b for b in corpus if b.startswith("cw.family:")}
    parts = {b for b in corpus if b.startswith("ddr.parts:")}
    res.append((f"E4 reviewed fixtures cover every family and encountered wording/layout branch: {len(corpus & covered)}/{len(corpus)} "
                f"branches ({len(fams)} civil families, {sum(b.startswith('cw.rule:') for b in corpus)} narrative templates, "
                f"{len(parts)} DDR part layouts, {sum(b.startswith('ddr.tool:') for b in corpus)} tool terms); "
                f"fixtures: {len(fix['civil_records'])} civil, {len(fix['drilling_reports'])} DDR, "
                f"{sum(len(v) for v in fix['claims'].values())} claim rows, {sum(len(v) for v in fix['links'].values())} links", errs))

    # Independent blind annotation: TRANSCRIPTION of raw fields (supporting evidence for E2; not semantic proof) ----
    errs = []
    import compare_blind_g2 as cb
    cmp = {"cw": cb.compare_cw(w.cw), "dds": cb.compare_dds(w.ddr_by_file)}
    errs += cb.completeness()
    for k, v in cmp.items():
        errs += [f"{k} {x['id']} {x['field']}: blind {x['blind']!r} parser {x['parser']!r}" for x in v if not x["agree"]]
    res.append((f"S1 blind transcription agrees with the parser, sample complete: civil {sum(x['agree'] for x in cmp['cw'])}/{len(cmp['cw'])} fields "
                f"({len({x['id'] for x in cmp['cw']})} records), DDR {sum(x['agree'] for x in cmp['dds'])}/{len(cmp['dds'])} fields "
                f"({len({x['id'] for x in cmp['dds']})} reports)", errs))

    # Independent SEMANTIC review of derived meanings (review finding 3) ---------------------------
    import semantic_review_g2 as sr
    sem = sr.run(w)
    errs = list(sem["failures"])
    committed = json.loads((sr.DIR / "comparison.json").read_text())
    if json.loads(json.dumps(sem, default=str)) != committed:
        errs.append("verification/g2/semantic/comparison.json does not reproduce")
    res.append(("S4 independent semantic review agrees by name, every sampled item annotated: "
                + "; ".join(f"{k} {sem[k]['annotated']}/{sem[k]['sampled']} items, {sem[k]['agree']}/{sem[k]['fields']} fields"
                            + (f" ({sem[k]['disposed']} disposed)" if sem[k]["disposed"] else "")
                            for k in ("ddr", "lines", "civil"))
                + " (tool term->code, crew by service, invoice->tool/part/crew/LH, civil attributes by name, candidate items)", errs))

    # Tool presence per service (review finding 2) ------------------------------------------------
    pop = links.tool_presence_population(w.dds_links, w.claims.rows["dds_lines"])
    errs = links.tool_presence_failures(pop)
    high = {c: f"{p['false'] + p['null']}/{p['lines']}" for c, p in pop.items() if p["share_not_established"] >= links.NOT_ESTABLISHED_SHARE}
    low = {c: f"{p['false'] + p['null']}/{p['lines']}" for c, p in pop.items() if 0 < p["share_not_established"] < links.NOT_ESTABLISHED_SHARE}
    res.append((f"S3 tool presence per tool-day service ({len(pop)} codes): not established >=90% only where explained "
                f"{ {c: (h, links.TOOL_PRESENCE['no_tool'][c]['question']) for c, h in high.items() if c in links.TOOL_PRESENCE['no_tool']} }; "
                f"substitutes {dict((k, v) for k, v in links.TOOL_BASIS.items() if v not in (k, None))}; other codes not established {low or 0}", errs))

    # Provenance context and carried items (review finding 4) -----------------------------------------
    from audit import provenance
    register = yaml.safe_load((ROOT / "spec" / "carried_items.yaml").read_text())
    rules_ids = {r["id"] for r in yaml.safe_load((ROOT / "spec" / "rules.yaml").read_text())["rules"]}
    qids = {q["id"] for q in yaml.safe_load((ROOT / "spec" / "open_questions.yaml").read_text())["questions"]}
    errs = provenance_check(w) + carried_items_check(w, register, rules_ids, qids)
    n_facts = len(provenance.facts(w))
    res.append((f"S5 every derived fact ({n_facts}) references run context {w.run_context['id']} (code {len(w.run_context['code'])} files, "
                f"reviewed inputs {len(w.run_context['reviewed_inputs'])}, snapshot {w.run_context['snapshot']['commit'][:8]}); "
                f"carried items registered with owner gate and treatment: "
                + ", ".join(f"{it['id']} {it['check'].split(' ')[0]} x{len(it['idents'])} -> {it['owner_gate']}/{it.get('question')}" for it in register["items"]), errs))

    # Committed outputs reproduce --------------------------------------------------------------
    errs = []
    cov = json.loads(json.dumps(build.coverage(w), default=build._j))
    for name, obj in (("run_context.json", w.run_context), ("coverage.json", cov), ("unresolved.json", json.loads(json.dumps(q.items, default=build._j))),
                      ("conflicts.json", json.loads(json.dumps(q.conflicts, default=build._j)))):
        if json.loads((build.OUT / name).read_text()) != obj:
            errs.append(f"verification/g2/{name} does not reproduce")
    res.append(("S2 committed G2 outputs reproduce from the snapshot (coverage, unresolved, conflicts)", errs))

    ok = True
    for name, e in res:
        print(("PASS " if not e else "FAIL ") + name)
        for x in e[:25]:
            print("     -", x)
        ok &= not e
    print("G2 VERIFY " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
