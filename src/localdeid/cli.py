"""Command-line tool: the same engine the app uses (SPEC 4.1).

    localdeid deid < note.txt               # print the copy block (ref, context, tagged text)
    localdeid deid --no-model note.txt      # rules, fields, roster, gazetteer only
    localdeid deid --json note.txt          # items, checks, and output as JSON
    localdeid selftest                      # model hashes, offline status, sample note

The tool reads synthetic or local text only and writes nothing to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import Settings, deidentify
from .restore import copy_block

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"


def load_pf(backend: str = "mlx_8bit", window: int | None = None, overlap: int | None = None):
    from .pf.backends import MODEL_DIRS, load_backend
    from .pf.model import PrivacyFilter

    b = load_backend(backend, MODELS, clear_cache=True)
    return PrivacyFilter(b, MODELS / MODEL_DIRS[backend], window=window, overlap=overlap)


def load_gliner():
    from .gliner_onnx import GlinerOnnx

    return GlinerOnnx(MODELS)


def cmd_deid(args: argparse.Namespace) -> int:
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    pf = None if args.no_model else load_pf(args.backend)
    gl = load_gliner() if args.gliner else None
    result = deidentify(raw, Settings(), pf_detect=pf.detect if pf else None,
                        org_detect=gl.detect if gl else None)
    if args.json:
        print(json.dumps({
            "ref": result.ref, "blocked": result.blocked, "output": result.output,
            "failures": [f.__dict__ for f in result.failures],
            "items": [{"tag": it.tag, "kind": it.kind, "sure": it.sure, "hidden": it.hidden,
                       "start": it.start, "end": it.end, "sources": it.sources, "notes": it.notes}
                      for it in result.items],
        }, ensure_ascii=False, indent=1))
    elif result.blocked:
        for f in result.failures:
            print(f"BLOCKED {f.code}: {f.word!r} {f.detail}", file=sys.stderr)
        return 2
    else:
        print(copy_block(result))
    return 0


def cmd_selftest(_: argparse.Namespace) -> int:
    """The app's self-test (model hashes, offline status, sample note) in this process."""
    from types import SimpleNamespace

    from .app import netguard  # noqa: F401  (blocks the network, as in the app worker)
    from .app.selftest import run

    try:
        engine = SimpleNamespace(pf=load_pf(), gl=None)
    except Exception as exc:  # noqa: BLE001 (a missing or changed model file: the hash check says which)
        print(f"model load: FAILED ({type(exc).__name__})")
        engine = SimpleNamespace(pf=None, gl=None)
    out = run(MODELS, engine)
    for c in out["checks"]:
        print(f"{c['name']}: {'ok' if c['ok'] else 'FAILED'} ({c['detail']})")
    return 0 if out["ok"] and engine.pf is not None else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="localdeid")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deid")
    d.add_argument("file", nargs="?")
    d.add_argument("--no-model", action="store_true")
    d.add_argument("--gliner", action="store_true", help="add the GLiNER organization detector (experiment)")
    d.add_argument("--backend", default="mlx_8bit", choices=["mlx_8bit", "mlx_8bit_ft1", "onnx_q4"])
    d.add_argument("--json", action="store_true")
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    return {"deid": cmd_deid, "selftest": cmd_selftest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
