"""Self-test (SPEC 4.6): model hashes, offline status, and a sample note.

Returns {"ok": bool, "checks": [{"name", "ok", "detail"}]}. Details are short
codes and counts, never note text.
"""

from __future__ import annotations

import socket
from pathlib import Path

SAMPLE = ("Name: Mateo Ruiz   DOB: 03/14/2021\nMother: Lucía Ruiz (919) 555-0142\n"
          "Mateo produced /k/ in 8/10 trials with mod cues at Little Sprouts Daycare. CPT 92507.")
EXPECT_HIDDEN = ("Mateo", "Ruiz", "Lucía", "555-0142", "Little Sprouts")
EXPECT_KEPT = ("/k/", "8/10", "mod cues", "92507")


def run(models: Path, engine=None) -> dict:
    checks = []

    def check(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, type(exc).__name__
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def hashes():
        from ..pf.artifacts import verify

        from ..pf.backends import ADAPTER_BASE, MODEL_DIRS

        # the files of the backend that actually runs (and the base an adapter builds on)
        pf = getattr(engine, "pf", None) if engine is not None else None
        name = getattr(getattr(pf, "backend", None), "name", "mlx_8bit")
        arts = ["o200k-bpe", MODEL_DIRS.get(name, MODEL_DIRS["mlx_8bit"])]
        if name in ADAPTER_BASE:
            arts.append(MODEL_DIRS[ADAPTER_BASE[name]])
        if engine is not None and getattr(engine, "gl", None) is not None:
            arts.append("gliner-x-base-q8")
        for a in arts:
            verify(models, a)
        return True, f"{len(arts)} artifacts verified"

    def offline():
        try:
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except OSError:
            return True, "sockets blocked"
        return False, "an internet socket could be created"

    def sample():
        from ..pipeline import Settings, deidentify

        pf = engine.pf.detect if engine is not None and engine.pf is not None else None
        r = deidentify(SAMPLE, Settings(), pf_detect=pf)
        leaks = [w for w in EXPECT_HIDDEN if w in r.output]
        lost = [w for w in EXPECT_KEPT if w not in r.output]
        ok = not leaks and not lost and not r.blocked
        return ok, f"{len(leaks)} visible identifiers, {len(lost)} changed clinical tokens, blocked={r.blocked}"

    check("model files", hashes)
    check("offline", offline)
    check("sample note", sample)
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
