"""Earlier versions of modules, kept as fixtures so a regression test can run a fixed defect through the code that had
it and show the defect is real. Each fixture is stored under the commit it was taken from in the original development
repository (see Provenance in the top-level README)."""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prior_code"


def source(rev: str, path: str) -> str:
    matches = [d for d in FIXTURES.iterdir() if d.name.startswith(rev)]
    assert len(matches) == 1, f"no unique prior-code fixture for {rev}"
    return (matches[0] / path).read_text()
