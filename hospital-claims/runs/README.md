# Generated attempts

`python -m insurance_audit reproduce` writes each attempt, the current pointers and the validated results release into
this folder. The outputs are excluded from Git because the frozen inputs reproduce them exactly, so a fresh clone holds
only this file and fills the rest on the first run.

Nothing here is an input: no prediction, test or report reads a previous attempt. The folder exists because
`src/insurance_audit/results.py` writes a release here before promoting it to `audit_results.csv`.
