"""A stand-in engine worker for tests/test_app_engine.py (no model)."""
import json
import os
import sys
import time

marker = sys.argv[1] if len(sys.argv) > 1 else ""
mode = sys.argv[2] if len(sys.argv) > 2 else ""
if mode == "never-ready":
    time.sleep(60)
print(json.dumps({"op": "ready", "protocol": 1}), flush=True)
if mode == "never-reads":
    time.sleep(60)
for line in sys.stdin:
    req = json.loads(line)
    text = req.get("text", "")
    if text == "crash-once" and marker and not os.path.exists(marker):
        open(marker, "w").close()
        os._exit(3)
    if text == "crash-always":
        os._exit(3)
    if text == "slow":
        time.sleep(5)
    if text == "bad":
        print(json.dumps({"id": req["id"], "rev": req.get("rev"), "ok": False, "error": "ValueError"}), flush=True)
        continue
    print(json.dumps({"id": req["id"], "rev": req.get("rev"), "ok": True, "result": {"echo": text, "pid": os.getpid()}}),
          flush=True)
