# Local DeID

A Mac menu-bar app that de-identifies a bilingual (English/Spanish) pediatric
speech-language pathologist's documents (evaluations, assessments, IFSPs,
daily notes) on her own Mac, fully offline.

1. Copy a note from the EMR and press **⌃⌥D**. The app replaces names, places,
   schools, dates, phone numbers, and IDs with tags such as `[CHILD_1]`.
2. Paste the tagged text into ChatGPT or Claude to draft.
3. Copy the draft and press **⌃⌥R**. The app puts the real values back, on
   this Mac only.

## Status

v1 is built and tested on the build Mac (see `reports/phase2.md` and
`reports/eval/phase3-gate4.md`). The shipping engine is the 8-bit OpenAI
Privacy Filter (MLX) plus English and Spanish rules, a caseload roster, and
name and place lists. The app is not a guarantee: the user reviews every note
in the Check window before copying. Open items: the speed and memory check on
the 8 GB Mac, and the 30-document acceptance run (SPEC section 3).

## Install

On an Apple Silicon Mac, paste this into Terminal (the same line updates):

```
curl -fsSL https://raw.githubusercontent.com/mrinoybanerjee/local-deid/main/install.sh | bash
```

See [docs/INSTALL.md](docs/INSTALL.md) for what it does, daily use, and where
the data is.

## Develop

Requirements: Apple Silicon, macOS 13 or later, [uv](https://docs.astral.sh/uv/).

```
uv sync --extra mlx --extra app
.venv/bin/python tools/fetch_models.py --only pf-mlx-8bit o200k-bpe
cd tests && PYTHONPATH=../src ../.venv/bin/python -m pytest -q
```

Tests that need model files skip when the files are missing. There is no CI:
the app needs macOS on Apple Silicon (MLX, AppKit, Keychain).

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

## Privacy

No real client data belongs in this repository, its tests, logs, or issues.
All evaluation data is synthetic. Third-party licenses are in `LICENSES/`.

Build history before 2026-10-12 (and the design reviews and research notes) is
in a private archive repository; commit hashes cited in `docs/DECISIONS.md` and
`reports/` refer to it.
