"""G0 snapshot freezing and verification.

Builds (``freeze``) or re-checks (``verify``) the pinned input snapshot:

* ``source/manifest.tsv``   - every tracked file: SHA-256, git blob SHA-1, bytes.
* ``source/pdf_pages.json``  - per-page identity of the two scanned contracts
  (SHA-256 of each page's embedded image samples, dimensions, colour space).
* ``source/inventory.json``  - populations, schemas, ID sets, joins and the
  read-only observations later gates depend on.

``verify`` recomputes everything from the snapshot bytes and fails on any
difference, missing file or extra file. It never modifies the snapshot.

Usage::

    python tools/snapshot.py verify [--snapshot PATH]
    python tools/snapshot.py freeze [--snapshot PATH]   # only to (re)create the frozen files
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "source"
PINNED_REPO = "https://github.com/majedzahrani3/invoice-auditing-level-2"
PINNED_COMMIT = "aef4924dc32506b4587de8b788b5a947e6beffec"
DEFAULT_SNAPSHOT = Path(
    os.environ.get("INVOICE_SNAPSHOT", ROOT.parent / ".inputs" / "civil-and-drilling")
)
PDFS = {
    "CW": "civilwork/contract/CW-2025-0417-CIV.pdf",
    "DDS": "drilling_services/contract/DDS-2025-118.pdf",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def list_files(snapshot: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(snapshot):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            out.append(Path(dirpath, name).relative_to(snapshot).as_posix())
    return sorted(out)


def build_manifest(snapshot: Path) -> list[tuple[str, str, str, int]]:
    rows = []
    for rel in list_files(snapshot):
        data = (snapshot / rel).read_bytes()
        rows.append((rel, sha256_bytes(data), git_blob_sha1(data), len(data)))
    return rows


def manifest_text(rows) -> str:
    lines = ["path\tsha256\tgit_blob_sha1\tbytes"]
    lines += [f"{p}\t{s}\t{g}\t{n}" for p, s, g, n in rows]
    return "\n".join(lines) + "\n"


def read_manifest(path: Path) -> list[tuple[str, str, str, int]]:
    rows = []
    with path.open(newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        next(reader)
        for p, s, g, n in reader:
            rows.append((p, s, g, int(n)))
    return rows


def pdf_pages(snapshot: Path) -> dict:
    import pymupdf  # pinned in requirements.txt

    result = {}
    for key, rel in PDFS.items():
        doc = pymupdf.open(snapshot / rel)
        pages = []
        text_chars = 0
        for i, page in enumerate(doc):
            text_chars += len(page.get_text())
            imgs = page.get_images(full=True)
            entry = {"page": i + 1, "images": len(imgs)}
            if len(imgs) == 1:
                pix = pymupdf.Pixmap(doc, imgs[0][0])
                entry.update(
                    width=pix.width,
                    height=pix.height,
                    colorspace=pix.colorspace.name if pix.colorspace else None,
                    samples_sha256=sha256_bytes(pix.samples),
                )
            pages.append(entry)
        result[key] = {"path": rel, "page_count": doc.page_count, "text_layer_chars": text_chars, "pages": pages}
    return result


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def inventory(snapshot: Path) -> dict:
    inv: dict = {"repo": PINNED_REPO, "commit": PINNED_COMMIT}
    files = list_files(snapshot)
    inv["file_count"] = len(files)
    inv["files_by_area"] = dict(sorted(Counter("/".join(f.split("/")[:2]) if "/" in f else f for f in files).items()))

    # --- civil ---------------------------------------------------------------
    ch_cols, ch = read_csv(snapshot / "civilwork/invoices/applications.csv")
    cl_cols, cl = read_csv(snapshot / "civilwork/invoices/application_lines.csv")
    crec = sorted(f for f in files if f.startswith("civilwork/records/"))
    crec_names = {Path(f).stem for f in crec}
    crec_sizes = [(snapshot / f).stat().st_size for f in crec]
    c_ids = [r["application_no"] for r in ch]
    c_line_ids = [r["line_ref"] for r in cl]
    c_refs = [r["record_ref"] for r in cl]
    inv["civil"] = {
        "headers": {"rows": len(ch), "columns": ch_cols, "unique_ids": len(set(c_ids))},
        "lines": {"rows": len(cl), "columns": cl_cols, "unique_line_refs": len(set(c_line_ids))},
        "header_ids_equal_line_ids": set(c_ids) == {r["application_no"] for r in cl},
        "distinct_item_codes": len({r["item_code"] for r in cl}),
        "item_codes": sorted({r["item_code"] for r in cl}),
        "units_by_code": {k: sorted(v) for k, v in _group(cl, "item_code", "unit").items()},
        "contract_ref_counts": dict(Counter(r["contract_ref"] for r in ch)),
        "nonzero_adjustment": sum(1 for r in ch if r["adjustment"] != "0.00"),
        "nonzero_retention_released": sum(1 for r in ch if r["retention_released"] != "0.00"),
        "application_date_range": [min(r["application_date"] for r in ch), max(r["application_date"] for r in ch)],
        "work_date_range": [min(r["work_date"] for r in cl), max(r["work_date"] for r in cl)],
        "records": {
            "files": len(crec),
            "by_family": dict(sorted(Counter(Path(f).stem.split("-")[0] for f in crec).items())),
            "total_bytes": sum(crec_sizes),
            "largest_bytes": max(crec_sizes),
        },
        "lines_with_record_ref": sum(1 for x in c_refs if x),
        "lines_blank_record_ref": sum(1 for x in c_refs if not x),
        "record_refs_without_file": sorted({x for x in c_refs if x and x not in crec_names}),
        "headers_on_2026_05_12": sorted(r["application_no"] for r in ch if r["application_date"] == "2026-05-12"),
    }

    # --- drilling ------------------------------------------------------------
    dh_cols, dh = read_csv(snapshot / "drilling_services/invoices/invoices.csv")
    dl_cols, dl = read_csv(snapshot / "drilling_services/invoices/invoice_lines.csv")
    drec = sorted(f for f in files if f.startswith("drilling_services/records/"))
    drec_sizes = [(snapshot / f).stat().st_size for f in drec]
    d_ids = [r["invoice_no"] for r in dh]
    inv["drilling"] = {
        "headers": {"rows": len(dh), "columns": dh_cols, "unique_ids": len(set(d_ids))},
        "lines": {"rows": len(dl), "columns": dl_cols, "unique_line_refs": len({r["line_ref"] for r in dl})},
        "header_ids_equal_line_ids": set(d_ids) == {r["invoice_no"] for r in dl},
        "distinct_service_codes": len({r["service_code"] for r in dl}),
        "service_codes": sorted({r["service_code"] for r in dl}),
        "units_by_code": {k: sorted(v) for k, v in _group(dl, "service_code", "unit").items()},
        "contract_ref_counts": dict(Counter(r["contract_ref"] for r in dh)),
        "well_class_counts": dict(Counter(r["well_class"] for r in dh)),
        "nonzero_adjustment": sum(1 for r in dh if r["adjustment"] != "0.00"),
        "records": {
            "files": len(drec),
            "total_bytes": sum(drec_sizes),
            "largest_bytes": max(drec_sizes),
        },
        "lines_blank_report_ref": sum(1 for r in dl if not r["report_ref"]),
        "blank_report_ref_codes": dict(Counter(r["service_code"] for r in dl if not r["report_ref"])),
        "ds900_lines": sum(1 for r in dl if r["service_code"] == "DS-900"),
        "headers_on_17_Aug_2026": sorted(r["invoice_no"] for r in dh if r["invoice_date"] == "17-Aug-2026"),
        "headers_on_18_Aug_2026": sorted(r["invoice_no"] for r in dh if r["invoice_date"] == "18-Aug-2026"),
    }

    # --- template ------------------------------------------------------------
    t_cols, t = read_csv(snapshot / "submission_template.csv")
    t_ids = [r["invoice_id"] for r in t]
    inv["template"] = {
        "rows": len(t),
        "columns": t_cols,
        "unique_ids": len(set(t_ids)),
        "ids_equal_header_ids": set(t_ids) == set(c_ids) | set(d_ids),
        "order_is_civil_then_drilling_in_file_order": t_ids == c_ids + d_ids,
        "all_other_fields_blank": all(not any(r[c] for c in t_cols[1:]) for r in t),
    }
    return inv


def _group(rows, key, val):
    out: dict[str, set] = {}
    for r in rows:
        out.setdefault(r[key], set()).add(r[val])
    return dict(sorted(out.items()))


def git_head(snapshot: Path) -> str | None:
    if not (snapshot / ".git").exists():
        return None
    return subprocess.run(["git", "-C", str(snapshot), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def git_tree_blobs(snapshot: Path) -> dict[str, str] | None:
    if not (snapshot / ".git").exists():
        return None
    out = subprocess.run(
        ["git", "-C", str(snapshot), "ls-tree", "-r", PINNED_COMMIT], capture_output=True, text=True, check=True
    ).stdout
    blobs = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        blobs[path] = meta.split()[2]
    return blobs


def verify(snapshot: Path) -> list[str]:
    errors: list[str] = []
    head = git_head(snapshot)
    if head is not None and head != PINNED_COMMIT:
        errors.append(f"snapshot HEAD {head} != pinned {PINNED_COMMIT}")

    frozen = read_manifest(SOURCE_DIR / "manifest.tsv")
    current = build_manifest(snapshot)
    fz = {r[0]: r for r in frozen}
    cu = {r[0]: r for r in current}
    for p in sorted(set(fz) - set(cu)):
        errors.append(f"missing file: {p}")
    for p in sorted(set(cu) - set(fz)):
        errors.append(f"extra file: {p}")
    for p in sorted(set(fz) & set(cu)):
        if fz[p] != cu[p]:
            errors.append(f"hash/size mismatch: {p}")

    blobs = git_tree_blobs(snapshot)
    if blobs is not None:
        if set(blobs) != set(fz):
            errors.append("pinned git tree path set differs from manifest")
        for p, blob in blobs.items():
            if p in fz and fz[p][2] != blob:
                errors.append(f"git blob mismatch vs pinned commit: {p}")

    for name, fn in (("pdf_pages.json", pdf_pages), ("inventory.json", inventory)):
        expected = json.loads((SOURCE_DIR / name).read_text())
        actual = json.loads(json.dumps(fn(snapshot)))
        if expected != actual:
            diff = [k for k in set(expected) | set(actual) if expected.get(k) != actual.get(k)]
            errors.append(f"{name} differs in keys: {sorted(diff)}")
    return errors


def freeze(snapshot: Path) -> None:
    head = git_head(snapshot)
    if head is not None and head != PINNED_COMMIT:
        sys.exit(f"refusing to freeze: HEAD {head} != pinned {PINNED_COMMIT}")
    SOURCE_DIR.mkdir(exist_ok=True)
    (SOURCE_DIR / "manifest.tsv").write_text(manifest_text(build_manifest(snapshot)))
    (SOURCE_DIR / "pdf_pages.json").write_text(json.dumps(pdf_pages(snapshot), indent=1) + "\n")
    (SOURCE_DIR / "inventory.json").write_text(json.dumps(inventory(snapshot), indent=1) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["verify", "freeze"])
    ap.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = ap.parse_args()
    if args.command == "freeze":
        freeze(args.snapshot)
        print("frozen:", ", ".join(str(p.relative_to(ROOT)) for p in sorted(SOURCE_DIR.iterdir())))
        return 0
    errors = verify(args.snapshot)
    frozen = read_manifest(SOURCE_DIR / "manifest.tsv")
    if errors:
        print("SNAPSHOT VERIFY FAILED")
        for e in errors[:50]:
            print("  -", e)
        return 1
    print(f"SNAPSHOT VERIFY OK: {len(frozen)} files match manifest (sha256 + git blob), "
          f"HEAD={git_head(args.snapshot) or 'n/a (no .git)'}; pdf_pages.json and inventory.json reproduce")
    return 0


if __name__ == "__main__":
    sys.exit(main())
