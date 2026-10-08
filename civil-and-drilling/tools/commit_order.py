"""The order in which review packets, independent readers' results and engine code were first committed.

Three gate checks (X1 in verify_g3, Y1 in verify_g4, Z5 in verify_g5) prove an ordering: each review packet was
committed before the readers' expected results computed from it, and, for G4 and G5, those results before the engine
they were compared with. The ordering is a fact about the original development history (see Provenance in the
top-level README), recorded here so the checks run on any clone of this project.

  python tools/commit_order.py check --source PATH    re-derive the record from a clone of that repository; exit 1 on
                                                        any difference
  python tools/commit_order.py record --source PATH   rewrite the record (only if the history is meant to change)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "verification" / "commit_order.json"
SOURCE_REPOSITORY = "the original development repository (see Provenance in the top-level README)"
SCOPE = ("audit", "verification/g3/cases", "verification/g4/histories", "verification/g5/samples")


@lru_cache(maxsize=1)
def _record() -> dict:
    return json.loads(RECORD.read_text())


def first_commit(path: str) -> int | None:
    """The position (1 = the first commit) of the commit that first added `path` in the original linear history, or None
    if the record has no such file."""
    return _record()["first_commit"].get(path)


def committed_before(a: int | None, b: int | None) -> bool:
    """True when position a strictly precedes position b in the original (linear) history."""
    if a is None or b is None:
        return False
    return a < b


def derive(source: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, check=True).stdout
    if git("rev-list", "--merges", "HEAD").strip():
        raise SystemExit("the source history has merges; positions would not express ancestry")
    commits = git("rev-list", "--reverse", "HEAD").split()
    position = {c: i + 1 for i, c in enumerate(commits)}
    paths = sorted(str(p.relative_to(ROOT)) for scope in SCOPE for p in (ROOT / scope).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts)
    first = {}
    for path in paths:
        added = git("log", "--diff-filter=A", "--format=%H", "--", path).split()
        if added:
            first[path] = position[added[-1]]
    return {"source_repository": SOURCE_REPOSITORY, "commits": len(commits), "first_commit": first}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "record"])
    ap.add_argument("--source", type=Path, required=True, help="a clone of the original development repository")
    args = ap.parse_args()
    fresh = derive(args.source.resolve())
    if args.action == "record":
        RECORD.write_text(json.dumps(fresh, indent=1, sort_keys=True) + "\n")
        print(f"recorded {len(fresh['first_commit'])} files")
        return 0
    same = fresh == json.loads(RECORD.read_text())
    print(f"COMMIT ORDER {'OK' if same else 'DIFFERS'}: {len(fresh['first_commit'])} files, {fresh['commits']} commits in the source history")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
