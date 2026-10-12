# Phase 1, day 1: memory and speed harness

Date: 2026-10-10. Host: Mac mini M4, 16 GB, macOS 26.6.2 (the build host).
**This is a proxy result.** SPEC section 3 measures speed and memory on an
8 GB MacBook Air with Chrome open. That run is still pending.

## Result

**Runtime: Privacy Filter as MLX 8-bit, window 8,192 tokens, overlap 1,024.**

| Gate (SPEC 3) | Target | Measured (mini, under pressure) | Margin |
|---|---|---|---|
| 500-word daily note, warm | < 5 s | 0.40 s | 12× |
| 5,000-word evaluation, warm | < 30 s | 3.9 s | 7× |
| Engine worker peak memory | ≤ about 2.1 GiB (2.5 GB app budget minus UI) | 1.74 GiB | 0.36 GiB |
| Cold start, note (spawn, hash check, load, first note) | — | 1.6 s | — |
| Cold start, evaluation (fresh worker per document) | — | 4.9 s | — |

Every load re-hashes the model files against `models/MANIFEST.json`
(0.55 s with a warm page cache). The cold numbers include that check.

"Under pressure" means a 9 GiB ballast of random bytes plus 4 busy CPU
processes. During the run, macOS pushed 4.3 GB to swap. This approximates an
8 GB Air with Chrome and ChatGPT open.

The whole-app number also needs the UI process (pywebview and WebKit). It is
measured in phase 2. With a UI of about 0.2–0.3 GB, the app comes to about
2.1–2.2 GB, under the 2.5 GB gate.

## How it was measured

- `harness/memspeed.py` runs every configuration in a fresh process.
- Memory is the kernel's lifetime peak of `phys_footprint` for the worker
  (`task_info(TASK_VM_INFO).ledger_phys_footprint_peak`). That number counts
  Metal buffers. It matched `/usr/bin/time -l` "peak memory footprint" within
  0.01% (519.57 vs 519.62 MB).
- Cold latency runs each document in its own fresh worker, timed from
  process spawn to that document done.
- Warm latency is the median of 5 runs of the whole Privacy Filter stage:
  tokenize, windowed inference, Viterbi decoding, and spans.
- Inputs are synthetic (`harness/gen_inputs.py`, fake names): English and
  Spanish 500-word notes (about 810 tokens), and mixed and Spanish 5,000-word
  evaluations (about 7,500–7,800 tokens).
- Quality: each runtime against OpenAI's unquantized fp32 ONNX export, which
  is a dev-only reference, plus coverage of the planted identifiers.

## Findings

1. **The ONNX q4 export uses dense attention.** Memory grows with the square
   of the window: 2.0–2.3 GiB at 2,048 tokens, 3.3–3.6 GiB at 3,072, and
   5.2–5.4 GiB at 4,096. The graph builds a T×T band mask
   (`Range`/`Sub`/`Abs`/`LessOrEqual`, then `MatMul` and `Softmax`). Its best
   setting (window 2,048 with overlap 512, 2 threads) peaked at 2.01 GiB and
   took 11.7 s per evaluation idle and 14.6 s under pressure.
2. **MLX 8-bit started at 2.9–5.0 GiB.** Four changes, each verified,
   brought it to 1.7 GiB:
   - Attention computed in blocks of 512 queries. The output is identical
     (max difference 0.0).
   - Mixture-of-experts layer computed in blocks of 512 tokens, plus
     evaluation after each layer.
   - `mx.set_cache_limit(0)` and `mx.clear_cache()` after each window.
   - Tokenizer moved from Hugging Face `tokenizers` (200 MB resident) to
     tiktoken with a pinned local file (79 MB). Token IDs and offsets are
     identical, and a test checks that.
3. **Quality favors MLX.** Against the fp32 reference, MLX 8-bit span recall
   is 0.985–1.000 and ONNX q4 is 0.833–0.919. On planted person names, MLX
   finds 54.7%, the reference 53.9%, and ONNX q4 51.2%.
4. **Privacy Filter alone catches only about half the names and about 20% of
   school names** on these documents, and all dates, phones, and IDs. This
   supports the spec: fields, rules, roster, and gazetteer must carry most of
   the recall.
5. **Windowed output is not bit-identical to a single pass.** 99.8–100% of
   token labels agree. The stitching itself is exact (tested with a local
   model). The difference is bf16 rounding on different tensor shapes, which
   can flip the top-4 expert routing for a few tokens. Blocking the
   mixture-of-experts layer has the same effect (99.8% agreement). The fp32
   comparison above already includes this noise.
6. **ONNX Runtime ships a network-capable `AzureExecutionProvider`.** The
   engine allows only `CPUExecutionProvider` and checks it after loading. A
   test covers this.

## Not yet verified

- Every number on the 8 GB Air with Chrome open (SPEC 3 says "Harness on an
  8 GB Air"). The run needs the Air.
- A cold start that reads weights from the SSD. `sudo purge` needs a
  password on the mini, so the page cache was warm. On an Air SSD (about
  2–3 GB/s), reading 1.4 GB of weights should add roughly 0.5–1 s.
- Whole-app memory with the UI process (phase 2).

## Files

- Tables: `reports/harness/day1-tables.md`. Raw JSON:
  `reports/harness/day1-raw/`.
- Rerun:
  ```
  .venv/bin/python harness/memspeed.py speed
  .venv/bin/python harness/memspeed.py speed --pressure
  .venv/bin/python harness/memspeed.py agree
  .venv/bin/python harness/memspeed.py report
  ```
