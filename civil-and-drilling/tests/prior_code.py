"""Earlier versions of modules, kept as fixtures so a regression test can run a fixed defect through the code that had
it and show the defect is real. Each fixture holds modules as they stood at an earlier point of the original development
history (see Provenance in the top-level README), under a name for that point."""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prior_code"


def source(rev: str, path: str) -> str:
    fixture = FIXTURES / rev
    assert fixture.is_dir(), f"no prior-code fixture named {rev}"
    return (fixture / path).read_text()
