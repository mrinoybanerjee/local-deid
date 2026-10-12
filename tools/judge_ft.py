"""Judge a fine-tuning iteration on the dev_test splits (DECISIONS 26 stopping rule).

    PYTHONPATH=src .venv/bin/python tools/judge_ft.py [--sets dev,dev2,dev3,dev4] [--skip-run]

Builds data/devtest/devtest.jsonl from the dev_test splits, runs tools/run_eval.py
with the base model (mlx_8bit) and the tuned model (mlx_8bit_ft1), and applies the
pre-registered pass rule. It prints counts only.

Pass (all, roster mode unless noted):
1. organization recall lower bound >= 95% (tuned);
2. each daily-note language >= 97% fully clean (tuned);
3. no people, date, or contact type catches fewer than base (cold and roster);
4. clinical hides no more than base (cold and roster).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localdeid.evaldata import GOLD_TYPES, code_digest, docs_digest, dump_jsonl, load_jsonl, sanitize  # noqa: E402

PROTECTED_COARSE = ("NAME", "DATE", "CONTACT")  # people, dates, contact details


def build(sets: list[str]) -> Path:
    docs = []
    for name in sets:
        docs += [d for d in load_jsonl(ROOT / "data" / name / f"{name}.jsonl") if d.meta.get("split") == "dev_test"]
    out = ROOT / "data" / "devtest" / "devtest.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    dump_jsonl(docs, out)
    print(f"devtest: {len(docs)} documents from {', '.join(sets)}")
    return out


def run(path: Path, backend: str) -> dict:
    cmd = [sys.executable, str(ROOT / "tools" / "run_eval.py"), str(path), "--backend", backend]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    subprocess.run(cmd, check=True, cwd=ROOT, env=env, stdout=subprocess.DEVNULL)
    name = path.stem + (f"_{backend}" if backend != "mlx_8bit" else "")
    return json.loads((ROOT / "reports" / "eval" / f"{name}.json").read_text(encoding="utf-8"))


def judge(base: dict, tuned: dict) -> bool:
    ok = True

    def check(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")

    tr = tuned["modes"]["roster"]["strata"]
    org = tr["ALL"]["recall"]["ORG"]
    check("organization lower bound >= 95%", org["lb95"] >= 0.95,
          f"{org['caught']}/{org['total']}, lb {org['lb95']:.1%}")
    for lang in ("en", "es", "mixed"):
        st = tr.get(f"{lang}/daily_note")
        if st is None:
            check(f"daily notes {lang} >= 97%", False, "no documents")
            continue
        check(f"daily notes {lang} >= 97% fully clean", st["fully_clean_rate"] >= 0.97,
              f"{st['fully_clean']}/{st['docs']} (base {base['modes']['roster']['strata'][f'{lang}/daily_note']['fully_clean']})")
    for mode in ("cold", "roster"):
        b, t = base["modes"][mode]["strata"]["ALL"], tuned["modes"][mode]["strata"]["ALL"]
        worse = [f"{ty} {t['recall_by_type'][ty]['caught']}<{b['recall_by_type'][ty]['caught']}"
                 for ty in b["recall_by_type"] if GOLD_TYPES.get(ty) in PROTECTED_COARSE
                 and t["recall_by_type"].get(ty, {"caught": -1})["caught"] < b["recall_by_type"][ty]["caught"]]
        check(f"people, date, contact types not below base ({mode})", not worse, ", ".join(worse) or "none lower")
        check(f"clinical hides <= base ({mode})", t["clinical_hides"] <= b["clinical_hides"],
              f"{t['clinical_hides']} vs base {b['clinical_hides']}")
    for mode in ("cold", "roster"):
        b, t = base["modes"][mode]["strata"]["ALL"], tuned["modes"][mode]["strata"]["ALL"]
        print(f"info  fully clean ({mode}): tuned {t['fully_clean']}/{t['docs']}, base {b['fully_clean']}/{b['docs']}; "
              f"blocked tuned {_n(tuned['modes'][mode]['blocked_docs'])}, base {_n(base['modes'][mode]['blocked_docs'])}")
    print("ITERATION PASSES dev_test" if ok else "ITERATION FAILS dev_test")
    return ok


def _check_cached(path: Path, report: dict, backend: str) -> None:
    """A cached report must be a model run on these documents with the current model files."""
    from localdeid.pf.backends import MODEL_DIRS

    want_docs = docs_digest([sanitize(d)[0] for d in load_jsonl(path)])  # as run_eval scores them
    manifest = json.loads((ROOT / "models" / "MANIFEST.json").read_text(encoding="utf-8"))["artifacts"]
    art = manifest.get(MODEL_DIRS[backend], {}).get("files", {})
    want_art = {f: m["sha256"] for f, m in sorted(art.items())}
    if report.get("model") is not True or report.get("backend") != backend:
        sys.exit(f"cached {backend} report did not run that model: run without --skip-run")
    if report.get("code_sha256") != code_digest():
        sys.exit(f"cached {backend} report came from other engine or scorer code: run without --skip-run")
    if report.get("docs_sha256") != want_docs or report.get("artifact_sha256") != want_art:
        sys.exit(f"cached {backend} report is for other documents or model files: run without --skip-run")


def _n(blocked) -> int:
    return len(blocked) if isinstance(blocked, list) else int(blocked)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="dev,dev2,dev3,dev4")
    ap.add_argument("--skip-run", action="store_true", help="judge the existing reports")
    args = ap.parse_args()
    path = build(args.sets.split(","))
    if args.skip_run:
        base = json.loads((ROOT / "reports/eval/devtest.json").read_text())
        tuned = json.loads((ROOT / "reports/eval/devtest_mlx_8bit_ft1.json").read_text())
        _check_cached(path, base, "mlx_8bit")
        _check_cached(path, tuned, "mlx_8bit_ft1")
    else:
        base, tuned = run(path, "mlx_8bit"), run(path, "mlx_8bit_ft1")
    sys.exit(0 if judge(base, tuned) else 1)


if __name__ == "__main__":
    main()
