# Contract records

Each hospital's contract is held as data the engine replays. No code is generated per contract, and no expression is
evaluated.

| File | Content |
|---|---|
| `hospital_N.json` | the services, rates, units, versions and adjustments of one contract, each with its source reference |
| `rules_hospital_N.json` | daily caps and exclusion windows, each citing the clause it comes from |
| `hospital_N.raw.json` | the extraction output before review, kept so the review can be compared with what it accepted |
| `HN.bundle.json` | binds a hospital's contract record, rule file, service mapping and abbreviation lexicon, with their hashes and the hashes of the source documents they were read from |

The records were extracted from the contract text with LLM assistance, then reviewed against the source text before
being marked `accepted`. The review was made during implementation, not by an independent reviewer. The interpreter
refuses a bundle that is not accepted, or whose contract, mapping and hospital do not agree
(`src/insurance_audit/schema.py`).

Service mappings in `../mappings/` were made the same way: candidates were proposed from the wording with LLM
assistance, then reviewed. A billed description is mapped to a contracted service only on the evidence of its
wording, never its price, and descriptions that cannot be resolved stay unresolved.
