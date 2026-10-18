"""Small synthetic MLX worker/API/vault check; no native app services or downloads.

Run with the mlx extra installed and verified shipping artifacts in models/:
    PYTHONPATH=src python tools/check_model_runtime.py --output /tmp/localdeid-model-check.json
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["PYTHONPATH"] = str(ROOT / "src")

from localdeid.app.engine import EngineClient
from localdeid.app.api import Api
from localdeid.app.session import Manager
from localdeid.app.vault import Vault, MemoryKeyStore


class MemoryClipboard:
    def __init__(self):
        self.text = ""
        self.count = 0

    def write(self, text):
        self.text = text
        self.count += 1

    def read(self):
        return self.text

    def change_count(self):
        return self.count


# Fictional identifiers only. Expected preservation is fixed before inference.
FIXTURES = [
    dict(id="sample", text="Name: Mateo Ruiz   DOB: 03/14/2021\nMother: Lucía Ruiz (919) 555-0142\n"
         "Mateo produced /k/ in 8/10 trials with mod cues at Little Sprouts Daycare. CPT 92507.",
         hidden=["Mateo", "Ruiz", "Lucía", "555-0142", "03/14/2021", "Little Sprouts"],
         kept=["/k/", "8/10", "mod cues", "92507"]),
    dict(id="spanish", text="Ana Ruiz llamó al 919-555-0142 el 03/14/2026. Email ana.ruiz@example.com.",
         hidden=["Ana Ruiz", "919-555-0142", "03/14/2026", "ana.ruiz@example.com"], kept=[]),
    dict(id="unicode", text="Name: José Núñez 😀\nJosé practiced /ɲ/ in 7/10 trials.",
         hidden=["José", "Núñez"], kept=["😀", "/ɲ/", "7/10"]),
    dict(id="clinical-only", text="Produced /k/ and /s/ in 8/10 trials with mod cues. CPT 92507.",
         hidden=[], kept=["/k/", "/s/", "8/10", "mod cues", "92507"]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output path; existing evidence is not overwritten.")
    report = dict(recorded_at=datetime.now(timezone.utc).isoformat(),
                  source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  source_restore_sha256=hashlib.sha256((ROOT / "src/localdeid/restore.py").read_bytes()).hexdigest(),
                  runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  python=platform.python_version(), os=platform.platform(),
                  packages={name: version(name) for name in ("numpy", "tiktoken", "mlx", "mlx-metal", "cryptography")},
                  backend="mlx_8bit", model=True, native_clipboard=False, keychain=False,
                  limitations="Four synthetic fixtures; not clinical acceptance, multilingual recall, OS-wide network isolation, or an 8 GB Air performance test.",
                  checks={}, fixtures=[])

    def check(name, passed):
        report["checks"][name] = bool(passed)
        assert passed, name

    engine = EngineClient(ROOT / "models", backend="mlx_8bit", model=True)
    manager = None
    completed = False
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="localdeid-model-check-") as temporary:
            clipboard = MemoryClipboard()
            vault = Vault(Path(temporary) / "vault", MemoryKeyStore())
            manager = Manager(engine, vault, clipboard=clipboard)
            api = Api(manager, vault, clipboard=clipboard, engine=engine)
            report["selftest"] = api.selftest()
            check("worker_selftest", report["selftest"].get("selftest", {}).get("ok"))
            print("Worker self-test passed", flush=True)
            saved = []
            for fixture in FIXTURES:
                start = time.perf_counter()
                out = api.paste(fixture["text"])
                check(fixture["id"] + ":paste", out["ok"])
                sid = out["view"]["id"]
                check(fixture["id"] + ":copy", api.copy(sid)["ok"])
                tagged = clipboard.text
                if fixture["id"] in ("sample", "unicode"):
                    context = tagged.splitlines()[1]
                    check(fixture["id"] + ":no_inferred_gender", "boy" not in context and "girl" not in context)
                if fixture["hidden"]:
                    check(fixture["id"] + ":identifiers_hidden", all(value not in tagged for value in fixture["hidden"]))
                if fixture["kept"]:
                    check(fixture["id"] + ":clinical_tokens_preserved", all(value in tagged for value in fixture["kept"]))
                restored = api.restore(tagged)
                check(fixture["id"] + ":restore_exact", restored["ok"] and restored["restore"]["text"] == fixture["text"])
                check(fixture["id"] + ":copy_restored", api.copy_restored(sid, restored["restore"]["rtoken"])["ok"] and clipboard.text == fixture["text"])
                report["fixtures"].append(dict(id=fixture["id"], tagged=tagged, counts=out["view"]["counts"],
                                               elapsed_seconds=time.perf_counter() - start))
                saved.append((tagged, fixture["text"]))
                print(fixture["id"], "round trip passed", flush=True)
            engine.cancel()
            report["restarted_selftest"] = engine.selftest()
            check("worker_restart_model_selftest", engine.restarts >= 1 and report["restarted_selftest"]["ok"])
            manager.close()
            manager = Manager(engine, vault, clipboard=clipboard)
            api = Api(manager, vault, clipboard=clipboard, engine=engine)
            for i, (tagged, original) in enumerate(saved):
                check(f"saved_session:{i}:restore_exact", api.restore(tagged)["restore"]["text"] == original)
            check("vault_has_no_plaintext_notes", all(
                fixture["text"].encode() not in file.read_bytes()
                for file in (Path(temporary) / "vault").rglob("*") if file.is_file()
                for fixture in FIXTURES))
            check("delete_temporary_sessions", api.delete_everything("DELETE")["ok"])
            check("delete_clears_history", api.history()["rows"] == [])
            check("deleted_mapping_cannot_restore", api.restore(saved[0][0])["restore"]["status"] == "no-session")
            check("new_session_after_delete", api.paste(FIXTURES[0]["text"])["ok"])
            completed = True
    finally:
        if manager is not None:
            manager.close()
        engine.close()
        report["elapsed_seconds"] = time.perf_counter() - started
        report["all_pass"] = completed and all(report["checks"].values())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report["checks"], indent=2), flush=True)


if __name__ == "__main__":
    main()
