"""Engine worker process (SPEC 4.1): holds the model, answers one JSON line per request.

    python -m localdeid.app.worker <models_dir> [--backend mlx_8bit] [--gliner] [--no-model]

Request:  {"id": 7, "op": "deid", "rev": 3, "text": "...", "settings": {...},
           "keep_keys": [...], "manual_hides": [[s, e], ...], "ref": "K7F2"}
          {"id": 8, "op": "ping"}   {"id": 9, "op": "selftest"}
Reply:    {"id": 7, "rev": 3, "ok": true, "result": {...}}
          {"id": 7, "rev": 3, "ok": false, "error": "code", "detail": "..."}

Model spans are cached by the text's hash, so an un-hide (which re-runs
deidentify) costs only the rules. stdout carries the protocol only; anything a
library prints goes to stderr. The network is blocked before any import.
"""

from __future__ import annotations

from . import netguard  # noqa: F401  (first: blocks the network)

import argparse
import hashlib
import json
import os
import sys
import traceback
from collections import OrderedDict
from pathlib import Path

PROTOCOL = 1
CACHE_SIZE = 32


class Engine:
    def __init__(self, models: Path, backend: str = "mlx_8bit", gliner: bool = False, model: bool = True):
        self.models = models
        self.pf = None
        self.gl = None
        if model:
            from ..pf.backends import MODEL_DIRS, load_backend
            from ..pf.model import PrivacyFilter

            self.pf = PrivacyFilter(load_backend(backend, models, clear_cache=True), models / MODEL_DIRS[backend])
        if gliner:
            from ..gliner_onnx import GlinerOnnx

            self.gl = GlinerOnnx(models)
        self._pf_cache: OrderedDict[str, list] = OrderedDict()
        self._gl_cache: OrderedDict[str, list] = OrderedDict()

    @staticmethod
    def _cached(cache: OrderedDict, text: str, fn):
        k = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if k in cache:
            cache.move_to_end(k)
            return cache[k]
        v = fn(text)
        cache[k] = v
        if len(cache) > CACHE_SIZE:
            cache.popitem(last=False)
        return v

    def deid(self, req: dict) -> dict:
        from ..pipeline import deidentify
        from .serialize import result_to_dict, settings_from_dict

        text = req["text"]
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        settings = settings_from_dict(req.get("settings") or {})
        hides = [(int(a), int(b)) for a, b in req.get("manual_hides") or []]
        pf = (lambda t: self._cached(self._pf_cache, t, self.pf.detect)) if self.pf else None
        gl = (lambda t: self._cached(self._gl_cache, t, self.gl.detect)) if self.gl else None
        r = deidentify(text, settings, pf_detect=pf, org_detect=gl, keep_keys=[str(k) for k in req.get("keep_keys") or []],
                       manual_hides=hides, ref=req.get("ref"))
        return result_to_dict(r)

    def selftest(self) -> dict:
        from .selftest import run

        return run(self.models, engine=self)


def serve(engine: Engine, inp, out) -> None:
    def send(obj: dict) -> None:
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    send({"op": "ready", "protocol": PROTOCOL, "model": engine.pf is not None, "gliner": engine.gl is not None})
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError("request must be an object")
        except ValueError as exc:
            send({"id": None, "ok": False, "error": "bad-request", "detail": str(exc)[:200]})
            continue
        rid, rev, op = req.get("id"), req.get("rev"), req.get("op")
        try:
            if op == "ping":
                send({"id": rid, "rev": rev, "ok": True, "result": "pong"})
            elif op == "deid":
                send({"id": rid, "rev": rev, "ok": True, "result": engine.deid(req)})
            elif op == "selftest":
                send({"id": rid, "rev": rev, "ok": True, "result": engine.selftest()})
            elif op == "quit":
                send({"id": rid, "rev": rev, "ok": True, "result": "bye"})
                return
            else:
                send({"id": rid, "rev": rev, "ok": False, "error": "unknown-op", "detail": str(op)[:50]})
        except Exception as exc:  # noqa: BLE001 (report to the UI, never crash on one request)
            # Error codes only: never echo text (SPEC 4.6 logs). The traceback goes to stderr,
            # which the UI discards.
            traceback.print_exc(file=sys.stderr)
            send({"id": rid, "rev": rev, "ok": False, "error": type(exc).__name__})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models")
    ap.add_argument("--backend", default="mlx_8bit", choices=["mlx_8bit", "mlx_8bit_ft1", "onnx_q4"])
    ap.add_argument("--gliner", action="store_true")
    ap.add_argument("--no-model", action="store_true")
    args = ap.parse_args(argv)
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr  # library prints never reach the protocol pipe
    engine = Engine(Path(args.models), args.backend, gliner=args.gliner, model=not args.no_model)
    serve(engine, sys.stdin, proto)
    return 0


if __name__ == "__main__":
    sys.exit(main())
