# Phase 1 exit: gate3 results (look 1) and the GLiNER decision

Date: 2026-10-11. Engine commit `5f50a7f` (run record in DECISIONS 25).
Privacy Filter MLX 8-bit, window 8,192/1,024, on the Mac mini. Command:
`tools/run_eval.py data/gate3/gate3.jsonl --ab` (fresh inference, both
configurations in one invocation). Raw numbers: `reports/eval/gate3_ab.json`.
Only per-stratum summaries were read; no gate3 document or miss was opened.

- **A** = engine without GLiNER. **B** = engine with GLiNER.
- **Cold** = no caseload. **Roster** = caseload and the employer name set, as
  the clinician will run it. The DECISIONS 25 targets apply to roster mode.
- gate3 = 185 synthetic documents from GPT-6.1 Sol (prompt v3, seed 41),
  never used for tuning: 35 daily notes, 10 evaluations, and 10 IFSPs per
  language (EN, ES, mixed), plus 10 general texts in EN and in ES.
- "Fully clean" = no gold identifier left visible. Lower bounds are 95%
  Wilson bounds.

## Result: the targets are not met. GLiNER does not ship. The fine-tune is triggered.

### Fully clean documents, roster mode

| Stratum | Target | A (shipping config) | B (with GLiNER) | Met by A? |
|---|---|---|---|---|
| Daily notes EN | ≥ 34/35 | 20/35 (lb 40.9%) | 33/35 (lb 81.4%) | **No** |
| Daily notes ES | ≥ 34/35 | 18/35 (lb 35.6%) | 26/35 (lb 57.9%) | **No** |
| Daily notes mixed | ≥ 34/35 | 24/35 (lb 52.0%) | 29/35 (lb 67.3%) | **No** |
| Evaluations, pooled | ≥ 21/30 | 11/30 (lb 21.9%) | 18/30 (lb 42.3%) | **No** |
| Evaluations per language | ≥ 5/10 each | EN 4, ES 1, mixed 6 | EN 6, ES 4, mixed 8 | **No** |
| IFSPs, pooled | ≥ 15/30 | 12/30 (lb 24.6%) | 21/30 (lb 52.1%) | **No** |
| IFSPs per language | ≥ 3/10 each | EN 5, ES 4, mixed 3 | EN 8, ES 6, mixed 7 | Yes |
| General texts | reported | EN 8/10, ES 6/10 | EN 9/10, ES 8/10 | — |
| All documents | reported | 99/185 | 144/185 | — |

Cold mode: A 29/185, B 50/185 fully clean.

### Other SPEC 3 measures

| Measure | Target | A roster | B roster |
|---|---|---|---|
| Clinical hides (KEEP-labelled tokens hidden) | 0 | 45 | 49 |
| Blocked documents (integrity check) | — | 11 | 10 |
| Median check items: daily notes | ≤ 5 | 3–4 | 3–4 |
| Median check items: evaluations, IFSPs | ≤ 15 | 6.5–10 | 6.5–9 |

The A clinical hides come from rules dates (14), gazetteer persons (17),
gazetteer and rules organizations (13), and propagation; a hide can have
several sources. The B hides add 22 with a `model:` ORG source and 4 with a
`model:` DAYCARE source.

### Recall per identifier type, roster mode (pooled)

People, dates, contact details, and IDs meet a 95% lower bound in A:
CHILD 742/742, PARENT 565/565, SIBLING 308/308, PEER 352/352, PET 254/254,
PROVIDER 885/886, TEACHER 362/362, EMPLOYER 289/289, FAMILY 355/355, DATE
1,186/1,186, TIME 756/758, PHONE 353/353, EMAIL 266/266, URL 109/109, ID
324/324, MEDICAID_ID 178/178 (lower bounds 96.6–99.7%). PERSON (other
people) is 80/83 (lb 89.9%); its sample is small.

| Type | A roster | B roster |
|---|---|---|
| AGENCY | 392/452, lb 83.3% | 436/452, lb 94.3% |
| ORG (workplaces) | 332/365, lb 87.6% | 360/365, lb 96.8% |
| DAYCARE | 160/170, lb 89.5% | 167/170, lb 94.9% |
| HOSPITAL | 194/207, lb 89.6% | 204/207, lb 95.8% |
| SCHOOL | 149/152, lb 94.4% | 151/152, lb 96.4% |
| PLACE | 741/759, lb 96.3% | 743/759, lb 96.6% |

Cold mode only: EMPLOYER 195/289 in A (250/289 in B) and PET 154/254 in
both. The caseload closes both gaps.

## GLiNER decision (pre-registered rule, DECISIONS 25)

1. **Gap: met.** A's lower bound is under 95% for AGENCY, ORG, DAYCARE,
   HOSPITAL, SCHOOL, and EMPLOYER (cold).
2. **B closes it: met.** B catches at least as many on every organization
   type, AGENCY's lower bound rises 11 points, and roster fully clean rises
   from 99 to 144.
3. **Clinical: failed.** B has 49 clinical hides against A's 45, and 26 of
   B's hides have a `model:` organization source.
4. **Runtime: pending** (the 8 GB Air run). It cannot change the outcome.

GLiNER does not ship in v1 (DECISIONS 26). The app runs the Privacy Filter
and rules only. gate3 is now spent.

## Phase 1 exit (DECISIONS 25)

The fine-tune trigger fired: daily notes fail in every language. People,
date, and contact types do not trigger it on their own; the daily-note
misses are organizations. The Privacy Filter has no organization class, so
the phase 3 fine-tune adds one (DECISIONS 26). Phase 3 is judged on the
`dev_test` splits and then once on a new sealed gate4 (GPT-6.1 Sol, seed
53), with the same targets.

## Trend across gate sets (A, roster, all documents fully clean)

| Gate set | Engine | Documents | A roster | B roster |
|---|---|---|---|---|
| gate2 (prompt v2) | `77e04c8` | 66 | 36% | 62% |
| gate3 (prompt v3) | `5f50a7f` | 185 | 54% | 78% |

The two gate sets differ in prompt and size, so the trend is indicative only.
