"""Run/version context for every G2 derived fact (review finding 4).

Each derived fact carries its source span and the version of the extraction that made it.
Source spans live on the facts (Source, spans). The version is this run context: a hash over
  * the implementation: every module of the audit package and the spec loader it uses (tools/spec_lib.py,
    tools/snapshot.py);
  * the reviewed inputs it reads: the evidence specifications, the verified terms files and instruments (Appendix G,
    Schedules 5 and 8, work areas, ground classes, record series ...);
  * the evidence it reads: the pinned snapshot commit and the G0 manifest that certifies its files.
Every derived fact (claim row, record, report, run, well span, link, unresolved entry, conflict) carries the
context id in its `ctx` field, and the full context is written to verification/g2/run_context.json and into
coverage.json. A change to any of these files - including a code-only change - changes the id, so the
committed outputs no longer reproduce until they are regenerated (G2 check S2).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .common import ROOT, snapshot

CODE_GLOBS = ["audit/*.py"]
CODE_FILES = ["tools/spec_lib.py", "tools/snapshot.py"]
REVIEWED_INPUTS = ["spec/evidence_cw.yaml", "spec/evidence_dds.yaml", "spec/terms_cw.yaml", "spec/terms_dds.yaml",
                   "spec/instruments.yaml"]
EVIDENCE_IDENTITY = ["source/manifest.tsv"]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_context(root: Path = ROOT) -> dict:
    code = sorted({str(p.relative_to(root)) for g in CODE_GLOBS for p in root.glob(g)} | set(CODE_FILES))
    ctx = {
        "code": {f: _sha(root / f) for f in code},
        "reviewed_inputs": {f: _sha(root / f) for f in REVIEWED_INPUTS},
        "snapshot": {"repo": snapshot.PINNED_REPO, "commit": snapshot.PINNED_COMMIT,
                     **{f: _sha(root / f) for f in EVIDENCE_IDENTITY}},
    }
    ctx_id = hashlib.sha256(json.dumps(ctx, sort_keys=True).encode()).hexdigest()[:16]
    return {"id": ctx_id, **ctx}


def facts(world) -> list:
    """Every derived fact object of a built world (the objects that must reference the run context)."""
    out = [r for rows in world.claims.rows.values() for r in rows]
    out += list(world.cw.values()) + list(world.ddr_by_file.values()) + list(world.runs.values()) + list(world.wells.values())
    out += list(world.cw_links.values()) + list(world.dds_links.values())
    out += list(world.queue.items) + list(world.queue.conflicts)
    return out


def stamp(world, ctx_id: str) -> None:
    for f in facts(world):
        f.ctx = ctx_id
