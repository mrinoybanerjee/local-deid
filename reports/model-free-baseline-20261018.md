# Model-free development baseline — October 18, 2026

This is a reproducible rules-and-matching baseline, not a shipping-model evaluation.
No model files, credentials, paid services or real clinical documents were used.
The development set has informed engineering; it is not an independent release gate.

## Method

Publication checkout: `d7d3296`. macOS 26.6.2 (25G83), arm64; Python 3.12.12.
The existing Python environment from the adjacent `local-deid` checkout supplied dependencies.
The evaluator imports `local-deid-publish/src/localdeid`, not the adjacent environment’s source.
The raw report’s engine/scorer digest is
`5d40711b5ded77f7faed9edda92af95c1fe062b54940929db305c09ae59ef163`.
Recomputing `localdeid.evaldata.code_digest()` from this checkout matched it.
The earlier full-patch checkout `43bdb30` has identical engine/scorer and evaluator source;
the publication commits split that patch into separate changes. The report also records the data digest.

From a configured checkout:

```sh
.venv/bin/python tools/run_eval.py data/dev/dev.jsonl --split dev_test --no-model
```

The actual interpreter used here was `../local-deid/.venv/bin/python`.
The command writes `reports/eval/dev_dev_test.json`; preserve existing reports before running.
This run's output was retained separately as
[`eval/devtest_rules_only_20261018.json`](eval/devtest_rules_only_20261018.json).
The raw report has `model: false`; its backend name is a CLI default, not evidence of model execution.

## Results

| Measure | Cold (no roster) | With synthetic roster |
|---|---:|---:|
| Documents | 26 | 26 |
| Fully clean (no gold identifier visible) | 12 (46.2%) | 13 (50.0%) |
| Clinical tokens hidden | 0 | 0 |
| Blocked documents | 1 | 0 |
| Median check items | 4 | 4 |
| Extra hides | 99 | 105 |
| Child mentions caught | 303/314 | 314/314 |

The roster helps known names in this set, but neither path removes every identifier.
Zero clinical hides on these documents does not establish that clinical text is always preserved.
These counts do not measure review time, clinical acceptance, model recall or adoption.
See the raw report for per-type recall and confidence bounds. The historical shipping-model
[gate4 report](eval/phase3-gate4.md) remains separate and its targets remain unmet.
