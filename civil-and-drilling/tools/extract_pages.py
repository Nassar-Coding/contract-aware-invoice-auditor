"""Extract the scanned contract pages losslessly for review (G1 second verification).

Each PDF page holds one embedded grayscale scan. This writes it unchanged as PNG and checks the
decoded samples against ``source/pdf_pages.json`` so every reviewed image is provably the scan.

Usage::

    python tools/extract_pages.py [--out verification/pages] [--snapshot PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import snapshot

ROOT = Path(__file__).resolve().parents[1]


def extract(out: Path, snap: Path) -> int:
    import pymupdf

    frozen = json.loads((ROOT / "source" / "pdf_pages.json").read_text())
    n = 0
    for key, rel in snapshot.PDFS.items():
        doc = pymupdf.open(snap / rel)
        d = out / key.lower()
        d.mkdir(parents=True, exist_ok=True)
        for i, page in enumerate(doc):
            xref = page.get_images(full=True)[0][0]
            pix = pymupdf.Pixmap(doc, xref)
            expected = frozen[key]["pages"][i]["samples_sha256"]
            if hashlib.sha256(pix.samples).hexdigest() != expected:
                raise SystemExit(f"{key} p{i + 1}: scan samples differ from source/pdf_pages.json")
            pix.save(d / f"p{i + 1:02d}.png")
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "verification" / "pages")
    ap.add_argument("--snapshot", type=Path, default=snapshot.DEFAULT_SNAPSHOT)
    args = ap.parse_args()
    n = extract(args.out, args.snapshot)
    print(f"extracted {n} scan pages to {args.out} (all sample hashes match source/pdf_pages.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
