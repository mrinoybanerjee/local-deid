"""Print the SPEC 3 speed and memory result of one machine in a few lines.

    .venv/bin/python harness/summary.py harness/out/speed_air_pf.json harness/out/app_memory_air.json

Speed: SPEC 3 limits (500-word note < 5 s, 5,000-word evaluation < 30 s) apply both to
the warm model time per document (memspeed.py speed) and to the whole-app time per
workload through the window (app_memory.sh), which includes the rules, the review
window, and the copy. The app run starts with an untimed warm-up (the model load), so
every SPEC workload is timed warm. Memory: the whole-app lifetime peak against the 2.5 GB gate.
Every check must pass for "Result: PASS".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LIMIT_S = {"note": 5.0, "eval": 30.0}
WORKLOADS = {"note_en_500.txt", "note_es_500.txt", "eval_es_5000.txt", "eval_mixed_5000.txt"}


def main(speed_path: str, memory_path: str) -> int:
    ok = True
    speed = json.loads(Path(speed_path).read_text()) if Path(speed_path).exists() else {}
    results = speed.get("results") or [{}]
    if "docs" not in results[0]:
        print(f"Speed: no result ({results[0].get('error', 'missing file')})  FAIL")
        ok = False
    else:
        for name, d in results[0]["docs"].items():
            limit = LIMIT_S[name.split("_")[0]]
            passed = d["warm_median_s"] < limit
            ok &= passed
            print(f"Speed {name}: {d['warm_median_s']:.1f} s warm (limit {limit:.0f} s)  {'ok' if passed else 'FAIL'}")
    memory = json.loads(Path(memory_path).read_text()) if Path(memory_path).exists() else {}
    peak = memory.get("peak_mib", {}).get("sum")
    if not memory.get("ok") or peak is None:
        print(f"Whole-app memory: {peak} MiB, gate {memory.get('gate_mib')} MiB, missing {memory.get('missing')}  FAIL")
        ok = False
    else:
        print(f"Whole-app memory: {peak} MiB peak (limit {memory['gate_mib']} MiB)  ok")
    warmup = memory.get("warmup")
    if warmup:
        print(f"Whole-app warm-up: {warmup['seconds']:.1f} s cold (includes model load), not gated")
    workloads = memory.get("workloads", [])
    if not warmup or {w["name"] for w in workloads} != set(WORKLOADS):
        print("Whole-app speed: the warm-up or a SPEC workload is missing  FAIL")
        ok = False
    for w in workloads:
        limit = LIMIT_S[w["name"].split("_")[0]]
        passed = w["seconds"] < limit and w["copied"]
        ok &= passed
        print(f"Whole-app {w['name']}: {w['seconds']:.1f} s warm through the window (limit {limit:.0f} s), "
              f"copied {w['copied']}  {'ok' if passed else 'FAIL'}")
    print("Result: PASS" if ok else "Result: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
