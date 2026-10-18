# Local DeID

A local Mac tool that helps replace identifiers in English and Spanish pediatric
speech-language pathology documents with reversible tags. Copy a note or open a
document, review the tagged text, then restore names locally when the draft returns.

**Review aid, not a guarantee.** The shipping base model missed the committed
synthetic evaluation targets. Review every output before sharing it; the app's
checks do not prove that all identifiers have been removed.

[How it works](#workflow) · [Install](#install) · [Evaluation and status](#evaluation-and-status) · [Privacy and limits](#privacy)

## Workflow

1. Copy a note from the EMR and press **⌃⌥D** (Control-Option-D), or open a Word,
   PDF, RTF, or text document in the window. Detected names, places, schools,
   dates, phone numbers, and IDs become tags such as `[CHILD_1]`.
2. Review the tagged text. The Check window opens when there are check items;
   otherwise the hotkey can copy the result directly. Add missed identifiers in
   Check before copying. Unsure items start hidden; integrity failures block Copy.
3. If you use an external drafting service, paste only the reviewed text and keep
   the `ref:` line and tags unchanged. Pasting into ChatGPT or Claude sends that
   text to that service; Local DeID does not make the external drafting step offline.
4. Copy the returned draft and press **⌃⌥R** (Control-Option-R). Review the Restore
   view, then choose **Copy restored draft**. Restoration happens on this Mac.

See [daily use](docs/INSTALL.md#daily-use) for Check controls, the caseload, and document handling.

## Install

Requires Apple Silicon, macOS 13 or later, and at least 5 GB of free disk space.
The installer uses the network to fetch code, packages, and model files. The app
is designed to run offline after setup.

On an Apple Silicon Mac, run the existing installer (the same line updates):

```
curl -fsSL https://raw.githubusercontent.com/mrinoybanerjee/local-deid/main/install.sh | bash
```

On first install, this also runs the self-test and speed/memory checks, opens the
window, and uses the clipboard. It builds `~/Applications/Local DeID.app`, keeps
code in `~/Library/LocalDeID`, configures launch at login, and opens the app.
Quit Local DeID before updating; sessions, caseload, and settings are retained.

Read [the install guide](docs/INSTALL.md) for the steps, Keychain access, check
results, updates, and troubleshooting. The global hotkeys do not require
Accessibility permission.

## Evaluation and status

The current app uses the base 8-bit OpenAI Privacy Filter through MLX, together
with English/Spanish rules, the caseload roster, and name/place lists. The tuned
model remains an evaluation artifact and is not the shipping configuration.

The committed [gate4 report](reports/eval/phase3-gate4.md), dated October 11, 2026,
records **185 synthetic documents**, not a clinical deployment study:

| Configuration | Fully clean documents before review |
|---|---:|
| Shipping base, with caseload roster | 94/185 |
| Shipping base, without caseload roster | 33/185 |
| Tuned model, with caseload roster (not shipped) | 141/185 |

“Fully clean” means no gold-labelled identifier was left visible in that synthetic
set. The shipping base left identifiers visible in 91/185 documents and missed
the stated targets. The tuned model improved this count but failed the no-regression
ship rule for identifier types and clinical content; neither configuration met all
targets. See the [raw base results](reports/eval/gate4.json) and the full report for
strata, blocked documents, clinical hides, and the decision boundaries.

Still pending in the committed status:

- Speed and whole-app memory checks on the 8 GB MacBook Air.
- The clinician's 30-document acceptance run, a blocking v1 exit in [SPEC section 3](SPEC.md#3-success-criteria).

The [phase 2 report](reports/phase2.md) records checks on the build Mac and lists
what was not verified on the Air. Those are historical results, not new measurements.

## Engineering focus

The project connects a local hybrid detector to a review-and-restore workflow:
span edits preserve surrounding text, integrity checks guard Copy, and encrypted
session mappings let a reviewed draft regain its identifiers locally. The detector
combines project rules and caseload matching with a third-party Privacy Filter model.

A fictional sample illustrates the boundary: hide names, date of birth and phone
numbers while keeping `/k/`, `8/10`, `mod cues` and CPT `92507`. These are the
[sample self-test expectations](src/localdeid/app/selftest.py), not a guarantee on new notes.

A fresh [model-free development baseline](reports/model-free-baseline-20261018.md)
found 12/26 synthetic documents fully clean without a roster and 13/26 with one.
This measures the rules and matching path, not the shipping model or clinical acceptance.

A [fresh real-model runtime check](reports/model-runtime-20261018.md) verified the
pinned shipping artifacts and four synthetic worker/API/vault round trips on a
16 GB M4 Mac. It also removed unsupported name-based gender inference from copied
context. This is integration evidence, not new model-recall or clinical acceptance
evidence; the gate4 limitations above remain.

## Design and evidence

The engine combines deterministic rules, roster and name/place matches, and the
base Privacy Filter. It edits identifier spans and runs integrity/leak checks before
Copy. The Mac app uses a separate engine worker with a network guard, and stores
restore mappings in an encrypted vault protected by a login-Keychain key.

- [SPEC](SPEC.md): workflow, pipeline, success gates, storage, and document limits.
- [Decision log](docs/DECISIONS.md): implementation decisions and tradeoffs.
- [Synthetic data](data/README.md): generators, labels, splits, and limitations.
- [Phase 2 verification](reports/phase2.md) and [gate4 results](reports/eval/phase3-gate4.md).

## Privacy

- Detection can miss identifiers or hide useful clinical text. Check and restore
  controls do not replace review of the output.
- Document import extracts text, not a redacted file. Headers, footers, footnotes,
  and comments are not read. Scanned/picture-only PDFs are refused. Review the
  extracted text; there is no document export.
- Sessions, caseload, and settings are encrypted locally with AES-GCM. The key is
  in this Mac's login Keychain. Sessions expire after 30 days, except those waiting
  for a draft; see [data storage](docs/INSTALL.md#where-the-data-is).
- The app clears its clipboard after two minutes and on quit. Copies already
  made by another application or clipboard-history tool are outside that control.
- No real client data belongs in this repository, tests, logs, or issues. All
  committed evaluation data is synthetic. Do not use the reported counts as a
  guarantee on new documents.

## Develop

From the repository root: Apple Silicon, macOS 13 or later, Python 3.12
(`pyproject.toml` requires `>=3.12,<3.13`), and [uv](https://docs.astral.sh/uv/).

```
uv sync --extra mlx --extra app
.venv/bin/python tools/fetch_models.py --only pf-mlx-8bit o200k-bpe
cd tests && PYTHONPATH=../src ../.venv/bin/python -m pytest -q
```

For the model-free regression suite, including the encrypted vault and synthetic
session tests:

```sh
uv sync --frozen --extra app
uv run --no-sync pytest -q --ignore=tests/test_finetune.py \
  --ignore=tests/test_app_clipboard.py --ignore=tests/test_app_hotkeys.py
```

CI runs the portable subset on Linux without app/model dependencies. Mac-only
checks skip there, including Symbol-font decoding, PDFKit, textutil, and native
memory checks. Clipboard/hotkey integration tests need an interactive
Mac session; the experimental fine-tuning tests need their separate model/MLX
setup. Tests requiring missing model files skip. Passing this suite does not
establish model recall, clinician acceptance, runtime network isolation, or the
8 GB Air memory/speed gates.

## Layout

| Path | Contents |
|---|---|
| `SPEC.md` | The design (v6), the source of truth |
| `AGENTS.md` | Rules for coding agents working in this repository |
| `docs/` | Install guide, decision log (`DECISIONS.md`), phase 2 plan, review backlog |
| `src/localdeid/` | Engine, Privacy Filter backends, menu-bar app |
| `tests/` | Unit, property, and app tests |
| `tools/` | Model fetch, app build, data generation, evaluation, fine-tuning |
| `harness/` | Speed and memory measurement |
| `data/` | Synthetic evaluation sets (all fictional; see `data/README.md`) |
| `reports/` | Evaluation and harness reports |
| `models/MANIFEST.json` | Pinned model files with SHA-256 hashes (the files are not committed) |

## License

MIT (see [LICENSE](LICENSE)). Third-party model notices are in [LICENSES](LICENSES/).

Build history before 2026-10-12 (and the design reviews and research notes) is
in a private archive repository; commit hashes cited in `docs/DECISIONS.md` and
`reports/` refer to it.
