"""Parity check: localdeid.gliner_onnx against the `gliner` package (dev only).

Both read the same pinned int8 ONNX file. The package does its own word
splitting, tokenization, span building, and decoding; this module rebuilds
them without PyTorch. The check runs every chunk of a synthetic JSONL set
through both and requires the same spans, labels, and scores (|diff| < 1e-5).
Chunks with a word that has no subword token (zero-width or control
characters) are not compared: the package shifts later word indices there.
It also writes tests/fixtures/gliner_parity.json, which the unit tests use
without the package.

    uv run --no-project --python 3.12 --with gliner==0.2.29 --with torch --with onnxruntime \
        --with tokenizers --with sentencepiece --with protobuf --with tiktoken \
        python tools/gliner_parity.py data/dev2/dev2.jsonl
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from localdeid.evaldata import load_jsonl  # noqa: E402
from localdeid.gliner_onnx import LABELS, GlinerOnnx, chunks  # noqa: E402

THRESHOLD = 0.3
REVISION = "a6c7a8fbfe2066c68d12c0480418504fde6d91d1"
EDGE_TEXTS = [  # synthetic edge cases: invisible characters, punctuation runs, long words, Spanish
    "Mateo va a Little​ Sprouts Learning Center en Durham.",
    "Referida por CDSA⁠ de Durham;\x07 su mamá trabaja en Acme Logistics.",
    "---- ---- ---- Duke Regional Hospital ---- ----",
    "Asiste a la Escuela Primaria Lakewood y a la Iglesia San Pedro.",
    "Seen at UNC Health Rex (Raleigh) after referral from Wake County Public Schools.",
    "a" * 400 + " Smith Family Dentistry",
    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! Hope Valley Baptist Church",
]


def package_model(models: Path):
    from gliner import GLiNER

    local = ROOT / "harness" / "out" / "gliner_pkg"
    shutil.rmtree(local, ignore_errors=True)
    (local / "onnx").mkdir(parents=True)
    src = models / "gliner-x-base-src"  # the package reads the original single-file model
    for name in ("tokenizer.json", "gliner_config.json"):
        shutil.copy(src / name, local / name)
    # The package's tokenizer loader also reads the small tokenizer files of the same snapshot.
    from huggingface_hub import snapshot_download

    snap = Path(snapshot_download("knowledgator/gliner-x-base", revision=REVISION,
                                  allow_patterns=["tokenizer_config.json", "special_tokens_map.json",
                                                  "added_tokens.json", "spiece.model"]))
    for name in ("tokenizer_config.json", "special_tokens_map.json", "added_tokens.json", "spiece.model"):
        shutil.copy(snap / name, local / name)
    shutil.copy(src / "model_quantized.onnx", local / "onnx" / "model_quantized.onnx")
    cfg = json.loads((local / "gliner_config.json").read_text())
    cfg["words_splitter_type"] = "whitespace"  # the experiment's splitter (stanza needs the network)
    (local / "gliner_config.json").write_text(json.dumps(cfg))
    return GLiNER.from_pretrained(str(local), load_onnx_model=True, onnx_model_file="onnx/model_quantized.onnx",
                                  local_files_only=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--fixture-chunks", type=int, default=80)
    args = ap.parse_args()

    ours = GlinerOnnx(ROOT / "models")
    pkg = package_model(ROOT / "models")
    texts = [d.text for d in load_jsonl(args.path)] + EDGE_TEXTS
    n_chunks = n_spans = 0
    bad: list[str] = []
    max_diff = 0.0
    shifted = 0
    fixture = []
    for text in texts:
        for base, offs in chunks(text):
            chunk = text[base:base + offs[-1][1]]
            words = [chunk[a:b] for a, b in offs]
            mine = [(offs[s][0], offs[e][1], lab, sc) for s, e, lab, sc in ours.predict_words(words, THRESHOLD)]
            theirs = [(p["start"], p["end"], p["label"], float(p["score"]))
                      for p in pkg.predict_entities(chunk, list(LABELS), threshold=THRESHOLD)]
            n_chunks += 1
            n_spans += len(theirs)
            if len(ours.encode(words)[1]) < len(words):
                # A word with no subword token shifts every later word index in the package
                # (prepare_word_mask counts token runs); this module leaves the word out instead.
                shifted += 1
                continue
            if [m[:3] for m in mine] != [t[:3] for t in theirs]:
                bad.append(f"spans differ in {chunk[:60]!r}: {mine} vs {theirs}")
            else:
                for m, t in zip(mine, theirs, strict=True):
                    max_diff = max(max_diff, abs(m[3] - t[3]))
            if len(fixture) < args.fixture_chunks and theirs or text in EDGE_TEXTS:
                fixture.append({"text": chunk, "spans": [list(t) for t in theirs]})
    print(f"{n_chunks} chunks, {n_spans} package spans, {len(bad)} chunks differ, max score diff {max_diff:.2e}, "
          f"{shifted} chunks with token-less words not compared")
    for b in bad[:20]:
        print(" ", b)
    out = ROOT / "tests" / "fixtures" / "gliner_parity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"threshold": THRESHOLD, "labels": list(LABELS), "chunks": fixture},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"fixture: {len(fixture)} chunks -> {out.relative_to(ROOT)}")
    return 0 if not bad and max_diff < 1e-5 else 1


if __name__ == "__main__":
    sys.exit(main())
