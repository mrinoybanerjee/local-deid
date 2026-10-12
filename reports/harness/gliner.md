# GLiNER in the engine worker: memory and speed

Date: 2026-10-11. Host: Mac mini M4, 16 GB, macOS 26.6.2 (the build host).
**This is a proxy result**, like `day1.md`: the SPEC 3 runs on the 8 GB Air
are still pending. Raw data: `harness/out/speed_{gliner,gliner_pressure,
pf_only_now,pf_pressure_now}.json` (not committed).

## Result

Privacy Filter (MLX 8-bit, window 8,192/1,024, cache cleared) and GLiNER
(`gliner-x-base-q8`, ONNX Runtime CPU) in one worker, GLiNER run after
Privacy Filter on each document.

| Measure (SPEC 3) | Target | Privacy Filter only | With GLiNER |
|---|---|---|---|
| Worker peak, idle | ≤ about 2.1 GiB | 1.74 GiB | 1.82 GiB |
| Worker peak, under pressure | ≤ about 2.1 GiB | 1.72 GiB | 1.80 GiB |
| 500-word note, warm (pressure) | < 5 s | 0.40 s | 1.33 s |
| 5,000-word evaluation, warm (pressure) | < 30 s | 3.9 s | 11.6 s |
| Cold start, evaluation (pressure) | — | 5.0 s | 13.0 s |

GLiNER alone takes 0.7–0.9 s on a 500-word note and 5–7.7 s on a 5,000-word
evaluation. "Under pressure" is the `day1.md` setting: 9 GiB of RAM ballast
plus 4 busy CPU processes.

## How the memory was brought down

The first build peaked at **2.27 GiB** (over the line). Two changes:

1. **Memory-mapped weights.** The Hugging Face file embeds the 0.3 GB of
   int8 weights in the graph, and ONNX Runtime copied them into the process
   (0.62 GiB footprint). `tools/derive_gliner.py` saves the same graph with
   its weights in one external data file; ONNX Runtime maps that file. With
   weight prepacking and the CPU arena off, the session costs about
   0.01 GiB of footprint. No weight changes; the output is byte-for-byte
   reproducible and pinned in `models/MANIFEST.json`.
2. **SentencePiece tokenizer.** The 250k-entry `tokenizer.json` cost
   0.24 GiB in the HF `tokenizers` library; the model's own `spiece.model`
   in SentencePiece costs 0.07 GiB and gives the same ids
   (`tests/test_gliner_onnx.py::test_sentencepiece_matches_hf_tokenizer`).

Parity after both changes: 9,101 of 9,101 comparable dev2 chunks give the
same spans as the `gliner` 0.2.29 package, max score difference 1.2e-7
(`tools/gliner_parity.py`).

## Caveat

Mapped weight pages are file-backed and clean, so `phys_footprint` (the
SPEC 3 measure, `day1.md`) does not count them. They still use RAM while
resident. Under memory pressure macOS can drop them and read them back from
the SSD, which costs time, not correctness. The under-pressure run above
includes that effect on the mini; the 8 GB Air run will show it for real.
