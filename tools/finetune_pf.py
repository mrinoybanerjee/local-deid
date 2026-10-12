"""Fine-tune the Privacy Filter with LoRA and an organization class (DECISIONS 26).

    PYTHONPATH=src .venv/bin/python tools/finetune_pf.py train --sets dev,dev2,dev3,dev4
    PYTHONPATH=src .venv/bin/python tools/finetune_pf.py labels data/dev3/dev3.jsonl   # label check

Trains on the `dev_train` split only (synthetic data, no AI4Privacy) and writes
models/pf-mlx-8bit-ft1/ (adapters.safetensors, config.json) plus its hashes in
models/MANIFEST.json. The base 8-bit weights are not changed. Judge the result
with `tools/run_eval.py <set> --split dev_test --backend mlx_8bit_ft1`.
Logs carry counts and losses only, never document text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from localdeid.evaldata import Doc, load_jsonl, sanitize  # noqa: E402
from localdeid.pf.tokenize import PFTokenizer  # noqa: E402

MODELS = ROOT / "models"
BASE = "pf-mlx-8bit"
OUT = "pf-mlx-8bit-ft1"

# Gold type -> Privacy Filter entity (DECISIONS 26). KEEP, TIME, and anything else -> O.
GOLD_TO_PF = {
    **{t: "private_person" for t in ("CHILD", "PARENT", "SIBLING", "FAMILY", "TEACHER", "PROVIDER", "PEER",
                                     "PERSON", "PET", "SURNAME")},
    **{t: "private_organization" for t in ("AGENCY", "DAYCARE", "EMPLOYER", "HOSPITAL", "ORG", "SCHOOL")},
    "PLACE": "private_address", "ADDRESS": "private_address", "DATE": "private_date", "PHONE": "private_phone", "EMAIL": "private_email",
    "URL": "private_url", "ID": "account_number", "MEDICAID_ID": "account_number",
}


def label_space() -> tuple[dict[int, str], dict[str, int]]:
    from localdeid.pf.lora import ORG_LABELS

    base = json.loads((MODELS / BASE / "config.json").read_text(encoding="utf-8"))
    id2label = {int(k): v for k, v in base["id2label"].items()}
    for lab in ORG_LABELS:
        id2label[len(id2label)] = lab
    return id2label, {v: k for k, v in id2label.items()}


def token_labels(doc: Doc, tok: PFTokenizer, label2id: dict[str, int]) -> tuple[np.ndarray, np.ndarray]:
    """Token ids and BIOES label ids. A token belongs to a gold span if it covers one of
    the span's non-space characters; adjacent spans stay separate entities."""
    enc = tok.encode(doc.text)
    n = len(doc.text)
    span_of = np.full(n, -1, dtype=np.int64)
    ents = []
    for s in sorted(doc.identifiers, key=lambda s: (s.start, s.end)):
        ent = GOLD_TO_PF.get(s.type)
        if ent is None:
            continue
        k = len(ents)
        ents.append(ent)
        for i in range(s.start, s.end):
            if not doc.text[i].isspace():
                span_of[i] = k
    owner = np.full(len(enc.ids), -1, dtype=np.int64)
    for t in range(len(enc.ids)):
        hits = span_of[enc.starts[t]:enc.ends[t]]
        hits = hits[hits >= 0]
        if hits.size:
            owner[t] = int(np.bincount(hits).argmax())
    labels = np.zeros(len(enc.ids), dtype=np.int64)  # 0 = O
    t = 0
    while t < len(owner):
        k = owner[t]
        if k < 0:
            t += 1
            continue
        u = t
        while u + 1 < len(owner) and owner[u + 1] == k:
            u += 1
        ent = ents[k]
        if u == t:
            labels[t] = label2id[f"S-{ent}"]
        else:
            labels[t] = label2id[f"B-{ent}"]
            labels[t + 1:u] = label2id[f"I-{ent}"]
            labels[u] = label2id[f"E-{ent}"]
        t = u + 1
    return np.asarray(enc.ids, dtype=np.int32), labels


def windows(ids: np.ndarray, labels: np.ndarray, size: int, overlap: int) -> list[tuple[np.ndarray, np.ndarray]]:
    if len(ids) <= size:
        return [(ids, labels)]
    out, step = [], size - overlap
    for a in range(0, len(ids), step):
        out.append((ids[a:a + size], labels[a:a + size]))
        if a + size >= len(ids):
            break
    return out


def load_split(sets: list[str], split: str) -> list[Doc]:
    docs = []
    for name in sets:
        for d in load_jsonl(ROOT / "data" / name / f"{name}.jsonl"):
            if d.meta.get("split") == split:
                docs.append(sanitize(d)[0])
    return docs


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def cmd_labels(args: argparse.Namespace) -> None:
    """Check the label conversion: entities per type and tokens per label (counts only)."""
    from collections import Counter

    id2label, label2id = label_space()
    tok = PFTokenizer(MODELS / "o200k-bpe" / "o200k_base.tiktoken")
    counts: Counter = Counter()
    for d in load_jsonl(args.path):
        _, labels = token_labels(sanitize(d)[0], tok, label2id)
        counts.update(id2label[int(x)] for x in labels)
    for lab, c in sorted(counts.items()):
        print(f"{lab:28} {c}")


def cmd_train(args: argparse.Namespace) -> None:
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten

    import localdeid.vendor.openmed_privacy_filter as vendor
    from localdeid.pf.artifacts import verify
    from localdeid.pf.backends import MlxBackend
    from localdeid.pf.lora import adapter_state, attach

    sets = args.sets.split(",")
    id2label, label2id = label_space()
    tok = PFTokenizer(MODELS / "o200k-bpe" / "o200k_base.tiktoken")
    train_docs = load_split(sets, "dev_train")
    examples = []
    for d in train_docs:
        ids, labels = token_labels(d, tok, label2id)
        examples += windows(ids, labels, args.window, args.overlap)
    if args.max_windows:
        examples = random.Random(args.seed).sample(examples, min(args.max_windows, len(examples)))
    n_tokens = sum(len(i) for i, _ in examples)
    print(f"train: {len(train_docs)} docs, {len(examples)} windows, {n_tokens} tokens", flush=True)

    mx.random.seed(args.seed)
    backend = MlxBackend(verify(MODELS, BASE))
    model = backend.model
    targets = args.targets.split(",")
    attach(model, rank=args.rank, alpha=args.alpha, targets=targets, num_labels=len(id2label), seed=args.seed)
    vendor.TRAINING = True
    model.train()
    n_params = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
    print(f"trainable parameters: {n_params}", flush=True)

    def loss_fn(m, x, y):
        logits = m(x).astype(mx.float32)
        return nn.losses.cross_entropy(logits, y, reduction="mean")

    loss_and_grad = nn.value_and_grad(model, loss_fn)
    opt = optim.MultiOptimizer(
        [optim.AdamW(learning_rate=args.head_lr, weight_decay=0.0),
         optim.AdamW(learning_rate=args.lr, weight_decay=0.0)],
        [lambda name, _: name.startswith("unembedding")])
    rng = random.Random(args.seed)
    step, skipped, t0 = 0, 0, time.time()
    total_steps = args.epochs * ((len(examples) + args.accum - 1) // args.accum)
    for epoch in range(args.epochs):
        order = list(range(len(examples)))
        rng.shuffle(order)
        running, seen = 0.0, 0
        for a in range(0, len(order), args.accum):
            batch = order[a:a + args.accum]
            grads, losses = None, []
            for k in batch:
                ids, labels = examples[k]
                loss, g = loss_and_grad(model, mx.array(ids[None, :]), mx.array(labels[None, :]))
                grads = g if grads is None else tree_map_add(grads, g)
                mx.eval(grads, loss)
                losses.append(float(loss))
            grads = tree_map_scale(grads, 1.0 / len(batch))
            grads, norm = optim.clip_grad_norm(grads, max_norm=args.clip)
            mx.eval(grads, norm)
            step += 1
            if not (np.isfinite(float(norm)) and all(np.isfinite(x) for x in losses)):
                skipped += 1  # a non-finite step changes nothing (DECISIONS 26 addendum)
                print(f"skip step {step}: non-finite gradient or loss", flush=True)
                continue
            opt.update(model, grads)
            mx.eval(model.parameters(), opt.state)
            running += sum(losses)
            seen += len(losses)
            if step % args.log_every == 0 or step == total_steps:
                rate = (time.time() - t0) / step
                print(f"epoch {epoch + 1} step {step}/{total_steps} loss {running / max(seen, 1):.4f} "
                      f"{rate:.1f}s/step eta {rate * (total_steps - step) / 60:.0f} min "
                      f"peak {mx.get_peak_memory() / 2**30:.1f} GiB skipped {skipped}", flush=True)
                running, seen = 0.0, 0

    out = MODELS / args.out
    out.mkdir(parents=True, exist_ok=True)
    state = adapter_state(model, targets)
    mx.save_safetensors(str(out / "adapters.safetensors"), {k: v.astype(mx.float32) for k, v in state.items()})
    cfg = json.loads((MODELS / BASE / "config.json").read_text(encoding="utf-8"))
    cfg["num_labels"] = len(id2label)
    cfg["id2label"] = {str(k): v for k, v in sorted(id2label.items())}
    cfg["label2id"] = {v: k for k, v in id2label.items()}
    cfg["_lora"] = {"rank": args.rank, "alpha": args.alpha, "targets": targets}
    cfg["_base_artifact"] = BASE
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    cfg["_training"] = {"sets": sets, "split": "dev_train", "docs": len(train_docs), "windows": len(examples),
                        "tokens": n_tokens, "epochs": args.epochs, "lr": args.lr, "head_lr": args.head_lr,
                        "accum": args.accum, "window": args.window, "seed": args.seed, "clip": args.clip,
                        "skipped_steps": skipped, "commit": commit}
    (out / "config.json").write_text(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path = MODELS / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][args.out] = {
        "repo": f"local: tools/finetune_pf.py on {BASE}", "revision": commit,
        "license": "Apache-2.0 (adapters of OpenMed/privacy-filter-mlx-8bit)",
        "files": {name: {"size": (out / name).stat().st_size, "sha256": _sha256(out / name),
                         "derived_by": "tools/finetune_pf.py train"}  # built here, never downloaded
                  for name in ("adapters.safetensors", "config.json")},
    }
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} and its MANIFEST.json entry ({(time.time() - t0) / 60:.0f} min)", flush=True)


def tree_map_add(a, b):
    from mlx.utils import tree_map

    return tree_map(lambda x, y: x + y, a, b)


def tree_map_scale(a, s):
    from mlx.utils import tree_map

    return tree_map(lambda x: x * s, a)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--sets", default="dev,dev2,dev3,dev4")
    t.add_argument("--rank", type=int, default=16)
    t.add_argument("--alpha", type=float, default=32.0)
    t.add_argument("--targets", default="attn.qkv,attn.out")
    t.add_argument("--epochs", type=int, default=3)
    t.add_argument("--lr", type=float, default=1e-4)
    t.add_argument("--head-lr", type=float, default=1e-3)
    t.add_argument("--accum", type=int, default=2, help="windows per optimizer step")
    t.add_argument("--clip", type=float, default=1.0, help="global gradient-norm clip")
    t.add_argument("--window", type=int, default=2048)
    t.add_argument("--overlap", type=int, default=256)
    t.add_argument("--seed", type=int, default=26)
    t.add_argument("--log-every", type=int, default=25)
    t.add_argument("--max-windows", type=int, default=0, help="smoke test: train on this many windows")
    t.add_argument("--out", default=OUT, help="artifact folder name under models/")
    t.set_defaults(func=cmd_train)
    lb = sub.add_parser("labels")
    lb.add_argument("path")
    lb.set_defaults(func=cmd_labels)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
