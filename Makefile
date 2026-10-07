# One entry point for both auditors. Every target runs the auditor's own commands from inside its directory;
# nothing here adds behaviour the auditors do not already have.
#
#   make hospital-inputs  hospital claims: clone the pinned input dataset into hospital-claims/data/source/
#   make hospital         hospital claims: reproduce, verify and independently validate audit_results.csv (~1.5 min)
#   make hospital-test    hospital claims: the 175-test suite (~1 min)
#   make hospital-eval    hospital claims: score Hospital 1's development partition against its labels (~1.5 min)
#   make setup            .venv with the civil auditor's pinned dependencies (the hospital auditor needs only the
#                         standard library)
#   make civil-inputs     civil works and drilling: clone the pinned input dataset into .inputs/civil-and-drilling/
#   make civil            civil works and drilling: reproduce and confirm byte-identical outputs (~20 min)
#   make civil-verify     civil works and drilling: every gate check, the reader comparisons and the tests (~35 min)
#   make all              all of the above
#
# Variables: PYTHON (a Python 3.12 interpreter; default python3.12), VENV (default .venv),
#            INVOICE_SNAPSHOT (a git clone of the civil inputs; default .inputs/civil-and-drilling).

PYTHON ?= python3.12
VENV   ?= .venv
PY     := $(abspath $(VENV))/bin/python
STAMP  := $(VENV)/.installed

HOSPITAL := hospital-claims
CIVIL    := civil-and-drilling

HOSPITAL_INPUTS_REPO   := https://github.com/majedzahrani3/insurance_auditing
HOSPITAL_INPUTS_COMMIT := 6fee1da60b74512156637a22be15d996a36627e1
HOSPITAL_INPUTS        := $(HOSPITAL)/data/source

CIVIL_INPUTS_REPO   := https://github.com/majedzahrani3/invoice-auditing-level-2
CIVIL_INPUTS_COMMIT := aef4924dc32506b4587de8b788b5a947e6beffec
INVOICE_SNAPSHOT ?= .inputs/civil-and-drilling
override INVOICE_SNAPSHOT := $(abspath $(INVOICE_SNAPSHOT))
export INVOICE_SNAPSHOT

.PHONY: help hospital-inputs hospital hospital-test hospital-eval setup civil-inputs civil civil-verify all
.NOTPARALLEL:

help:
	@grep -E '^#   make |^#                 |^# Variables|^#            ' Makefile | sed 's/^# \{0,3\}//'

hospital-inputs: $(HOSPITAL_INPUTS)/.git

$(HOSPITAL_INPUTS)/.git:
	git clone --quiet $(HOSPITAL_INPUTS_REPO) $(HOSPITAL_INPUTS)
	git -C $(HOSPITAL_INPUTS) checkout --quiet $(HOSPITAL_INPUTS_COMMIT)

# The hospital auditor runs on the standard library alone, so its targets use $(PYTHON) directly.
hospital: hospital-inputs
	cd $(HOSPITAL) && PYTHONPATH=src $(PYTHON) -m insurance_audit reproduce
	cd $(HOSPITAL) && PYTHONPATH=src $(PYTHON) -m insurance_audit verify-results
	cd $(HOSPITAL) && $(PYTHON) tools/validate_results.py
	cd $(HOSPITAL) && git diff --stat --exit-code HEAD -- audit_results.csv && echo "HOSPITAL REPRODUCED: audit_results.csv byte-identical"

hospital-test: hospital-inputs
	cd $(HOSPITAL) && $(PYTHON) tools/run_checks.py local

hospital-eval: hospital-inputs
	cd $(HOSPITAL) && PYTHONPATH=src $(PYTHON) -m insurance_audit reproduce --evaluate-development
	cd $(HOSPITAL) && $(PYTHON) tools/score_development.py --export-current-h1 runs/h1_development_export.csv \
	    --partition development --output runs/score_development.json
	@cd $(HOSPITAL) && $(PYTHON) -c "import json; d = json.load(open('runs/score_development.json')); \
	    print({k: d[k] for k in ('population', 'erroneous', 'tp', 'fn', 'fp', 'recall', 'precision')}, \
	    'amount exact on TPs:', round(d['tp_amount']['exact_match_rate'], 3)); \
	    assert (d['tp'], d['fn'], d['fp']) == (42, 0, 0), 'development score differs from the documented 42 / 0 / 0'"

setup: $(STAMP)

$(STAMP): $(CIVIL)/requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r $(CIVIL)/requirements.txt
	touch $@

civil-inputs: $(INVOICE_SNAPSHOT)/.git

$(INVOICE_SNAPSHOT)/.git:
	git clone --quiet $(CIVIL_INPUTS_REPO) $(INVOICE_SNAPSHOT)
	git -C $(INVOICE_SNAPSHOT) checkout --quiet $(CIVIL_INPUTS_COMMIT)

civil: $(STAMP) civil-inputs
	cd $(CIVIL) && PYTHON=$(PY) ./reproduce.sh

civil-verify: $(STAMP) civil-inputs
	cd $(CIVIL) && PYTHON=$(PY) bash tools/check_g5.sh

all: hospital hospital-test hospital-eval civil civil-verify
