# Phase 3: gate4 results (one look) and the ship decision

Date: 2026-10-11. Engine commit `eed8348`. Privacy Filter MLX 8-bit, window 8,192/1,024,
on the Mac mini. Commands: `tools/run_eval.py data/gate4/gate4.jsonl --backend mlx_8bit`
and `... --backend mlx_8bit_ft1`. Raw numbers: `reports/eval/gate4.json` and
`reports/eval/gate4_mlx_8bit_ft1.json`. gate4's SHA-256 matched the sealed value
before the run. Only per-stratum summaries were read; no gate4 document or miss was
opened.

- **Base** = the shipping engine with the 8-bit Privacy Filter.
- **Tuned** = the same engine with fine-tune iteration 2 (DECISIONS 26: QLoRA rank 32
  on attention and the expert gate, an added `private_organization` class).
- gate4 = 185 synthetic documents from GPT-6.1 Sol (prompt v3, seed 53), never used
  for tuning: 35 daily notes, 10 evaluations, and 10 IFSPs per language (EN, ES,
  mixed), plus 10 general texts in EN and in ES. The label repair of DECISIONS 26
  applies to both configurations.
- "Fully clean" = no gold identifier left visible.

## Result: the tuned model does not ship. Base ships. Neither meets the targets.

The pre-registered ship rule (DECISIONS 26 and its amendment, written before the look):

| Condition | Base | Tuned | Met? |
|---|---|---|---|
| 1. Fully clean, roster, higher than base | 94/185 | 141/185 | Yes |
| 2. No identifier type catches fewer than base (roster) | — | CHILD 725 vs 726, PHONE 335 vs 336, PLACE 735 vs 744 | **No** |
| 3. Clinical hides no more than base, both modes | 21 / 21 | 23 / 23 | **No** |
| 4. Air memory and speed | not run | not run | pending |

Conditions 2 and 3 fail, so base ships and the tuned model stays an evaluation
artifact (`models/pf-mlx-8bit-ft1`). There is no iteration 3 in v1, and no change may
follow from this look. gate4 is spent; a later version needs a new sealed gate (gate5).

## Fully clean documents, roster mode (DECISIONS 25 targets)

| Stratum | Target | Base | Tuned |
|---|---|---|---|
| Daily notes EN | ≥ 34/35 | 19/35 | 34/35 |
| Daily notes ES | ≥ 34/35 | 13/35 | 26/35 |
| Daily notes mixed | ≥ 34/35 | 25/35 | 28/35 |
| Evaluations, pooled | ≥ 21/30 | 14/30 (lb 30.2%) | 18/30 (lb 42.3%) |
| Evaluations per language | ≥ 5/10 each | EN 5, ES 4, mixed 5 | EN 6, ES 2, mixed 10 |
| IFSPs, pooled | ≥ 15/30 | 9/30 (lb 16.7%) | 17/30 (lb 39.2%) |
| IFSPs per language | ≥ 3/10 each | EN 3, ES 3, mixed 3 | EN 8, ES 3, mixed 6 |
| General texts | reported | EN 8/10, ES 6/10 | EN 10/10, ES 8/10 |
| All documents | reported | 94/185 | 141/185 |

Cold mode (no caseload): base 33/185, tuned 86/185.

The shipping configuration (base) misses every target. The tuned model would meet the
English daily-note target and both pooled long-document targets, but it does not
ship under the rule.

## Other measures

| Measure | Base | Tuned |
|---|---|---|
| Clinical hides (KEEP-labelled tokens hidden), cold / roster | 21 / 21 | 23 / 23 |
| Clinical hides with a `model:` organization source | 0 | 16 |
| Blocked documents (integrity or leak check) | 6 | 5 |
| Median check items, daily notes | 3–4 | 4–5 |
| Median check items, evaluations and IFSPs | 5.5–8.5 | 7–9 |

## Recall per identifier type, roster mode

| Type | Base | Tuned |
|---|---|---|
| AGENCY | 384/479 (lb 76.4%) | 463/479 (lb 94.6%) |
| DAYCARE | 131/150 (lb 81.1%) | 148/150 (lb 95.3%) |
| HOSPITAL | 154/165 (lb 88.5%) | 162/165 (lb 94.8%) |
| ORG | 492/532 (lb 89.9%) | 520/532 (lb 96.1%) |
| SCHOOL | 170/171 | 171/171 |
| PROVIDER | 823/828 | 828/828 |
| PLACE | 744/760 (lb 96.6%) | 735/760 (lb 95.2%) |
| CHILD | 726/730 | 725/730 |
| PHONE | 336/336 | 335/336 |

Every other type is identical in both: DATE 1,144/1,144, PARENT 541/541, SIBLING
309/309, PEER 341/341, PET 259/259, TEACHER 342/342, FAMILY 327/327, PERSON 81/81,
EMPLOYER 275/275, EMAIL 267/267, ID 311/311, MEDICAID_ID 173/173, URL 116/116, TIME
747/748.

## What this means

- Base leaves at least one identifier visible in 91 of 185 synthetic documents, most
  of them organization names. The tuned model leaves 44.
- The tuned model's regressions are small but real: 9 more missed places, 1 child
  mention, 1 phone number, and 2 more clinical words hidden (16 of its clinical hides
  have a model organization source).
- The rule required no regression at all on any type and on clinical hides, so it
  chose base. That choice was fixed before the look.
- The clinician's 30-document acceptance run (SPEC 3: at most 1 identifier missed) stays the
  blocking exit for v1, on the shipping configuration.
