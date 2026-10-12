"""Run the engine over a labelled set and report the SPEC 3 gates per stratum.

    .venv/bin/python tools/run_eval.py data/dev/dev.jsonl --split dev_train
    .venv/bin/python tools/run_eval.py data/gate/gate.jsonl          # metrics only

Two modes per run:
- cold:   no caseload (worst case: a child not yet in her roster);
- roster: a caseload built from the set's planted children and families
          (about 50-100 children at once, like a real caseload).

For the gate set the report holds metrics only: no missed strings, so the gate
cannot leak into tuning (SPEC 4.8). Dev reports list misses for engineering.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from localdeid.evaldata import Doc, code_digest, docs_digest, load_jsonl, sanitize  # noqa: E402
from localdeid.evaluate import score_doc, summarize  # noqa: E402
from localdeid.pipeline import Settings, deidentify  # noqa: E402
from localdeid.roster import Child, Related  # noqa: E402

from localdeid.generic import trim  # noqa: E402

RELATION = {"PARENT": "parent", "SIBLING": "sibling", "FAMILY": "family", "TEACHER": "teacher",
            "PET": "pet", "PEER": "peer"}


def caseload(docs: list[Doc]) -> list[Child]:
    """One roster entry per document's child, with the planted relatives."""
    kids = []
    for d in docs:
        names = []
        for s in d.identifiers:
            a, b = trim(d.text, s.start, s.end)  # "la maestra Carmen" -> "Carmen"
            names.append((s.type, d.text[a:b]))
        child = next((v for t, v in names if t == "CHILD" and len(v.split()) >= 2), None) or next(
            (v for t, v in names if t == "CHILD" and "." not in v), None)
        if not child:
            continue
        parts = child.split()
        first, surnames = (parts[0], [" ".join(parts[1:])]) if len(parts) > 1 else (parts[0], [])
        related = []
        seen = set()
        for t, v in names:
            if t in RELATION and v not in seen and "." not in v:
                seen.add(v)
                rp = v.split()
                related.append(Related(rp[0], RELATION[t], [" ".join(rp[1:])] if len(rp) > 1 else []))
        kids.append(Child(first, surnames, related=related))
    return kids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--split", default=None, help="only docs whose meta.split equals this")
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--gliner", action="store_true", help="add the GLiNER organization detector")
    ap.add_argument("--ab", action="store_true",
                    help="run without and with GLiNER in one invocation (DECISIONS 23 gate rule)")
    ap.add_argument("--modes", default="cold,roster")
    ap.add_argument("--backend", default="mlx_8bit", help="Privacy Filter backend, e.g. mlx_8bit_ft1 (DECISIONS 26)")
    args = ap.parse_args()

    docs = []
    relabelled = 0
    for d in load_jsonl(args.path):
        d, n = sanitize(d)
        docs.append(d)
        relabelled += n
    if args.split:
        docs = [d for d in docs if d.meta.get("split") == args.split]
    use_gliner = args.gliner or args.ab
    name = (Path(args.path).stem + (f"_{args.split}" if args.split else "")
            + ("_ab" if args.ab else "_gliner" if args.gliner else "")
            + (f"_{args.backend}" if args.backend != "mlx_8bit" else ""))
    is_gate = any(d.meta.get("split") == "gate" for d in docs)
    pf = None
    if not args.no_model:
        from localdeid.cli import load_pf

        pf = load_pf(args.backend)
    org_detect = None
    if use_gliner:
        from localdeid.cli import load_gliner

        gl = load_gliner()
        org_cache: dict[str, list] = {}  # the same text runs in every mode

        def org_detect(t: str) -> list:
            if t not in org_cache:
                org_cache[t] = gl.detect(t)
            return org_cache[t]
    out_dir = ROOT / "reports" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    from localdeid.pf.backends import MODEL_DIRS

    manifest = json.loads((ROOT / "models" / "MANIFEST.json").read_text(encoding="utf-8"))["artifacts"]
    art = manifest.get(MODEL_DIRS.get(args.backend, ""), {}).get("files", {})
    report: dict = {"set": name, "docs": len(docs), "model": not args.no_model, "backend": args.backend,
                    "docs_sha256": docs_digest(docs),  # the sanitized documents this run scored
                    "code_sha256": code_digest(),  # the engine and scorer that scored them
                    "artifact_sha256": {f: m["sha256"] for f, m in sorted(art.items())},
                    "gliner": "ab" if args.ab else args.gliner,
                    "gold_codes_relabelled_keep": relabelled,
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "modes": {}}
    configs = [("A", None), ("B", org_detect)] if args.ab else [("", org_detect)]
    runs = [(c, od, mode) for c, od in configs for mode in args.modes.split(",")]
    for cfg, cfg_org_detect, mode in runs:
        key = f"{cfg}:{mode}" if cfg else mode
        if mode == "roster":
            settings = Settings(children=caseload(docs))
        else:
            settings = Settings()
        scores, misses, blocked = [], defaultdict(list), []
        t0 = time.time()
        for d in docs:
            doc_settings = settings
            if mode == "roster":
                # The clinician's employer is one known string in her settings (SPEC 6 [EMPLOYER]).
                employers = sorted({d.text[s.start:s.end] for s in d.identifiers if s.type == "EMPLOYER"})
                doc_settings = Settings(children=settings.children, employer_names=employers)
            r = deidentify(d.text, doc_settings, pf_detect=pf.detect if pf else None, org_detect=cfg_org_detect)
            s = score_doc(d, r)
            scores.append(s)
            if r.blocked:
                blocked.append({"doc": d.doc_id, "failures": [f.code for f in r.failures]})
            if not is_gate:
                for t, v in s.missed:
                    misses[t].append(f"{d.doc_id}: {v}")
                for v in s.clinical_hides:
                    misses["CLINICAL_HIDE"].append(f"{d.doc_id}: {v}")
                for v in s.extra_hides:
                    misses["EXTRA_HIDE"].append(f"{d.doc_id}: {v}")
        report["modes"][key] = {
            "seconds": round(time.time() - t0, 1),
            "roster_children": len(settings.children),
            "strata": summarize(scores),
            "blocked_docs": blocked if not is_gate else len(blocked),
            **({"misses": dict(misses)} if not is_gate else {}),
        }
        all_ = report["modes"][key]["strata"]["ALL"]
        print(f"[{name} {key}] docs {all_['docs']} clean {all_['fully_clean']} "
              f"({all_['fully_clean_rate']:.1%}) clinical hides {all_['clinical_hides']} "
              f"median checks {all_['median_check_items']} blocked {all_['blocked']} "
              f"extra hides {all_['extra_hides']}", flush=True)
        for t, v in all_["recall_by_type"].items():
            print(f"    {t:12s} {v['caught']:4d}/{v['total']:<4d} {v['rate']:.1%} (lb95 {v['lb95']:.1%})")
    (out_dir / f"{name}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"wrote reports/eval/{name}.json")


if __name__ == "__main__":
    main()
