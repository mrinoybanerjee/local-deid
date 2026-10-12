"""Phase 1 day 1 harness: Privacy Filter runtime memory, speed, and agreement.

Every configuration runs in a fresh subprocess so memory peaks and cold starts
are not shared. Memory is the kernel's lifetime peak of phys_footprint for the
worker process (src/localdeid/sysmem.py), which counts Metal buffers too.

    .venv/bin/python harness/memspeed.py speed            # memory + latency matrix
    .venv/bin/python harness/memspeed.py speed --pressure # same, with RAM ballast + CPU burners
    .venv/bin/python harness/memspeed.py agree            # quality vs fp32 reference + gold recall
    .venv/bin/python harness/memspeed.py report           # print markdown tables from harness/out/

Outputs go to harness/out/ (raw JSON, not committed); the written summary is
reports/harness/day1.md.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

T_SPAWN_ENV = "LOCALDEID_T_SPAWN"
_T0 = time.time()

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "harness" / "inputs"
OUT = ROOT / "harness" / "out"
MODELS = ROOT / "models"
PY = str(ROOT / ".venv" / "bin" / "python")

DOC_ORDER = ["note_en_500", "note_es_500", "eval_mixed_5000", "eval_es_5000"]
FULL = 1 << 30  # "window" larger than any document: one pass, no windows
# ONNX attention is dense (T x T), so its memory grows with the square of the
# window; a single ONNX pass over a 7.8k-token evaluation would need ~20 GB and
# is not run. Overlap 1024 matches the model's receptive field (8 layers x 128),
# so windowed logits equal single-pass logits; w2048 trades that for memory.
WINDOWS = {
    "onnx_q4": {"w2048": (2048, 512), "w3072": (3072, 1024), "w4096": (4096, 1024)},
    "mlx_8bit": {"w2560": (2560, 1024), "w3072": (3072, 1024), "w4096": (4096, 1024),
                 "w8192": (8192, 1024), "full": (FULL, 0)},
}
GiB = 1 << 30


# --------------------------------------------------------------------------- child


def child(args: argparse.Namespace) -> None:
    t_spawn = float(os.environ.get(T_SPAWN_ENV, _T0))
    sys.path.insert(0, str(ROOT / "src"))
    from localdeid.sysmem import meminfo

    t = time.time()
    from localdeid.pf.backends import MODEL_DIRS, load_backend
    from localdeid.pf.model import PrivacyFilter

    rec: dict = {"backend": args.backend, "window": args.window, "overlap": args.overlap,
                 "threads": args.threads, "clear_cache": args.clear_cache, "arena": not args.no_arena}
    rec["import_s"] = time.time() - t
    t = time.time()
    backend = load_backend(args.backend, MODELS, threads=args.threads, clear_cache=args.clear_cache,
                           arena=not args.no_arena)
    pf = PrivacyFilter(backend, MODELS / MODEL_DIRS[args.backend], window=args.window,
                       overlap=args.overlap)
    gl = None
    if args.gliner:  # second model in the same worker, run after Privacy Filter (DECISIONS 21)
        from localdeid.gliner_onnx import GlinerOnnx

        gl = GlinerOnnx(MODELS, threads=args.threads)
        rec["gliner"] = True
    rec["load_s"] = time.time() - t
    m = meminfo()
    rec["after_load"] = {"footprint": m.footprint, "peak": m.footprint_peak}

    docs = args.docs.split(",")
    rec["docs"] = {}
    for i, name in enumerate(docs):
        text = (INPUTS / f"{name}.txt").read_text(encoding="utf-8")
        d: dict = {"tokens": len(pf.tokenizer.encode(text).ids), "words": len(text.split())}
        t = time.time()
        spans = pf.detect(text)
        if gl:
            t_g = time.time()
            gl.detect(text)
            d["gliner_first_s"] = time.time() - t_g
        d["first_s"] = time.time() - t
        if i == 0:
            rec["cold_s"] = time.time() - t_spawn  # spawn -> first document done
        runs, g_runs = [], []
        for _ in range(args.warm):
            t = time.time()
            pf.detect(text)
            if gl:
                t_g = time.time()
                gl.detect(text)
                g_runs.append(time.time() - t_g)
            runs.append(time.time() - t)
        d["warm_median_s"] = statistics.median(runs) if runs else None
        d["warm_max_s"] = max(runs) if runs else None
        if g_runs:
            d["gliner_warm_median_s"] = statistics.median(g_runs)
        d["n_spans"] = len(spans)
        m = meminfo()
        d["footprint_after"] = m.footprint
        d["peak_after"] = m.footprint_peak
        if args.dump:
            logits, _ = pf.token_logits(text)
            import numpy as np

            dump = OUT / "logits" / f"{args.dump}__{name}.npy"
            dump.parent.mkdir(parents=True, exist_ok=True)
            np.save(dump, logits.astype(np.float32))
            (OUT / "logits" / f"{args.dump}__{name}.spans.json").write_text(
                json.dumps([s.__dict__ for s in spans]))
        rec["docs"][name] = d
    if args.backend == "mlx_8bit":
        import mlx.core as mx

        rec["mlx_peak_memory"] = mx.get_peak_memory()
    m = meminfo()
    rec["peak"] = m.footprint_peak
    print("RESULT " + json.dumps(rec), flush=True)


# --------------------------------------------------------------------------- parent


def run_child(cfg: dict, docs: list[str], warm: int, dump: str | None = None,
              timeout: int = 3600) -> dict:
    cmd = [PY, __file__, "child", "--backend", cfg["backend"], "--window", str(cfg["window"]),
           "--overlap", str(cfg["overlap"]), "--docs", ",".join(docs), "--warm", str(warm)]
    if cfg.get("threads"):
        cmd += ["--threads", str(cfg["threads"])]
    if cfg.get("clear_cache"):
        cmd.append("--clear-cache")
    if cfg.get("arena") is False:
        cmd.append("--no-arena")
    if cfg.get("gliner"):
        cmd.append("--gliner")
    if dump:
        cmd += ["--dump", dump]
    env = dict(os.environ, **{T_SPAWN_ENV: repr(time.time())})
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    for line in p.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    return {"error": p.returncode, "stderr": p.stderr[-2000:], **cfg}


def speed_matrix() -> list[dict]:
    """Configurations in the final day 1 matrix (after the memory fixes)."""
    cfgs = []
    for wname, (w, o) in WINDOWS["onnx_q4"].items():
        for th in (4, 2):
            cfgs.append({"backend": "onnx_q4", "window": w, "overlap": o, "threads": th, "wname": wname})
        cfgs.append({"backend": "onnx_q4", "window": w, "overlap": o, "threads": 4, "arena": False,
                     "wname": wname})
    for wname, (w, o) in WINDOWS["mlx_8bit"].items():
        for cc in (False, True):
            cfgs.append({"backend": "mlx_8bit", "window": w, "overlap": o, "clear_cache": cc,
                         "wname": wname})
            # the fine-tuned model (DECISIONS 26): same base weights plus adapters
            cfgs.append({"backend": "mlx_8bit_ft1", "window": w, "overlap": o, "clear_cache": cc,
                         "wname": wname})
    return cfgs


def cfg_key(c: dict) -> str:
    """Stable name for a configuration, e.g. onnx_q4:w2048:t4:noarena or mlx_8bit:full:clear."""
    if c["backend"].startswith("onnx"):
        return f"{c['backend']}:{c['wname']}:t{c['threads']}" + ("" if c.get("arena", True) else ":noarena")
    return (f"{c['backend']}:{c['wname']}:" + ("clear" if c.get("clear_cache") else "cache")
            + (":gliner" if c.get("gliner") else ""))


class Pressure:
    """RAM ballast (random bytes, touched) plus CPU burners, to mimic a busy 8 GB Air."""

    def __init__(self, ballast_gib: float, burners: int):
        self.procs: list[subprocess.Popen] = []
        self.ballast_gib, self.burners = ballast_gib, burners

    def __enter__(self) -> "Pressure":
        code = ("import os,sys,time\nn=int(float(sys.argv[1])*(1<<30))\nb=bytearray(os.urandom(1<<20))*(n>>20)\n"
                "print('ready',flush=True)\nwhile True:\n  time.sleep(5)\n  s=sum(b[::1<<16])\n")
        p = subprocess.Popen([sys.executable, "-c", code, str(self.ballast_gib)],
                             stdout=subprocess.PIPE, text=True)
        assert p.stdout is not None and p.stdout.readline().strip() == "ready"
        self.procs.append(p)
        for _ in range(self.burners):
            self.procs.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))
        time.sleep(2)
        return self

    def __exit__(self, *exc) -> None:
        for p in self.procs:
            p.kill()
        for p in self.procs:
            p.wait()


def cmd_speed(args: argparse.Namespace) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfgs = speed_matrix()
    if args.gliner:
        cfgs = [dict(c, gliner=True) for c in cfgs]
    if args.only:
        cfgs = [c for c in cfgs if cfg_key(c) in args.only.split(",")]
    tag = args.tag or ("pressure" if args.pressure else "idle")
    results = []
    ctx = Pressure(args.ballast_gib, args.burners) if args.pressure else None
    if ctx:
        ctx.__enter__()
    try:
        for cfg in cfgs:
            print(f"[{tag}] {cfg}", flush=True)
            r = run_child(cfg, DOC_ORDER, args.warm)
            r["wname"] = cfg["wname"]
            # Cold start per document type: a fresh worker for each document,
            # timed from spawn to that document done (load included).
            if "error" not in r:
                r["cold_per_doc"] = {}
                for doc in DOC_ORDER:
                    c = run_child(cfg, [doc], warm=0)
                    r["cold_per_doc"][doc] = c.get("cold_s")
            results.append(r)
            print("   ", summarize(r), flush=True)
    finally:
        if ctx:
            ctx.__exit__()
    meta = {"host": host_info(), "pressure": args.pressure, "ballast_gib": args.ballast_gib,
            "burners": args.burners, "warm_runs": args.warm, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (OUT / f"speed_{tag}.json").write_text(json.dumps({"meta": meta, "results": results}, indent=1))


def summarize(r: dict) -> str:
    if "error" in r:
        return f"ERROR {r['error']}: {r['stderr'][-300:]}"
    d = r["docs"]
    cold = r.get("cold_per_doc") or {}
    cold_eval = max((v for k, v in cold.items() if k.startswith("eval") and v), default=float("nan"))
    return (f"peak {r['peak'] / GiB:.2f} GiB | cold note {r['cold_s']:.1f}s, eval {cold_eval:.1f}s | note warm "
            f"{d['note_en_500']['warm_median_s']:.2f}s | eval warm {d['eval_mixed_5000']['warm_median_s']:.2f}s"
            f" / es {d['eval_es_5000']['warm_median_s']:.2f}s")


def host_info() -> dict:
    def sh(*c: str) -> str:
        return subprocess.run(c, capture_output=True, text=True).stdout.strip()

    return {"cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
            "mem_bytes": int(sh("sysctl", "-n", "hw.memsize")), "macos": sh("sw_vers", "-productVersion")}


# --------------------------------------------------------------------------- agreement


AGREE_RUNS = {
    "ref_fp32_w3072": {"backend": "onnx_fp32_ref", "window": 3072, "overlap": 1024, "threads": 4},
    "onnx_q4_w4096": {"backend": "onnx_q4", "window": 4096, "overlap": 1024, "threads": 4},
    "onnx_q4_w2048": {"backend": "onnx_q4", "window": 2048, "overlap": 512, "threads": 4},
    "mlx_8bit_w4096": {"backend": "mlx_8bit", "window": 4096, "overlap": 1024, "clear_cache": True},
    "mlx_8bit_w8192": {"backend": "mlx_8bit", "window": 8192, "overlap": 1024, "clear_cache": True},
    "mlx_8bit_full": {"backend": "mlx_8bit", "window": FULL, "overlap": 0, "clear_cache": True},
}


def coverage(text: str, gold: list[dict], spans: list[dict]) -> dict:
    """Type-agnostic: an identifier counts as caught only if every non-space char is covered."""
    covered = bytearray(len(text))
    for s in spans:
        covered[s["start"]:s["end"]] = b"\x01" * (s["end"] - s["start"])
    per: dict[str, list[int]] = {}
    for g in gold:
        chars = [i for i in range(g["start"], g["end"]) if not text[i].isspace()]
        full = all(covered[i] for i in chars)
        per.setdefault(g["type"], [0, 0])
        per[g["type"]][0] += int(full)
        per[g["type"]][1] += 1
    return per


def cmd_agree(args: argparse.Namespace) -> None:
    import numpy as np

    sys.path.insert(0, str(ROOT / "src"))
    from localdeid.pf.viterbi import log_softmax

    OUT.mkdir(parents=True, exist_ok=True)
    if not args.skip_run:
        for name, cfg in AGREE_RUNS.items():
            print(f"dump {name}", flush=True)
            r = run_child(cfg, DOC_ORDER, warm=0, dump=name)
            if "error" in r:
                raise SystemExit(f"{name} failed: {r['stderr']}")
    ld = OUT / "logits"
    report: dict = {"vs_ref": {}, "window_vs_full": {}, "gold_coverage": {}}
    for doc in DOC_ORDER:
        text = (INPUTS / f"{doc}.txt").read_text(encoding="utf-8")
        gold = json.loads((INPUTS / f"{doc}.json").read_text())["spans"]
        ref = log_softmax(np.load(ld / f"ref_fp32_w3072__{doc}.npy"))
        ref_lab = ref.argmax(1)
        ref_spans = {(s["start"], s["end"], s["label"]) for s in
                     json.loads((ld / f"ref_fp32_w3072__{doc}.spans.json").read_text())}
        for name in AGREE_RUNS:
            lp = log_softmax(np.load(ld / f"{name}__{doc}.npy"))
            spans = json.loads((ld / f"{name}__{doc}.spans.json").read_text())
            sset = {(s["start"], s["end"], s["label"]) for s in spans}
            inter = len(sset & ref_spans)
            report["vs_ref"].setdefault(name, {})[doc] = {
                "token_argmax_agree": float((lp.argmax(1) == ref_lab).mean()),
                "max_abs_prob_diff": float(np.abs(np.exp(lp) - np.exp(ref)).max()),
                "span_precision_vs_ref": inter / len(sset) if sset else 1.0,
                "span_recall_vs_ref": inter / len(ref_spans) if ref_spans else 1.0,
            }
            report["gold_coverage"].setdefault(name, {})[doc] = coverage(text, gold, spans)
        # MLX: windowed vs one pass (exactness of the stitching). ONNX: the
        # reduced-overlap window vs the exact one (cost of the memory fallback).
        for rt, wa, wb in (("mlx_8bit", "w4096", "full"), ("mlx_8bit", "w8192", "full"),
                           ("onnx_q4", "w2048", "w4096")):
            a = log_softmax(np.load(ld / f"{rt}_{wa}__{doc}.npy"))
            b = log_softmax(np.load(ld / f"{rt}_{wb}__{doc}.npy"))
            report["window_vs_full"].setdefault(f"{rt} {wa} vs {wb}", {})[doc] = {
                "token_argmax_agree": float((a.argmax(1) == b.argmax(1)).mean()),
                "max_abs_prob_diff": float(np.abs(np.exp(a) - np.exp(b)).max()),
            }
    (OUT / "agree.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


# --------------------------------------------------------------------------- report


def cmd_report(_: argparse.Namespace) -> None:
    for p in sorted(OUT.glob("speed_*.json")):
        tag = p.stem[len("speed_"):]
        data = json.loads(p.read_text())
        print(f"\n### Speed and memory ({tag})\n")
        print(f"Host: {data['meta']['host']}; warm runs: {data['meta']['warm_runs']}"
              + (f"; ballast {data['meta']['ballast_gib']} GiB, {data['meta']['burners']} CPU burners"
                 if data["meta"]["pressure"] else ""))
        print("\n| Runtime | Window/overlap | Option | Load s | Cold s (note EN / eval mixed / eval ES) |"
              " Load peak GiB | Peak GiB | Note EN warm s | Note ES warm s | Eval mixed warm s | Eval ES warm s |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in data["results"]:
            if "error" in r:
                print(f"| {r['backend']} | {r.get('wname')} | | ERROR | | | | | | | |")
                continue
            opt = (f"threads={r['threads']}" + ("" if r.get("arena", True) else ", no arena")
                   if r["backend"].startswith("onnx") else ("clear_cache" if r["clear_cache"] else "cache"))
            d = r["docs"]
            win = "full" if r["window"] == FULL else f"{r['window']}/{r['overlap']}"
            cpd = r.get("cold_per_doc") or {}
            cold = " / ".join(f"{cpd[k]:.2f}" if cpd.get(k) else "-" for k in
                              ("note_en_500", "eval_mixed_5000", "eval_es_5000")) if cpd else f"{r['cold_s']:.2f} / - / -"
            print(f"| {r['backend']} | {win} | {opt} | {r['load_s']:.2f} | {cold} | "
                  f"{r['after_load']['peak'] / GiB:.2f} | {r['peak'] / GiB:.2f} | "
                  + " | ".join(f"{d[k]['warm_median_s']:.2f}" for k in DOC_ORDER) + " |")
    p = OUT / "agree.json"
    if p.exists():
        rep = json.loads(p.read_text())
        print("\n### Agreement with the fp32 reference (window 3072/1024, exact)\n")
        print("| Run | Doc | Token label agree | Span P vs ref | Span R vs ref | Max prob diff |")
        print("|---|---|---|---|---|---|")
        for name, docs in rep["vs_ref"].items():
            for doc, v in docs.items():
                print(f"| {name} | {doc} | {v['token_argmax_agree']:.4f} | {v['span_precision_vs_ref']:.3f} |"
                      f" {v['span_recall_vs_ref']:.3f} | {v['max_abs_prob_diff']:.3f} |")
        print("\n### Gold identifiers fully covered (Privacy Filter alone, type-agnostic)\n")
        types = sorted({t for d in rep["gold_coverage"].values() for x in d.values() for t in x})
        print("| Run | " + " | ".join(types) + " |")
        print("|---|" + "---|" * len(types))
        for name, docs in rep["gold_coverage"].items():
            cells = []
            for t in types:
                c = sum(x.get(t, [0, 0])[0] for x in docs.values())
                n = sum(x.get(t, [0, 0])[1] for x in docs.values())
                cells.append(f"{c}/{n} ({c / n:.1%})" if n else "-")
            print(f"| {name} | " + " | ".join(cells) + " |")
        print("\n### Window comparisons\n")
        print("| Comparison | Doc | Token label agree | Max prob diff |")
        print("|---|---|---|---|")
        for rt, docs in rep["window_vs_full"].items():
            for doc, v in docs.items():
                print(f"| {rt} | {doc} | {v['token_argmax_agree']:.4f} | {v['max_abs_prob_diff']:.2e} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("child")
    c.add_argument("--backend", required=True)
    c.add_argument("--window", type=int, required=True)
    c.add_argument("--overlap", type=int, required=True)
    c.add_argument("--threads", type=int, default=None)
    c.add_argument("--clear-cache", action="store_true")
    c.add_argument("--no-arena", action="store_true")
    c.add_argument("--gliner", action="store_true")
    c.add_argument("--docs", required=True)
    c.add_argument("--warm", type=int, default=5)
    c.add_argument("--dump", default=None)
    s = sub.add_parser("speed")
    s.add_argument("--warm", type=int, default=5)
    s.add_argument("--pressure", action="store_true")
    s.add_argument("--ballast-gib", type=float, default=9.0)
    s.add_argument("--burners", type=int, default=4)
    s.add_argument("--gliner", action="store_true", help="also load and run GLiNER in each worker")
    s.add_argument("--only", default=None, help="comma list of config keys (see cfg_key)")
    s.add_argument("--tag", default=None, help="output name: harness/out/speed_<tag>.json")
    a = sub.add_parser("agree")
    a.add_argument("--skip-run", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    {"child": child, "speed": cmd_speed, "agree": cmd_agree, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
