# Phase 2 build plan (Fable 5.1 advisor, 2026-10-11)

Source of truth stays SPEC.md. This is the agreed build plan for the menu-bar app.

Plan below. I read SPEC 4.1, 4.4–4.7, 5, 6, 8 and the engine API (`pipeline.py`, `restore.py`, `cli.py`, `pyproject.toml`, `pf/backends.py`). The phase 1 backend default in the CLI is `mlx_8bit`; the plan does not depend on which one wins.

## 1. Libraries (all non-GPL)

1. **pywebview 6.2.1** (BSD-3, released 2026-04-15, has the macOS Tahoe retain fixes). Cocoa backend, which pulls `pyobjc-framework-Cocoa` and `-WebKit` (MIT). Add `pyobjc-framework-Security` for Keychain and `pyobjc-framework-Quartz` for the screenshot window list.
2. **Menu bar: pyobjc `NSStatusItem`, not rumps.** rumps runs its own NSApplication loop and would fight pywebview for the main thread. Create the status item inside `webview.start(func)` through `PyObjCTools.AppHelper.callAfter`, which hops to the main thread. Hide the Dock icon with `NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)` at runtime, so `python -m` launches look the same as the bundle.
3. **Global hotkeys:** `NSEvent.addGlobalMonitorForEventsMatchingMask_handler_` on key-down, check Control+Option and keyCode 2 (D) or 15 (R). This needs one Accessibility grant to the app. Fallback if the grant does not stick: Carbon `RegisterEventHotKey` through ctypes, which needs no permission.
4. **Concealed pasteboard:** `NSPasteboard.generalPasteboard()`, declare `NSPasteboardTypeString` plus `org.nspasteboard.ConcealedType` with an empty string. Record `changeCount()`. A 120 s timer clears the pasteboard only if the count is unchanged.
5. **Keychain:** `SecItemAdd` / `SecItemCopyMatching`, `kSecClassGenericPassword`, service `com.mrinoy.localdeid`, `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`, synchronizable off. About 25 lines. Skip the `keyring` package.
6. **AES-GCM:** `cryptography` (Apache-2.0 or BSD), `AESGCM` with 12-byte random nonces.

## 2. Process model

1. `subprocess.Popen([sys.executable, "-m", "localdeid.worker", models_dir], stdin=PIPE, stdout=PIPE)`. One JSON object per line each way. Not `multiprocessing`: a plain interpreter path survives any packaging choice.
2. Request: `{id, op: "deid"|"ping"|"selftest", text, settings, keep_keys, manual_hides, ref, rev}`. Reply: `{id, rev, ok, result | error_code}`. Settings travel with each request, so the worker holds only the model.
3. **Worker caches model spans** keyed by sha256 of the text. Every un-hide calls `deidentify` again (`pipeline.py:972`), which would re-run the model on each click. With the cache, re-runs are rules-only and fast.
4. **One session thread** in the UI process consumes a `queue.Queue` of closures. Bridge methods enqueue and wait on a Future with a timeout. Nothing else touches sessions.
5. **Stale dropping:** each session has an integer `rev` that increments on every text or decision change. A reply whose `rev` is not the session's current `rev` is discarded.
6. **Crash and cancel:** if `poll()` is not None or the pipe breaks, respawn, resend the current request once, and show "Restarting checks… your note is safe". Cancel is kill plus respawn. Add a 10-minute idle kill to free the 1.4 GB on an 8 GB Air; the next paste pays one cold load.

## 3. Vault

1. Location: `~/Library/Application Support/LocalDeID/vault/sessions/<ref>-<uuid>.ldv`. One file per session. Not under iCloud Drive. Set `NSURLIsExcludedFromBackupKey` on the vault directory through `NSURL.setResourceValue_forKey_error_` once at creation.
2. File layout: a plaintext JSON header (`format`, `ref`, `state`, `created`, `updated`, `pipeline_version`, `nonce_key`, `wrapped_key`, `nonce_meta`, `nonce_body`), then `meta` ciphertext (child display name, item counts) and `body` ciphertext (the full Result dict, decisions, copied blocks). Both encrypted with the session key, the header as AAD. The History list decrypts only `meta`.
3. Keys: a 32-byte master key in Keychain. Per session, a random 32-byte key wrapped with the master key. Deleting the file deletes the wrapped key. "Delete everything" removes the directory and the Keychain item.
4. Writes: write to `tmp/<name>.tmp` in the same directory, `fsync`, `os.replace`. Save after every state change, including every Copy, because `copy_block` mutates `copied_contexts` (`restore.py:74-77`) and restore needs it (`restore.py:124`).
5. Expiry: on launch and hourly, read headers only. Warn at day 27 in History. Delete at day 30 unless `state == "waiting"`, which instead shows a warning row that needs Done.
6. Check new refs against existing vault refs. `new_ref` (`pipeline.py:968`) draws from about one million values, and a duplicate breaks the `wrong-ref` logic.

## 4. Network blocking and verification

1. `netguard.py` is the first import in both `app/__main__.py` and `worker.py`. It replaces `socket.socket`, `socket.create_connection`, and `socket.getaddrinfo` with functions that raise, and sets `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `ORT_DISABLE_TELEMETRY=1`.
2. pywebview must run with `http_server=False` (the default) and a `file://` URL. Its built-in server would open a listening socket, which breaks both the guard and the offline test. Set `webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False` and `ALLOW_DOWNLOADS = False`. Keep `private_mode` on. Put the CSP meta tag in `index.html`.
3. Offline test (`tests/test_offline.py`, marked `offline`, run by hand on both Macs): launch `python -m localdeid.app --selftest-exit`, which pastes a sample note through the worker and quits after 20 s. Meanwhile, every 0.5 s, collect pids with `pgrep -P` recursion plus any `com.apple.WebKit.*` process started after the launch time (`ps -o pid,lstart,comm`), and run `lsof -nP -i -a -p <pids>`. Assert empty. Also capture `nettop -x -P -L 3` and assert zero bytes for those names.
4. Optional hard guarantee for the worker: launch it under `sandbox-exec -p '(version 1)(allow default)(deny network*)'`. Verify it on macOS 26 first. The tool is deprecated but still present. I could not run the check here because the command was not approved.

## 5. Packaging

1. **No py2app in v1.** MLX's metallib and onnxruntime dylibs make py2app fragile, and the worker needs a real interpreter path.
2. Thin bundle: `LocalDeID.app/Contents/Info.plist` (`CFBundleIdentifier com.mrinoy.localdeid`, `LSUIElement true`), and `Contents/MacOS/LocalDeID` as a zsh script that execs `~/Library/Application Support/LocalDeID/app/.venv/bin/python -m localdeid.app`. Launched through LaunchServices, the python child is attributed to the app for Accessibility.
3. Signing: create one "LocalDeID Dev" code-signing certificate in Keychain Access on the Air, export the `.p12` to the mini as a backup. `codesign --force --sign "LocalDeID Dev" LocalDeID.app`. No quarantine attribute arrives through `rsync` or `scp`, so Gatekeeper stays quiet.
4. Install on the Air: install uv, pin Python in `.python-version`, `git clone mini:Codex/Projects/Local-DeID` into the app directory, `uv sync --no-dev --extra mlx` (or `onnx`), then `rsync -av mini:Codex/Projects/Local-DeID/models/ .../app/models/`, then `localdeid selftest` to verify hashes. Do not `uv pip install` the fetch tooling on the Air. `requests`, `httpx`, and `huggingface_hub` are in the mini venv but not in `pyproject.toml`, so `uv sync` leaves them out. Keep it that way.
5. Add the app to Login Items by hand.

## 6. Modules and tests

```
src/localdeid/app/
  __main__.py   netguard first, then window, status item, hotkeys, worker
  netguard.py   socket guard + offline env
  worker.py     model process: JSON lines, PF span cache
  engine.py     UI-side client: spawn, restart, kill, rev check, idle kill
  session.py    Session dataclass + the one serialized queue
  serialize.py  Result/Item/Instance/Protected <-> dict
  vault.py      Keychain master key, AES-GCM files, list, expire, delete-all
  clipboard.py  concealed write, read, 120 s clear
  hotkeys.py    NSEvent global monitor
  statusitem.py NSStatusItem menu: Open, De-identify, Restore, Quit
  api.py        JS bridge: validated methods, never raises to JS
  selftest.py   pf.artifacts.verify, socket guard check, sample note
  ui/index.html, app.js, app.css   vanilla JS, no build step
```

Tests:

- **Unit:** vault round trip, tamper fails auth, atomic write with an injected exception mid-write, expiry with an injected clock, serialize round trip over `deidentify` outputs, `engine.py` against a fake worker (crash restart, stale rev dropped, queue order), `netguard` makes `socket.socket()` raise.
- **Restore:** the existing Hypothesis round trip and fuzz (`tests/test_pipeline_restore.py:135`, `:216`) plus one test that restores from a vault-loaded Result and one that fuzzes drafts across two sessions.
- **Bridge:** a Hypothesis test that random JSON into every `api.py` method returns an error dict and never raises.
- **Offline:** section 4.
- **UI smoke:** an `--smoke script.json` mode. The app runs the steps through `window.evaluate_js` (set textarea, click Copy, dispatch KeyboardEvents for U, H, ⌘Z), asserts DOM text, and screenshots with `screencapture -l <NSWindow.windowNumber()>` from `window.native`. Exit code is the result. No Playwright.

## 7. Cut or defer, and headless risks

1. **Click-drag to hide (5.2):** replace with "select text, press H" using `window.getSelection()`. Same `manual_hides` backend, and it is drivable from `evaluate_js`.
2. **"AI additions" highlighting in Restore:** defer. Ship green restored values from `Restored.spans` (`restore.py:101`). Side by side is two `<pre>` panes.
3. **First run:** one page with the three texts, not a wizard.
4. **Caseload growth from Check ("Add Sofía's sister"):** defer. Ship the one-name-per-line box.
5. **Memory-pressure unload (4.7):** replace with the 10-minute idle kill.
6. **History:** today only, three states. No search.
7. **Hard to verify headless:** VoiceOver labels, zoom to 200%, narrow layout, and the Accessibility prompt. Put ARIA labels in the HTML and check them with `evaluate_js`. Zoom and narrow layout get screenshots at two window sizes. Test the hotkey prompt once on the Air by hand.
8. **Keep:** fingerprint restore with confirmation, because `best_session` already exists (`restore.py:277`). The UI only needs one confirm dialog with the child's name large.

Defects to fix before the app uses the engine:

- `cli.py:21-22` resolves the models directory from the package path. Pass it explicitly to the worker and read it from the app directory.
- `cli.py:62-69` runs `tools/fetch_models.py` for the self-test, which imports `huggingface_hub`. The app self-test must call `pf.artifacts.verify` directly.
- `Result` is not JSON-serializable, and `Protected` is a frozen dataclass (`protect.py:19`), so `serialize.py` needs explicit `from_dict` reconstruction.

Build order: serialize and worker with CLI parity, then vault, then window with paste, Check, and Copy, then Restore and History, then status item, hotkeys, and clipboard, then Caseload, Settings, and self-test, then the bundle and the offline test, then `/cr`.
