"""Start the menu-bar app (SPEC 4.1, 5.1).

    python -m localdeid.app                     # the app
    python -m localdeid.app --selftest-exit     # self-test through the worker, print JSON, exit
    python -m localdeid.app --smoke             # scripted UI run in a temporary folder (never real data)

Environment: LOCALDEID_HOME (data folder, default ~/Library/Application Support/LocalDeID),
LOCALDEID_MODELS (model folder, default <repo>/models), LOCALDEID_TEST_KEYSTORE=1 (in-memory key; tests only).
"""

from __future__ import annotations

from . import netguard  # noqa: F401  (first: blocks the network)

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UI = Path(__file__).resolve().parent / "ui" / "index.html"
HOTKEY_D, HOTKEY_R = 2, 15  # macOS key codes for D and R
QUITTING = threading.Event()  # set by our Quit item: closing then means quit, not hide


def home() -> Path:
    return Path(os.environ.get("LOCALDEID_HOME", Path.home() / "Library" / "Application Support" / "LocalDeID"))


def models() -> Path:
    return Path(os.environ.get("LOCALDEID_MODELS", ROOT / "models"))


def build(*, model: bool = True):
    from .api import Api
    from .clipboard import MacClipboard
    from .engine import EngineClient
    from .session import Manager
    from .vault import KeychainKeyStore, MemoryKeyStore, Vault, VaultError

    keystore = MemoryKeyStore() if os.environ.get("LOCALDEID_TEST_KEYSTORE") == "1" else KeychainKeyStore()
    vault = Vault(home() / "vault", keystore)
    clipboard = MacClipboard()
    holder: dict = {}
    # GLiNER does not ship in v1 (DECISIONS 26): the engine runs the Privacy Filter and rules.
    engine = EngineClient(models(), gliner=False, model=model)
    manager = Manager(engine, vault, clipboard=clipboard, settings_loader=lambda: holder["api"]._engine_settings())
    api = Api(manager, vault, clipboard=clipboard, engine=engine)
    holder["api"] = api
    try:
        vault.expire()
    except VaultError:
        pass  # a missing key: the sessions cannot be listed, and the window says so
    return api, manager, engine, vault


# --------------------------------------------------------------------------- macOS shell


def _install_shell(window, api):
    """Status item, hotkeys, and the Dock-less app policy. Runs on the main thread.
    Returns (hotkeys, key codes that fell back to the NSEvent monitor)."""
    import objc
    from AppKit import (NSApp, NSApplicationActivationPolicyAccessory, NSEvent, NSEventMaskKeyDown,
                        NSEventModifierFlagCommand, NSEventModifierFlagControl, NSEventModifierFlagOption,
                        NSEventModifierFlagShift, NSMenu, NSMenuItem, NSStatusBar, NSVariableStatusItemLength)
    from Foundation import NSObject
    from PyObjCTools import AppHelper

    NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    # The hotkey and menu work runs on a background thread: window.show and
    # window.evaluate_js hop to the main thread themselves and wait for it, so
    # calling them on the main thread would deadlock. Only AppKit calls hop here.
    def bring_front():
        window.show()

    def in_background(fn, then):
        threading.Thread(target=lambda: then(fn()), daemon=True).start()

    def flash(text: str) -> None:
        def set_title():
            item.button().setTitle_(text)
            AppHelper.callLater(5.0, lambda: item.button().setTitle_("DeID"))
        AppHelper.callAfter(set_title)

    def show_check(result):
        out, gen = result
        if not out.get("ok"):
            bring_front()
            window.evaluate_js(f"LocalDeID.notice({json.dumps(out.get('message', ''))}, true)")
        elif out.get("copied"):
            flash(out["notice"])
        else:
            bring_front()
            window.evaluate_js(f"LocalDeID.showCheck({json.dumps(out['view'])}, {json.dumps(gen)})")

    def show_restore(result):
        out, draft, seq = result
        bring_front()
        if out.get("ok"):
            window.evaluate_js(f"LocalDeID.showRestore({json.dumps(out['restore'])}, {json.dumps(draft)}, "
                               f"{json.dumps(seq)})")
        else:
            window.evaluate_js(f"LocalDeID.notice({json.dumps(out.get('message', ''))}, true)")

    def deidentify():
        def run():
            try:  # the opening's number is taken now, before the checks run
                gen = window.evaluate_js("LocalDeID.beginOpen()")
            except Exception:  # noqa: BLE001 (the page is not ready: showCheck takes a number then)
                gen = None
            return api.hotkey_deidentify(), gen if isinstance(gen, int) else None
        in_background(run, show_check)

    def restore():
        def run():
            try:  # the restore's number is taken now, so a newer manual restore wins
                seq = window.evaluate_js("LocalDeID.beginRestore()")
            except Exception:  # noqa: BLE001
                seq = None
            draft = api._clipboard_text()
            return api.restore(draft), draft, seq if isinstance(seq, int) else None
        in_background(run, show_restore)

    class Target(NSObject):
        def open_(self, _):
            threading.Thread(target=bring_front, daemon=True).start()

        def deid_(self, _):
            deidentify()

        def restore_(self, _):
            restore()

        def quit_(self, _):
            QUITTING.set()
            NSApp.terminate_(None)

    target = Target.alloc().init()
    item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    item.button().setTitle_("DeID")
    item.button().setToolTip_("Local DeID")
    menu = NSMenu.alloc().init()
    for title, sel, key in (("Open Local DeID", "open:", ""), ("De-identify clipboard  ⌃⌥D", "deid:", ""),
                            ("Restore clipboard draft  ⌃⌥R", "restore:", ""), (None, None, None),
                            ("Quit Local DeID", "quit:", "q")):
        if title is None:
            menu.addItem_(NSMenuItem.separatorItem())
            continue
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, key)
        mi.setTarget_(target)
        menu.addItem_(mi)
    item.setMenu_(menu)

    from .hotkeys import HotKeys

    actions = {HOTKEY_D: deidentify, HOTKEY_R: restore}
    try:
        hotkeys = HotKeys()
        failed = hotkeys.register(actions)
    except OSError:
        hotkeys, failed = None, list(actions)
    if failed:
        # Another app owns the key: an NSEvent monitor still sees it (needs the Accessibility grant).
        wanted = NSEventModifierFlagControl | NSEventModifierFlagOption
        mask = wanted | NSEventModifierFlagCommand | NSEventModifierFlagShift

        def on_key(event):
            if (event.modifierFlags() & mask) == wanted and event.keyCode() in failed:
                actions[event.keyCode()]()
                return None
            return event

        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, lambda e: (on_key(e), None)[1])
        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, on_key)
    _install_shell.keep = (target, item, menu, hotkeys)  # keep the Objective-C objects alive
    objc  # noqa: B018 (imported for its side effect of loading the bridge)
    return hotkeys, failed


def _single_instance():
    """A second launch exits: one status item, one owner of the hotkeys."""
    import fcntl

    home().mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(home() / "app.lock", "w")  # noqa: SIM115 (held for the life of the process)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def _expire_hourly(manager, stop: threading.Event) -> None:
    while not stop.wait(3600):
        try:
            manager.expire()
        except Exception:  # noqa: BLE001 (the next hour tries again)
            pass


def run_app(args) -> int:
    import webview

    lock = _single_instance()
    if lock is None:
        print("Local DeID is already running.")
        return 0
    api, manager, engine, vault = build()
    stop = threading.Event()
    threading.Thread(target=_expire_hourly, args=(manager, stop), daemon=True).start()
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    window = webview.create_window("Local DeID", url=UI.as_uri(), js_api=api, width=1100, height=760,
                                   min_size=(520, 420), text_select=True)

    def on_closing():
        # pywebview sends the close button and app termination (Quit, logout,
        # shutdown) through this one event. Only the user closing the window
        # hides it, so the app stays in the menu bar; anything else quits.
        if QUITTING.is_set() or not _user_closed_window(window):
            shutdown()  # quitting: the 2-minute timer dies with the app, so clear now (SPEC 4.6)
            return True
        window.hide()
        return False

    window.events.closing += on_closing
    done = threading.Event()

    def shutdown() -> None:
        """No new work, the engine stopped, the queue drained, then the clipboard cleared
        last, so no queued copy can land after the clear. Runs once."""
        if done.is_set():
            return
        done.set()
        stop.set()
        manager.close(timeout=0)  # take no new work
        engine.close()  # an engine call in flight ends at once
        manager.close(timeout=10)  # let queued work finish
        api._clipboard.clear_now()

    _protect_window(window, api)

    def started():
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_install_shell, window, api)
        AppHelper.callAfter(_no_drops, window)
        threading.Thread(target=lambda: _warm(engine), daemon=True).start()

    try:
        webview.start(started, private_mode=True, http_server=False)
    finally:
        shutdown()
        lock.close()
    return 0


def _protect_window(window, api) -> None:
    """Only the bundled page may use the bridge, and the window never stays on another
    page. Drag-and-drop, the one way to open another page here, is also switched off
    (`_no_drops`, once the window exists). The guard runs on bridge threads only."""
    ui_url = UI.as_uri()
    api._set_guard(lambda: (window.get_current_url() or "").split("#")[0] == ui_url)

    def on_loaded():
        if (window.get_current_url() or "").split("#")[0] != ui_url:
            window.load_url(ui_url)

    window.events.loaded += on_loaded


def _webview_of(window):
    from WebKit import WKWebView

    def find(view):
        if isinstance(view, WKWebView):
            return view
        for sub in view.subviews():
            hit = find(sub)
            if hit is not None:
                return hit
        return None

    return find(window.native.contentView())


def _no_drops(window) -> None:
    """A link dropped on the window would load a web page: the web view takes no drops."""
    web = _webview_of(window)
    if web is not None:
        web.unregisterDraggedTypes()


def _user_closed_window(window) -> bool:
    """True when the current event is a click in our window (the close button)
    or ⌘W; False for Quit, ⌘Q, logout, and shutdown."""
    from AppKit import (NSApp, NSEventModifierFlagCommand, NSEventTypeKeyDown, NSEventTypeLeftMouseDown,
                        NSEventTypeLeftMouseUp)

    ev = NSApp.currentEvent()
    if ev is None:
        return False
    if ev.type() in (NSEventTypeLeftMouseDown, NSEventTypeLeftMouseUp):
        return ev.window() is not None and ev.window() == window.native
    if ev.type() == NSEventTypeKeyDown and ev.modifierFlags() & NSEventModifierFlagCommand:
        return (ev.charactersIgnoringModifiers() or "").lower() == "w"
    return False


def _warm(engine) -> None:
    try:
        engine.call("ping")
    except Exception:  # noqa: BLE001 (the first real call reports any problem)
        pass


def run_selftest(args) -> int:
    api, manager, engine, _ = build(model=not args.no_model)
    try:
        out = api.selftest()
        print(json.dumps(out, indent=1))
        return 0 if out.get("ok") and out["selftest"]["ok"] else 1
    finally:
        manager.close()
        engine.close()


SMOKE_NOTE = "Rosa Díaz said 4/5 words today. Rosa will practice at home."


def _on_main(fn, timeout: float = 30.0):
    """Run `fn` on the main thread and return its result (smoke test)."""
    from PyObjCTools import AppHelper

    box, done = [], threading.Event()

    def run():
        try:
            box.append(fn())
        finally:
            done.set()

    AppHelper.callAfter(run)
    done.wait(timeout)
    return box[0] if box else None


def _status_text(window) -> str:
    try:
        return window.evaluate_js("document.getElementById('notice').textContent + ' | ' + "
                                  "document.getElementById('engine').textContent") or ""
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


def run_smoke(args) -> int:
    """Open the window, run the sample note through Paste → Check → Copy → Restore with
    the real UI and engine, check the DOM, and save screenshots. Exit code = result."""
    import shutil
    import tempfile

    import webview

    # The smoke run clicks Delete everything: it must never touch real data, so it always
    # gets a fresh folder and an in-memory key, whatever the environment says.
    scratch = tempfile.mkdtemp(prefix="localdeid-smoke-")
    os.environ["LOCALDEID_HOME"] = scratch
    os.environ["LOCALDEID_TEST_KEYSTORE"] = "1"
    api, manager, engine, vault = build(model=not args.no_model)
    window = webview.create_window("Local DeID", url=UI.as_uri(), js_api=api, width=args.width, height=760,
                                   text_select=True)
    _protect_window(window, api)
    result = {"ok": False, "steps": []}
    shots = Path(args.out)
    shots.mkdir(parents=True, exist_ok=True)

    def step(name, ok, detail=""):
        result["steps"].append({"name": name, "ok": bool(ok), "detail": detail})

    def shot(name):
        """WKWebView's own snapshot: no screen-recording permission needed."""
        from AppKit import NSBitmapImageRep, NSPNGFileType
        from PyObjCTools import AppHelper
        from WebKit import WKWebView

        done = threading.Event()

        def find(view):
            if isinstance(view, WKWebView):
                return view
            for sub in view.subviews():
                hit = find(sub)
                if hit is not None:
                    return hit
            return None

        def save(image, error):
            try:
                if image is not None:
                    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(
                        str(shots / f"{name}.png"), True)
            finally:
                done.set()

        def take():
            web = find(window.native.contentView())
            if web is None:
                done.set()
                return
            web.takeSnapshotWithConfiguration_completionHandler_(None, save)

        AppHelper.callAfter(take)
        done.wait(20)
        step(f"screenshot {name}", (shots / f"{name}.png").exists())

    def wait_js(expr, timeout=180.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = window.evaluate_js(expr)
                if v:
                    return v
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        return None

    def script():
        try:
            step("bridge ready", wait_js("!!(window.pywebview && window.pywebview.api)", 60))
            _on_main(lambda: _no_drops(window))
            step("guard accepts the bundled page", api.state().get("ok") is True)
            step("web view takes no drops", _on_main(lambda: len(_webview_of(window).registeredDraggedTypes())) == 0)
            step("first-run card shown", wait_js("!document.getElementById('firstrun').hidden", 20))
            shot("1-welcome")
            window.evaluate_js("document.getElementById('trysample').click()")
            hidden = wait_js("getComputedStyle(document.getElementById('firstrun')).display === 'none'", 10)
            end = time.time() + 10
            while not api.get_settings()["settings"].get("first_run_done") and time.time() < end:
                time.sleep(0.2)
            step("first run recorded", hidden and api.get_settings()["settings"].get("first_run_done") is True)
            window.evaluate_js("document.getElementById('run').click()")
            ok = wait_js("!document.getElementById('check').hidden && document.querySelectorAll('.chip').length > 0")
            step("check view shows chips", ok)
            visible = window.evaluate_js("document.getElementById('text').textContent")
            step("child shown large", window.evaluate_js("document.getElementById('child').textContent") == "Mateo R.")
            step("chips have labels", window.evaluate_js(
                "Array.from(document.querySelectorAll('.chip')).every(c => c.getAttribute('aria-label'))"))
            step("note text intact in Check", "produced /k/ in 8/10 trials with mod cues" in (visible or ""))
            shot("2-check")
            # ⌘C with a selection in Check copies the tagged note, never the selected real values
            api._clipboard.write("smoke sentinel")
            window.evaluate_js("(() => { const r = document.createRange(); r.selectNodeContents(document.getElementById('text'));"
                               "const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
                               "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'c', metaKey: true})); })()")
            end = time.time() + 60
            while time.time() < end and not (api._clipboard.read() or "").startswith("ref: "):
                time.sleep(0.3)
            got = api._clipboard.read() or ""
            step("⌘C in Check copies the tagged note", got.startswith("ref: ") and "Mateo" not in got)
            # a selection that starts outside Check and runs into the note: no native copy
            blocked = window.evaluate_js(
                "(() => { const r = document.createRange(); r.setStartBefore(document.querySelector('nav'));"
                "r.setEndAfter(document.getElementById('text')); const s = window.getSelection();"
                "s.removeAllRanges(); s.addRange(r); const e = new ClipboardEvent('copy', {cancelable: true});"
                "document.dispatchEvent(e); s.removeAllRanges(); return e.defaultPrevented; })()")
            step("native copy of a selection into the note is blocked", blocked is True)
            window.evaluate_js("document.getElementById('copy').click()")
            ok = wait_js("!document.getElementById('notice').hidden && "
                         "document.getElementById('notice').textContent.startsWith('Copied')")
            step("copy notice", ok)
            copied = api._clipboard.read()
            step("clipboard is de-identified", copied.startswith("ref: ") and "Mateo" not in copied
                 and "555-0142" not in copied and "/k/" in copied)
            window.evaluate_js(f"document.getElementById('draft').value = {json.dumps(copied)};"
                               "document.querySelector('nav button[data-view=restore]').click();"
                               "document.getElementById('dorestore').click()")
            ok = wait_js("!document.getElementById('restoreactions').hidden")
            step("restore view ready", ok)
            restored = window.evaluate_js("document.getElementById('restored').textContent")
            step("restored equals the note", restored == api._m.get(api._m.history()[0].id).raw)
            shot("3-restore")
            hk = _on_main(lambda: _install_shell(window, api))
            step("hotkeys registered", hk is not None and hk[0] is not None and not hk[1], str(hk and hk[1]))
            api._clipboard.write(SMOKE_NOTE)
            sim = _on_main(lambda: hk[0].simulate(HOTKEY_D))
            end = time.time() + 180
            clicked = False
            while time.time() < end and api._clipboard.read() == SMOKE_NOTE:
                if not clicked and wait_js("!document.getElementById('check').hidden && "
                                           "document.getElementById('text').textContent.includes('4/5 words')", 0.5):
                    step("⌃⌥D with unsure items opens Check", True)
                    window.evaluate_js("document.getElementById('copy').click()")
                    clicked = True
                time.sleep(0.5)
            out = api._clipboard.read()
            step("⌃⌥D copies the de-identified note back", out.startswith("ref: ") and "Rosa" not in out
                 and "4/5" in out, f"sim={sim} clicked={clicked} clipboard={out[:40]!r} status={_status_text(window)!r}")
            draft = out.replace("Tags named", "The AI wrote this. Tags named")
            api._clipboard.write(draft)
            _on_main(lambda: hk[0].simulate(HOTKEY_R))
            ok = wait_js("!document.getElementById('restore').hidden && "
                         "document.getElementById('restored').textContent.includes('Rosa')", 60)
            step("⌃⌥R restores the draft in the window", ok)
            shot("5-hotkey-restore")
            window.evaluate_js("document.querySelector('nav button[data-view=history]').click()")
            # SPEC 4.3: keeping a caseload name asks first; "Keep it hidden" leaves it hidden.
            api.set_caseload("Rosa Díaz")
            window.evaluate_js("document.querySelector('nav button[data-view=home]').click();"
                               "document.getElementById('paste').value = 'Rosa Díaz said 4/5 words.';"
                               "document.getElementById('run').click()")
            row = ("Array.from(document.querySelectorAll('#rows li'))"
                   ".find(li => li.querySelector('.value').textContent === 'Rosa')")
            ok = wait_js(f"!document.getElementById('check').hidden && !!{row}", 120)
            step("caseload note in Check", ok)
            window.evaluate_js(f"{row}.querySelector('button').click()")
            step("keeping a caseload name asks first", wait_js("!document.getElementById('askbox').hidden", 10))
            shot("6-confirm")
            window.evaluate_js("document.getElementById('ask-no').click()")
            step("answer no keeps it hidden", wait_js(
                f"document.getElementById('askbox').hidden && {row}.getAttribute('aria-label').includes('hidden')",
                10))
            step("hidden elements are not drawn", window.evaluate_js(
                "Array.from(document.querySelectorAll('[hidden]')).every(e => getComputedStyle(e).display === 'none')"))
            step("history lists both sessions", wait_js("document.querySelectorAll('#hist tr').length >= 2", 20))
            shot("4-history")
            # Delete everything leaves no client text on any screen
            window.evaluate_js("document.querySelector('nav button[data-view=settings]').click();"
                               "document.getElementById('deleteconfirm').value = 'DELETE';"
                               "document.getElementById('deleteall').click()")
            ok = wait_js("document.getElementById('notice').textContent === 'Everything was deleted.'", 30)
            empty = window.evaluate_js(
                "['text','rows','src','restored'].every(id => document.getElementById(id).textContent === '') && "
                "['paste','draft','kids'].every(id => document.getElementById(id).value === '')")
            step("delete everything clears every screen", ok and empty and api._m.history() == [])
            # Save waits for the saved values: the caseload button turns on only after they load
            window.evaluate_js("document.querySelector('nav button[data-view=caseload]').click()")
            step("caseload Save turns on after loading",
                 wait_js("!document.getElementById('savekids').disabled", 20))
            result["ok"] = all(s["ok"] for s in result["steps"])
        except Exception as exc:  # noqa: BLE001
            import traceback

            where = traceback.extract_tb(exc.__traceback__)[-1]
            step("script", False, f"{type(exc).__name__} at {Path(where.filename).name}:{where.lineno}")
        finally:
            api._clipboard.clear_now()
            window.destroy()

    webview.start(script, private_mode=True, http_server=False)
    manager.close()
    engine.close()
    shutil.rmtree(scratch, ignore_errors=True)
    print(json.dumps(result, indent=1))
    return 0 if result["ok"] else 1


MEMORY_GATE_MIB = 2384  # SPEC 3: whole app < 2.5 GB
WORKLOADS = ("note_en_500.txt", "note_es_500.txt", "eval_es_5000.txt", "eval_mixed_5000.txt")
# Untimed first run so every timed workload is warm. It shares no text with the
# workloads, so the worker's detection cache cannot serve them.
WARMUP_TEXT = "Warm-up note: Alex Rivera met the therapist in Durham on March 3."


def run_memory(args) -> int:
    """SPEC 3 whole-app memory: after an untimed warm-up, the SPEC workloads (500-word
    notes, 5,000-word evaluations) go through the window (paste, Check, Copy) with the
    model, each timed warm, then the
    lifetime peak footprint of the app, its worker, and its WebKit content process is
    read from the kernel (no sampling). Any missing measurement fails the run.
    Runs in a temporary folder with an in-memory key, like --smoke."""
    import shutil
    import tempfile

    import webview

    from ..sysmem import meminfo, peak_footprint

    scratch = tempfile.mkdtemp(prefix="localdeid-memory-")
    os.environ["LOCALDEID_HOME"] = scratch
    os.environ["LOCALDEID_TEST_KEYSTORE"] = "1"
    api, manager, engine, vault = build(model=True)
    window = webview.create_window("Local DeID", url=UI.as_uri(), js_api=api, width=1100, height=760)
    _protect_window(window, api)
    inputs = ROOT / "harness" / "inputs"
    result: dict = {"ok": False, "workloads": [], "peak_mib": {}, "gate_mib": MEMORY_GATE_MIB}

    def wait_js(expr, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if window.evaluate_js(expr):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        return False

    def script():
        try:
            wait_js("!!(window.pywebview && window.pywebview.api)", 60)
            window.evaluate_js("document.getElementById('firstrun').hidden = true")
            for name in ("warmup", *WORKLOADS):
                text = WARMUP_TEXT if name == "warmup" else (inputs / name).read_text(encoding="utf-8")
                prev = window.evaluate_js("S.view ? S.view.id : ''") or ""
                t0 = time.time()
                window.evaluate_js("document.querySelector('nav button[data-view=home]').click();"
                                   f"document.getElementById('paste').value = {json.dumps(text)};"
                                   "document.getElementById('run').click()")
                # this workload's own session on screen, not the previous one
                shown = wait_js(f"S.view && S.view.id !== {json.dumps(prev)} && "
                                "!document.getElementById('check').hidden", 600)
                ref = window.evaluate_js("S.view ? S.view.ref : ''") or ""
                window.evaluate_js("document.getElementById('notice').textContent = '';"
                                   "document.getElementById('copy').click()")
                copied = bool(ref) and wait_js(
                    f"document.getElementById('notice').textContent.includes('ref ' + {json.dumps(ref)})", 120)
                run = {"name": name, "words": len(text.split()), "shown": shown,
                       "copied": copied, "seconds": round(time.time() - t0, 1)}
                if name == "warmup":
                    result["warmup"] = run
                else:
                    result["workloads"].append(run)
            web = _on_main(lambda: _webview_of(window))
            web_pid = _on_main(lambda: int(web._webProcessIdentifier())) if web is not None else None
            worker_pid = engine._proc.pid if engine._proc is not None else None
            result["worker_restarts"] = engine.restarts
            if engine.restarts:  # an earlier worker's peak is gone: the run cannot vouch for the gate
                result["error"] = "worker-restarted"
            peaks = {"app": meminfo().footprint_peak,
                     "worker": peak_footprint(worker_pid) if worker_pid else None,
                     "webkit_content": peak_footprint(web_pid) if web_pid else None}
            missing = [k for k, v in peaks.items() if not v]
            result["peak_mib"] = {k: (v >> 20) if v else None for k, v in peaks.items()}
            total = sum(v for v in peaks.values() if v) >> 20
            result["peak_mib"]["sum"] = total
            result["missing"] = missing
            result["not_counted"] = "WebKit GPU and Networking helpers (shared XPC services)"
            result["ok"] = (not missing and not engine.restarts
                            and all(w["shown"] and w["copied"] for w in result["workloads"])
                            and total < MEMORY_GATE_MIB)
        except Exception as exc:  # noqa: BLE001
            result["error"] = type(exc).__name__
        finally:
            api._clipboard.clear_now()
            window.destroy()

    webview.start(script, private_mode=True, http_server=False)
    manager.close()
    engine.close()
    shutil.rmtree(scratch, ignore_errors=True)
    print(json.dumps(result, indent=1))
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="localdeid.app")
    ap.add_argument("--selftest-exit", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--memory-run", action="store_true", help="SPEC 3 whole-app memory with the SPEC workloads")
    ap.add_argument("--no-model", action="store_true", help="tests: rules only, no model files")
    ap.add_argument("--out", default="harness/out/smoke")
    ap.add_argument("--width", type=int, default=1100)
    args = ap.parse_args(argv)
    if args.selftest_exit:
        return run_selftest(args)
    if args.smoke:
        return run_smoke(args)
    if args.memory_run:
        return run_memory(args)
    return run_app(args)


if __name__ == "__main__":
    sys.exit(main())
