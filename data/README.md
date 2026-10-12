# Synthetic evaluation data

All documents are fictional. No real client data is stored here or anywhere in the repository.

| Set | Generator | Docs | Use |
|---|---|---|---|
| `dev/dev.jsonl` | Claude Sonnet 5 (`claude -p`, no tools, no MCP) | 96 | Engine tuning. `meta.split` is `dev_train` or `dev_test`, split by writing style (template) so near-copies never cross splits. |
| `gate/gate.jsonl` | GPT-6.1 Sol, high reasoning (`codex exec`, read-only, no user config) | 66 | First gate set. Measured once (`reports/eval/phase1-baseline.md`), then **retired**: its aggregate numbers were seen. Strata: EN/ES/mixed × daily note / evaluation / IFSP, plus a general-text stratum (EN/ES). |
| `dev2/dev2.jsonl` | Claude Sonnet 5, prompt v2 | 119 | Engine tuning. Prompt v2 marks every clinical token as KEEP and adds organization-heavy challenges (agencies, employers, workplaces, daycares, hospitals). |
| `gate2/gate2.jsonl` | GPT-6.1 Sol, high reasoning, prompt v2 | 66 | Sealed gate set. Metrics only, measured once at the end of phase 1. Same strata as `gate`. |

- Each record: `doc_id`, `text`, `spans` (`start`, `end`, `type`; code-point offsets), `meta`
  (language, doc_type, style, challenges, generator, attempt, split).
- Labels come from inline markup (`[[TYPE|text]]`, `[[KEEP|text]]`) parsed by
  `src/localdeid/evaldata.py`. The raw generator outputs are in `raw/`.
- Generators make label mistakes (codes labelled ID, titles inside spans). `evaldata.sanitize`
  and the scoring tolerances in `evaluate.py` apply the SPEC rules consistently; reports record
  how many labels were changed.
- Regenerate or extend: `.venv/bin/python tools/gen_data.py dev|gate` (resumes from the JSONL).
- **Mrinoy spot-checks the gate set** (SPEC 7): read a sample in `gate/raw/`.
