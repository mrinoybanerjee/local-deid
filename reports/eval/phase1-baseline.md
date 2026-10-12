# Phase 1 baseline: SPEC section 3 gates on the gate set

Date: 2026-10-10. Engine commit `3c4c69d`. Privacy Filter MLX 8-bit, window
8192/1024, on the Mac mini. Raw numbers: `reports/eval/gate.json`. The gate set
was run once; it holds metrics only and was never used for tuning.

## Result: the gates are not met

| Gate (SPEC 3) | Target | Gate set (cold / roster) | Met? |
|---|---|---|---|
| Daily notes fully clean | ≥ 97% | EN 62%, ES 12%, mixed 50% (8 docs each) | **No** |
| All documents fully clean | set after baseline | 21.2% (14/66), 95% lower bound 13% | baseline |
| Clinical content kept | 0 hides | 140 / 152 hides of KEEP-labelled tokens | **No** |
| Review load (median rows) | ≤ 5 note, ≤ 15 eval/IFSP | 3–4 notes, 3–8 eval/IFSP | Yes |
| Blocked documents | (not a gate) | 6 / 66 | — |
| Speed, memory | see `reports/harness/day1.md` | met on the mini proxy | proxy only |

Recall per identifier type (cold mode; roster mode is within 1–2 points):

| Type | Caught | Rate | 95% lower bound |
|---|---|---|---|
| CHILD | 342/344 | 99.4% | 97.9% |
| PARENT | 201/201 | 100% | 98.1% |
| PROVIDER | 343/343 | 100% | 98.9% |
| TEACHER | 135/135 | 100% | 97.2% |
| DATE | 487/488 | 99.8% | 98.8% |
| PHONE / EMAIL / URL | 280/280 | 100% | ≥ 93% |
| ID / MEDICAID_ID | 205/206 | 99.5% | ≥ 94% |
| PLACE | 306/309 | 99.0% | 97.2% |
| SCHOOL | 64/64 | 100% | 94.3% |
| HOSPITAL | 61/68 | 89.7% | 80.2% |
| DAYCARE | 36/45 | 80.0% | 66.2% |
| ORG (parents' workplaces) | 54/70 | 77.1% | 66.0% |
| AGENCY | 80/124 | 64.5% | 55.8% |
| EMPLOYER (clinician's company) | 54/87 | 62.1% | 51.6% |

## What this shows

1. **People, dates, contact details, and IDs are close to the target.** The
   detectors for these (fields, rules, roster, gazetteer, Privacy Filter)
   generalize to a different generator's writing.
2. **Named organizations are the gap.** Agencies, the clinician's employer,
   workplaces, and daycares fall to 62–80%. They have no fixed format, and
   Privacy Filter has no organization label. Because long documents mention
   many organizations, these misses drive the low fully-clean rate.
3. **Clinical hides are far above zero, and the dev set did not measure
   them.** The Claude dev set marked 87 KEEP spans in 96 documents; the
   GPT-6.1 Sol gate set marked 2,407 in 66. Tuning never saw most clinical
   tokens.
4. **The dev-to-gate gap is large** (dev_test 73% fully clean, gate 21%).
   Template splits inside one generator underestimate the shift to a new
   generator. That is why SPEC 7 uses an independent generator for the gate.

## Next steps (SPEC 4.8 and 7: tune on dev only; a failed test needs a fresh sealed set)

1. Generate a second Claude dev set with exhaustive KEEP marking and many
   organization forms (agencies, employers, workplaces, daycares,
   hospitals), using one prompt version shared with the next gate set.
2. Tune clinical protection and organization detection on dev data only.
3. Decide on the SPEC 7 step 5 fine-tune of Privacy Filter on the synthetic
   train split, if rules cannot close the organization gap.
4. Generate a **fresh sealed gate set** (GPT-6.1 Sol) and measure once. This
   gate set has been seen, even in aggregate, and is retired as a sealed test.
