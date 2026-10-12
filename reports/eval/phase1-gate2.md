# Phase 1: gate2 results (look 1) and the GLiNER decision

Date: 2026-10-11. Engine commit `77e04c8` (run record in DECISIONS 23).
Privacy Filter MLX 8-bit, window 8,192/1,024, on the Mac mini. Command:
`tools/run_eval.py data/gate2/gate2.jsonl --ab` (fresh inference, both
configurations in one invocation). Raw numbers: `reports/eval/gate2_ab.json`.
Only per-stratum summaries were read; no per-document errors were opened.

- **A** = engine without GLiNER. **B** = engine with GLiNER.
- **Cold** = no caseload. **Roster** = caseload and the employer name set, as
  the clinician will run it.
- gate2 = 66 synthetic documents from GPT-6.1 Sol (prompt v2), never used for
  tuning. 3,684 identifiers, 3,267 KEEP spans.

## Result: the SPEC 3 gates are not met. GLiNER does not ship.

| Gate (SPEC 3) | Target | A cold | A roster | B roster | Met? |
|---|---|---|---|---|---|
| Daily notes fully clean | ≥ 97% | EN 62%, ES 12%, mixed 25% | EN 75%, ES 50%, mixed 38% | EN 100%, ES 75%, mixed 75% | **No** |
| All documents fully clean | set after baseline | 18% (12/66) | 36% (24/66) | 62% (41/66) | — |
| Clinical content kept | 0 hides | 11 | 11 | 16 | **No** |
| Review load (median rows) | ≤ 5 note, ≤ 15 eval/IFSP | 2.5 / 5–8 | 2.5–3.5 / 4–7 | 2.5–3 / 4–7 | Yes |
| Blocked documents | (not a gate) | 2 | 2 | 1 | — |
| Worker memory, speed | ≤ 2.1 GiB; < 5 s / < 30 s | 1.74 GiB; 0.4 s / 3.9 s | same | 1.82 GiB; 1.3 s / 11.6 s | proxy only |

Each daily-note stratum has 8 documents, so the 95% lower bounds are wide
(B roster EN 100% has a lower bound of 68%).

### Recall per identifier type (95% lower bound)

People, dates, contact details, IDs, and schools meet the bound in both
configurations: CHILD 98.7–99.3%, PARENT 100%, PROVIDER 99.4%, TEACHER 100%,
DATE 99.8%, PHONE/EMAIL/URL 100%, ID/MEDICAID_ID 100%, SCHOOL 56/56 (a
lower bound of 93.6% is the most 56 documents allow), PLACE 97.1–97.4%.

| Type | A (cold) | B (cold) | A roster | B roster |
|---|---|---|---|---|
| AGENCY | 183/218, lb 78.5% | 205/218, lb 90.1% | same as cold | same as cold |
| DAYCARE | 43/54, lb 67.1% | 52/54, lb 87.5% | | |
| ORG (workplaces) | 234/256, lb 87.3% | 254/256, lb 97.2% | 233/256, lb 86.9% | 253/256, lb 96.6% |
| HOSPITAL | 70/76, lb 83.8% | 74/76, lb 90.9% | | |
| EMPLOYER | 93/134, lb 61.2% | 113/134, lb 77.2% | 133/134, lb 95.9% | 133/134, lb 95.9% |

## GLiNER decision (pre-registered rule, DECISIONS 23)

1. **Gap — met.** A's lower bound is under 95% for AGENCY, DAYCARE, ORG,
   HOSPITAL, and SCHOOL (cold).
2. **B closes it — met.** B catches at least as many on every gap type,
   AGENCY's lower bound rises 11.6 points, and roster fully clean rises from
   24 to 41 documents.
3. **Clinical — failed.** B hides 16 KEEP-labelled tokens; A hides 11.
4. **Runtime — met** (1.80–1.82 GiB, speed gates met on the mini proxy).

GLiNER does not ship. gate2 is now spent for GLiNER selection. Any later
change to GLiNER must be validated on dev data and measured on a fresh
sealed gate set.

## Compared with gate set 1 (`phase1-baseline.md`, engine `3c4c69d`)

| | Gate set 1 (A, cold) | gate2 (A, cold) |
|---|---|---|
| Fully clean | 21% (14/66) | 18% (12/66) |
| Clinical hides | 140 | 11 |
| AGENCY / EMPLOYER / DAYCARE recall | 64.5% / 62.1% / 80.0% | 83.9% / 69.4% / 79.6% |

These are different sets, so the comparison is indicative only. The main
gain is clinical protection (140 hides down to 11). Organizations still
drive the low fully-clean rate.

## What this shows

1. **Organizations remain the gap without GLiNER.** Rules close part of it.
   GLiNER closes most of it (B roster: 62% fully clean, every org type
   except EMPLOYER-cold at a lower bound of 87.5% or more). But it adds clinical
   hides, so under the pre-registered rule it cannot ship.
2. **Dev overstates the gate again.** dev2 A cold is 34.5% fully clean,
   gate2 18%; B cold dev2 55.5%, gate2 45.5%. The independent generator
   still writes organizations and clinical text that dev2 lacks.
3. **Clinical hides are near zero but not zero** (11). SPEC 3 asks for zero.
4. **Long documents fail first.** Evaluations and IFSPs are 0–40% fully
   clean in A; daily notes do best.

## Next steps (for Mrinoy to decide; SPEC 7: a failed gate needs a fresh sealed set)

Advisor recommendation (Fable 5.1, 2026-10-11), ranked:

1. **GLiNER plus clinical fixes, validated on a fresh dev3, measured once on
   a fresh sealed gate3.** About 3–4 days. Fix the protect coverage that
   lets GLiNER hide KEEP tokens, and the date and daycare-rule hides that A
   also has; then work on Spanish agency names. 97% on daily notes is not
   assured even then.
2. **Privacy Filter fine-tune (SPEC 7 step 5)**, only if option 1 misses
   gate3. About 1–2 weeks (training path, GPU, re-quantization, parity and
   memory harness again), with a risk of learning the generators' styles.
3. **Ship A as is and rely on the check list.** Fails the SPEC 3 headline
   (36% fully clean before review).

Caveat: the advisor used aggregate per-type and per-source counts from the
gate2 summary file. Any change they suggest must be validated on dev3 and
measured on gate3; gate2 numbers cannot be used as evidence again.

**gate3 size.** With 8 daily notes per language, one miss gives 87.5%. The
advisor suggests 35 daily notes per language (one miss still gives 97.1%;
the lower bound at zero misses is 92%), evaluations and IFSPs at gate2 size,
and the general stratum: about 150 documents. `tools/gen_data.py` fixes 8
daily notes per language today, so this needs a small generator change.

**Also open:** SPEC 3 says to set the evaluation and IFSP fully-clean targets
after the phase 1 baseline. Gate2 is that baseline (A roster: evaluations
20%, IFSPs 0–40%; B roster: 60–80% and 20–40%). The targets should be set
before gate3 so it has a pre-registered bar.

Independent of the choice: run the harness on the 8 GB Air (memory and speed
are mini proxies).
