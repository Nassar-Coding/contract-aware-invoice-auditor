# Verification records

Everything the gate checks compare against, and the evidence behind each figure in the READMEs.

| Path | Content |
|---|---|
| `g2/` … `g6/` | committed outputs of each gate; `reproduce.sh` regenerates them and `git diff` must show no change |
| `ocr/`, `blind/`, `reading_comparison.json` | OCR of every scanned page, blind transcriptions of the contract tables, and their cell-by-cell comparison with the transcribed terms |
| `second_pass_log.yaml`, `second_pass_visual_log.txt`, `verbatim_reread/` | the second verification of every table and instrument against the scan images |
| `param_rule_packets/`, `param_rule_readings/`, `param_rule_log.yaml`, `param_rule_dispositions.yaml` | second readings of every parameter and rule, bound to each item's content hash |
| `section_readings.jsonl`, `section_dispositions.yaml` | second readings of the contract sections not otherwise re-read |
| `g2/blind/`, `g2/semantic/` | blind annotation and semantic review of a seeded record sample |
| `g3/cases/`, `g4/histories/`, `g5/samples/` | packets given to independent readers (inputs only) and the expected results they returned |
| `commit_order.json` | the order in which those packets, results and the engine were first committed (`tools/commit_order.py`) |

The independent readers' packets and returned readings are kept as given, with two normalizations:

- local file paths are recorded relative to the input snapshot;
- process terms are replaced by the terms this repository uses: "policy decision" for a decision the contract text
  does not settle, and "dataset README (output format)" for the dataset's description of the output file.

Both changes leave the meaning of every packet and reading unchanged. The packet builders (`tools/g3_cases.py`,
`tools/g5_samples.py`, `tools/semantic_review_g2.py packet`) rebuild the committed packets byte for byte. Quotes from
the dataset's own documents and from the contract scans are left verbatim. The records as originally produced are
kept in the original development repository (see Provenance in the top-level README).
