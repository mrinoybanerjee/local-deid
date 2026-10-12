# Phase 2: the menu-bar app

Date: 2026-10-11. Built and verified on the Mac mini (Apple M4, 16 GB, macOS 26.6).
The 8 GB MacBook Air checks are still to run (`harness/air_run.sh`, see `docs/INSTALL.md`).

## What ships (SPEC 5)

- Menu-bar item "DeID" with Open, De-identify clipboard (⌃⌥D), Restore clipboard
  draft (⌃⌥R), and Quit. The hotkeys use Carbon `RegisterEventHotKey`, so the app
  needs no Accessibility permission.
- One window: Paste, Check, Restore, History, Caseload, Settings, and a first-run card.
- Check: chips in the note (red hidden, amber unsure), a ranked check list, select
  words and press H to hide, U to show again, ⌘Z, and ⌘C / Copy. Un-hiding a
  caseload name asks first (SPEC 4.3). Copy is never possible while the integrity
  check blocks the output.
- Restore by the ref line, or by fingerprint with a confirmation that names the child
  and the exact copy.
- Vault: one AES-GCM file per session with its own key, wrapped by a master key in
  the login Keychain (this Mac only, not synchronized); crash-safe writes; 30-day
  expiry except notes waiting for a draft; Delete everything.
- Clipboard: concealed and transient types; cleared after 2 minutes, and on quit.
- Engine worker: a separate process with a network guard (only Unix sockets), a
  span cache, restarts on crash, a timeout on every send and receive, and a
  10-minute idle stop.
- Self-test (Settings): model-file hashes of the active backend, the offline guard,
  and a sample note.
- `tools/build_app.sh` builds `~/Applications/Local DeID.app`, a signed launcher for
  the checkout's Python environment.

GLiNER is not in the app (DECISIONS 26).

## Verification on the mini

| Check | Result | Source |
|---|---|---|
| Unit and integration tests | 758 pass | `cd tests && PYTHONPATH=../src ../.venv/bin/python -m pytest -q` |
| Scripted UI run (`--smoke`), rules only and with the model | every step passes | `python -m localdeid.app --smoke [--no-model]` |
| Offline: no internet socket from the app, the worker, or a new WebKit helper | PASS (17–24 samples per run) | `harness/offline_check.sh` |
| Self-test through the worker | PASS | `python -m localdeid.app --selftest-exit` |
| Bundle launch, one instance, Quit ends app and worker | PASS | LaunchServices launch of the built bundle |
| Whole-app peak memory (mini, a proxy for the Air), SPEC workloads | 1,901–1,912 MiB lifetime peaks in two runs (app about 70, worker about 1,780, WebKit content about 55) | `harness/app_memory.sh` (`--memory-run`) |

The smoke run covers: first-run card, sample note, Check (chips, labels, ⌘C copies
the tagged note, native copies of real values blocked), Copy, Restore, History,
⌃⌥D and ⌃⌥R through Carbon, the caseload confirmation, Delete everything (every
screen cleared), the bridge guard, and the no-drop web view.

## Code review (`/cr`, GPT-6.1 Sol, high reasoning)

43 rounds. Every finding was fixed with a regression test or a smoke step. The loop
stopped at two empty rounds in a row (rounds 42 and 43, the second a full pass over
all 16 app files), per DECISIONS 24. The main defect classes found and fixed:

- Restore mappings: each copied ref keeps its own mapping; confirmation names the copy.
- Stale state: a failed engine run never leaves an old result to copy; a caseload
  change rebuilds results; replies that arrive late never replace newer screens.
- Privacy: the bridge refuses any page but the bundled one; native copies of real
  values are blocked; Delete everything and single deletions clear the screen; the
  clipboard is cleared after queued work on quit.
- Concurrency: one session queue for engine runs, saves, and reads; separate pipe
  and bookkeeping locks in the engine client; clipboard writes and timers under one lock.

## Not verified yet

- Everything on the 8 GB Air: memory, speed, the Keychain prompt, the hotkeys in
  other apps, and the real pasteboard of another Mac.
- One intermittent smoke failure at the ⌃⌥D step (twice in about 30 runs, both while
  a training job loaded the mini); the step now records the hotkey status and the
  clipboard text, and it has not recurred.
