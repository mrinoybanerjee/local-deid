"""GLiNER experiment (SPEC 4.3, phase 1 only; dev-only, never shipped as is).

Step 1 (throwaway env with the `gliner` package and PyTorch):
    uv run --no-project --python 3.12 --with gliner --with torch --with protobuf \
        --with sentencepiece --with tiktoken --with huggingface_hub \
        python tools/gliner_experiment.py spans data/dev2/dev2.jsonl --model gx
Step 2 (project venv): score the engine with and without the GLiNER spans:
    .venv/bin/python tools/gliner_experiment.py score data/dev2/dev2.jsonl --model gx

Spans are organization-like only (labels below), computed on sentence chunks
with exact code-point offsets, and cached in harness/out/gliner_<model>_<set>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "harness" / "out"
LABELS = ["organization", "company", "daycare", "school", "government agency", "hospital or clinic",
          "church", "library"]
MODELS = {
    "gx": ("knowledgator/gliner-x-base", "a6c7a8fbfe2066c68d12c0480418504fde6d91d1"),
    "gm": ("urchade/gliner_multi-v2.1", "443d26d654e0324125a96bebd8e796c14ff2efe6"),
}


def chunks(text: str, max_words: int = 120):
    """Sentence-ish chunks (by line and sentence end), at most max_words words, with offsets."""
    for m in re.finditer(r"[^\n.!?]+[.!?]?", text):
        seg, base = m.group(), m.start()
        words = list(re.finditer(r"\S+", seg))
        for i in range(0, len(words), max_words):
            part = words[i:i + max_words]
            if part:
                s, e = part[0].start(), part[-1].end()
                yield base + s, seg[s:e]


def load_model(key: str):
    import shutil

    from gliner import GLiNER
    from huggingface_hub import snapshot_download

    repo, rev = MODELS[key]
    path = snapshot_download(repo, revision=rev, ignore_patterns=["onnx/*"])
    if key == "gx":  # its config asks for the stanza splitter (network); use whitespace words
        local = OUT / "gliner_gx"
        shutil.rmtree(local, ignore_errors=True)
        shutil.copytree(path, local)
        cfg = json.loads((local / "gliner_config.json").read_text())
        cfg["words_splitter_type"] = "whitespace"
        (local / "gliner_config.json").write_text(json.dumps(cfg))
        path = str(local)
    return GLiNER.from_pretrained(path)


def cmd_spans(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from localdeid.evaldata import load_jsonl

    model = load_model(args.model)
    docs = load_jsonl(args.path)
    out: dict[str, list] = {}
    t0 = time.time()
    for d in docs:
        spans = []
        for base, seg in chunks(d.text):
            for e in model.predict_entities(seg, LABELS, threshold=args.threshold):
                s, t = base + e["start"], base + e["end"]
                if d.text[s:t] == e["text"]:
                    spans.append([s, t, e["label"], round(float(e["score"]), 4)])
        out[d.doc_id] = spans
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"gliner_{args.model}_{Path(args.path).stem}.json"
    path.write_text(json.dumps(out))
    print(f"{len(docs)} docs in {time.time() - t0:.1f}s -> {path}")


def cmd_score(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tools"))
    from localdeid import pipeline
    from localdeid.cli import load_pf
    from localdeid.evaldata import load_jsonl, sanitize
    from localdeid.evaluate import score_doc, summarize
    from localdeid.pf.model import PFSpan
    from localdeid.pipeline import Settings, deidentify

    from localdeid.protect import protect
    from localdeid.rules import _CLINICAL_PHRASE_WORDS, _GENERIC_ORG_WORDS, _TEST_WORDS
    from localdeid.generic import all_generic

    def keep_span(text, s, e, prot):
        if any(p.start < e and s < p.end for p in prot):
            return False  # GLiNER is the least-trusted detector: touching a clinical token voids it
        words = re.findall(r"[^\W\d_]+", text[s:e])
        if not words or _TEST_WORDS.search(text[s:e]):
            return False
        return not all(w.lower() in _CLINICAL_PHRASE_WORDS or w.lower() in _GENERIC_ORG_WORDS or all_generic(w)
                       for w in words)

    cache = json.loads((OUT / f"gliner_{args.model}_{Path(args.path).stem}.json").read_text())
    pf = load_pf()
    pipeline.PF_KIND["gliner_org"] = "ORG"
    docs = [sanitize(d)[0] for d in load_jsonl(args.path)]
    for thr in [None] + [float(x) for x in args.thresholds.split(",")]:
        scores = []
        for d in docs:
            prot = protect(d.text)
            extra = [] if thr is None else [PFSpan(s, e, "gliner_org", sc) for s, e, _, sc in cache[d.doc_id]
                                            if sc >= thr and keep_span(d.text, s, e, prot)]
            r = deidentify(d.text, Settings(), pf_detect=lambda t, extra=extra: pf.detect(t) + extra)
            scores.append(score_doc(d, r))
        a = summarize(scores)["ALL"]
        rec = {k: f"{v['caught']}/{v['total']} ({v['rate']:.1%}, lb {v['lb95']:.1%})"
               for k, v in a["recall_by_type"].items() if k in ("AGENCY", "DAYCARE", "EMPLOYER", "HOSPITAL",
                                                               "ORG", "SCHOOL")}
        print(f"thr={thr}: clean {a['fully_clean']}/{a['docs']} clinical {a['clinical_hides']} "
              f"rows {a['median_check_items']} {rec}")
        print(f"   clinical by source: {a['clinical_hides_by_source']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spans")
    s.add_argument("path")
    s.add_argument("--model", default="gx", choices=list(MODELS))
    s.add_argument("--threshold", type=float, default=0.3)
    s.set_defaults(func=cmd_spans)
    c = sub.add_parser("score")
    c.add_argument("path")
    c.add_argument("--model", default="gx", choices=list(MODELS))
    c.add_argument("--thresholds", default="0.5,0.6,0.7")
    c.set_defaults(func=cmd_score)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
