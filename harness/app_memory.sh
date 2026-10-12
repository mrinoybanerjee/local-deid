#!/bin/zsh
# Whole-app memory (SPEC 3: peak < 2.5 GB with Chrome open). Runs the SPEC workloads
# (500-word notes, 5,000-word evaluations) through the window with the model
# (`python -m localdeid.app --memory-run`: temporary folder, in-memory key) and reads
# the kernel's lifetime peak footprint of the app, its worker, and its WebKit content
# process. A missing measurement fails the run. Writes harness/out/app_memory_<tag>.json.
# Quit Local DeID first. The run uses the clipboard and clears it at the end.
#   harness/app_memory.sh air
set -u
cd "${0:A:h}/.."
tag=${1:-local}
mkdir -p harness/out
PYTHONPATH=src .venv/bin/python -m localdeid.app --memory-run > harness/out/app_memory_$tag.json 2> harness/out/app_memory_$tag.log
rc=$?
cat harness/out/app_memory_$tag.json
exit $rc
