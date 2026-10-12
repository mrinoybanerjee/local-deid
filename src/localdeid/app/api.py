"""The JS bridge (SPEC 4.1): a small, validated set of calls.

Every method checks its arguments, runs through the session manager's single
queue, and returns a dict: {"ok": true, ...} or {"ok": false, "error": code,
"message": plain words}. Nothing raises into the page. Messages name a next
step (SPEC 5.3) and never contain note text.
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import threading
from dataclasses import asdict
from typing import Any, Callable

from . import documents
from .caseload import CaseloadError, parse
from .engine import EngineError
from .session import Manager, SessionError, view
from .serialize import settings_to_dict
from .vault import VaultError

DEFAULT_SETTINGS = {
    "keep_names": [], "employer_names": [], "template_en": "", "template_es": "",
    "ai_tip": "Use a Temporary Chat in ChatGPT or Claude.", "first_run_done": False,
}
_SID = re.compile(r"^[0-9a-f]{32}$")
MESSAGES = {
    "empty-text": "The clipboard or paste box is empty. Copy the note, then try again.",
    "text-too-long": "This text is longer than any note the app was built for. Split it and try again.",
    "blocked": "Copy is blocked until the flagged words are hidden or kept. See the list on the right.",
    "not-ready": "The checks are still running. Try again in a moment.",
    "unknown-item": "That item changed. Look at the list again.",
    "bad-span": "Select the words to hide in the note text, then press H.",
    "no-session": "No session matches this draft's ref line. Paste a draft made from a note you checked here.",
    "worker-exited": "Restarting checks… your note is safe. Try again.",
    "timeout": "The checks took too long and were restarted. Your note is safe. Try again.",
    "session-damaged": "This session's file is damaged and cannot be opened. Delete it from History.",
    "no-such-session": "That session no longer exists.",
    "draft-expired": "That question is old. Copy the draft and restore it again.",
    "delete-failed": "Delete everything did not finish: some saved files may already be gone, and others "
                     "are still here. Restart the Mac, then use Delete everything again.",
    "restore-changed": "That restore is no longer saved. Restore the draft again, then copy it.",
    "which-copy": "This note was copied more than once with different tags. Restore the draft with its ref line, "
                  "or pick the copy in the list.",
    "settings-damaged": "Your saved settings cannot be read, so the checks are stopped. To start again, use "
                        "Settings > Delete everything.",
    "caseload-damaged": "Your saved caseload cannot be read, so the checks are stopped. To start again, use "
                        "Settings > Delete everything.",
    "bad-request": "Something went wrong in the app. Try again.",
    "doc-unsupported": "This kind of file cannot be read. Use a Word (.docx or .doc), PDF, RTF, or text file.",
    "doc-too-large": "This file is larger than 25 MB. Save a shorter copy, or copy the text and paste it.",
    "doc-empty": "This document has no text in it.",
    "doc-unreadable": "This file cannot be read. Open it, save it again as Word or PDF, and try again. "
                      "Or copy the text and paste it.",
    "doc-locked": "This PDF is protected by a password. Open it, copy the text, and paste it.",
    "doc-scanned": "This PDF is a scan (a picture of the pages), so it has no text to check. Print it to PDF "
                   "from the EMR, or copy the text and paste it.",
    "doc-embedded": "This Word file holds another document or an embedded table, which the app cannot read. "
                    "Copy the text and paste it.",
    "doc-equation": "This Word file has an equation the app cannot read. Copy the text and paste it.",
    "doc-incomplete": "Part of this Word file cannot be read, so it was not opened. Copy the text and paste it.",
    "deleted-meanwhile": "Delete everything ran while the document was being read, so it was not opened.",
    "doc-form": "This PDF form has a list with several choices, which the app cannot read. Print it to PDF "
                "(File > Print > Save as PDF), or copy the text and paste it.",
    "doc-numbering": "This Word file uses a kind of list numbering the app cannot read. Copy the text and "
                     "paste it.",
    "doc-form-word": "This Word form has a drop-down list the app cannot read. Copy the text and paste it.",
    "doc-symbols": "This Word file uses a symbol the app cannot read. Copy the text and paste it.",
    "doc-part-scanned": "Part of this PDF is a scan or a picture, so some of its words cannot be checked. "
                        "Print it to PDF from the EMR, or copy the text and paste it.",
}


def _msg(code: str) -> str:
    if code.startswith("keychain-"):
        return "The app cannot read its key from the Keychain. Unlock this Mac and try again."
    return MESSAGES.get(code, "Something went wrong. Try again, or run the self-test in Settings.")


class Api:
    def __init__(self, manager: Manager, vault, *, clipboard=None, engine=None,
                 on_show: Callable[[str, dict], None] | None = None, notify: Callable[[str], None] | None = None):
        self._m = manager
        self._vault = vault
        self._clipboard = clipboard
        self._engine = engine
        self._on_show = on_show or (lambda view_name, data: None)
        self._notify = notify or (lambda text: None)
        self._drafts: dict[str, str] = {}  # confirmation token -> the draft it was shown for
        self._guard: Callable[[], bool] | None = None
        self._choose: Callable[[], tuple[str, bytes] | None] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ plumbing

    def _set_guard(self, guard: Callable[[], bool]) -> None:
        """`guard()` is True only while the window shows the bundled UI (the app sets it)."""
        self._guard = guard

    def _set_chooser(self, choose: Callable[[], tuple[str, bytes] | None]) -> None:
        """`choose()` shows the Open dialog and returns (extension, bytes), or None (the app sets it)."""
        self._choose = choose

    def _wrap(self, fn: Callable[[], dict]) -> dict:
        if self._guard is not None:
            try:
                ok = self._guard()
            except Exception:  # noqa: BLE001 (fail closed)
                ok = False
            if not ok:  # another page got the bridge (a dropped link): it gets nothing
                return {"ok": False, "error": "foreign-page", "message": _msg("")}
        try:
            out = fn()
            return {"ok": True, **(out or {})}
        except (SessionError, EngineError, VaultError) as exc:
            code = getattr(exc, "code", None) or str(exc)
            return {"ok": False, "error": code, "message": _msg(code)}
        except CaseloadError as exc:
            return {"ok": False, "error": "caseload", "message": f"Line {exc.line}: {exc.message}"}
        except Exception as exc:  # noqa: BLE001 (never raise into the page)
            return {"ok": False, "error": type(exc).__name__, "message": _msg("")}

    @staticmethod
    def _sid(sid: Any) -> str:
        if not isinstance(sid, str) or not _SID.match(sid):
            raise SessionError("no-such-session")
        return sid

    @staticmethod
    def _text(text: Any) -> str:
        if not isinstance(text, str):
            raise SessionError("empty-text")
        return text

    # ------------------------------------------------------------------ settings and caseload

    def _settings_dict(self) -> dict:
        s = dict(DEFAULT_SETTINGS)
        stored = self._vault.load_json("settings", {}) or {}
        s.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        return s

    def _engine_settings(self) -> dict:
        """What every engine call gets: the caseload and the clinician's own settings."""
        s = self._settings_dict()
        kids = parse(self._vault.load_json("caseload", "") or "")
        return {"children": [asdict(k) for k in kids], "extra_labels": {},
                "keep_names": list(s["keep_names"]), "employer_names": list(s["employer_names"])}

    def get_settings(self) -> dict:
        # queued behind any save still waiting, so a read never returns the old values
        return self._wrap(lambda: {"settings": self._m.call(self._settings_dict)})

    def set_settings(self, values: Any) -> dict:
        def merge():  # read, merge, and save as one queued step: two saves never lose an update
            if not isinstance(values, dict):
                raise SessionError("bad-request")
            s = self._settings_dict()
            for k, v in values.items():
                if k not in DEFAULT_SETTINGS or k == "first_run_done" and not isinstance(v, bool):
                    continue
                if k in ("keep_names", "employer_names"):
                    v = [str(x).strip() for x in (v if isinstance(v, list) else str(v).splitlines()) if str(x).strip()]
                elif k in ("template_en", "template_es"):
                    v = str(v)
                    if v and "{note}" not in v:
                        raise SessionError("template")
                elif k == "ai_tip":
                    v = str(v)[:200]
                s[k] = v
            self._vault.save_json("settings", s)
            return {"settings": s}
        out = self._wrap(lambda: self._m.call(merge))  # queued: never in the middle of a run
        if not out["ok"] and out["error"] == "template":
            out["message"] = "A note template must contain {note} where the de-identified note goes."
        return out

    def get_caseload(self) -> dict:
        return self._wrap(lambda: {"text": self._m.call(self._vault.load_json, "caseload", "") or ""})

    def set_caseload(self, text: Any) -> dict:
        def run():
            t = self._text(text)
            kids = parse(t)  # raises CaseloadError with a line number
            self._m.call(self._vault.save_json, "caseload", t)  # queued: never in the middle of a run
            return {"children": len(kids)}
        return self._wrap(run)

    # ------------------------------------------------------------------ sessions

    def paste(self, text: Any) -> dict:
        return self._wrap(lambda: {"view": view(self._m.new(self._text(text)))})

    def open_document(self, ext: Any, data: Any, wipes: Any = None) -> dict:
        """A dropped document: its extension (never its name) and its bytes as base64.
        `wipes` is the Delete everything count the page read before reading the file."""
        def run():
            if not isinstance(ext, str) or not isinstance(data, str):
                raise SessionError("bad-request")
            if len(data) > documents.MAX_BYTES * 4 // 3 + 4:
                raise SessionError("doc-too-large")
            # a Delete everything after the page started reading cancels this import
            since = wipes if isinstance(wipes, int) and not isinstance(wipes, bool) else self._m.wipes
            try:
                raw = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError):
                raise SessionError("doc-unreadable") from None
            return {"view": view(self._m.new(documents.extract(ext, raw), unless_wiped_since=since))}
        return self._wrap(run)

    def choose_document(self) -> dict:
        """The Open a document… button: the Open dialog, then the same checks as a drop."""
        def run():
            if self._choose is None:
                raise SessionError("bad-request")
            since = self._m.wipes
            picked = self._choose()
            if picked is None:
                return {"cancelled": True}
            ext, raw = picked
            return {"view": view(self._m.new(documents.extract(ext, raw), unless_wiped_since=since))}
        return self._wrap(run)

    def open(self, sid: Any) -> dict:
        return self._wrap(lambda: {"view": view(self._m.refresh(self._sid(sid)))})

    def decide(self, sid: Any, key: Any, hidden: Any, confirmed: Any = False) -> dict:
        """Hide or keep an item. Keeping a caseload name needs `confirmed` (SPEC 4.3)."""
        def run():
            if not isinstance(key, str) or not isinstance(hidden, bool):
                raise SessionError("unknown-item")
            try:
                return {"view": view(self._m.decide(self._sid(sid), key, hidden, confirmed is True))}
            except SessionError as exc:
                if exc.code != "confirm-roster":
                    raise
                return {"ok": False, "error": "confirm-roster",
                        "message": f"\u201c{exc.detail}\u201d is on your caseload. Show it in the copied text anyway?"}
        return self._wrap(run)

    def hide_span(self, sid: Any, start: Any, end: Any) -> dict:
        def run():
            if not isinstance(start, int) or not isinstance(end, int) or isinstance(start, bool):
                raise SessionError("bad-span")
            return {"view": view(self._m.hide_span(self._sid(sid), start, end))}
        return self._wrap(run)

    def undo(self, sid: Any) -> dict:
        return self._wrap(lambda: {"view": view(self._m.undo_last(self._sid(sid)))})

    def copy(self, sid: Any, with_template: Any = False) -> dict:
        def run():
            s = self._m.get(self._sid(sid))
            template = None
            if with_template is True:
                st = self._settings_dict()
                lang = view(s)["language"]
                template = (st["template_es"] if lang == "Spanish" else st["template_en"]) or None
            self._m.copy(s.id, template)
            s = self._m.get(s.id)  # Copy may have rerun the checks (a caseload change): show what was copied
            return {"notice": f"Copied · ref {s.ref} · the clipboard clears in 2 minutes",
                    "tip": self._settings_dict()["ai_tip"], "view": view(s)}
        return self._wrap(run)

    def set_state(self, sid: Any, state: Any) -> dict:
        return self._wrap(lambda: {"view": view(self._m.set_state(self._sid(sid), str(state)))})

    def history(self) -> dict:
        def run():
            rows = []
            for h in self._m.history():
                rows.append({"id": h.id, "ref": h.ref, "state": h.state, "child": h.meta.get("child", ""),
                             "hidden": h.meta.get("hidden", 0), "updated": h.updated,
                             "days_left": round(h.days_left or 0, 1), "blocked": h.meta.get("blocked", False)})
            return {"rows": rows}
        return self._wrap(run)

    def delete(self, sid: Any) -> dict:
        return self._wrap(lambda: (self._m.delete(self._sid(sid)), {})[1])

    # ------------------------------------------------------------------ restore

    def restore(self, draft: Any = None) -> dict:
        def run():
            text = self._clipboard.read() if draft is None and self._clipboard is not None else self._text(draft)
            res = self._m.restore_draft(text)
            if res.get("status") == "confirm":
                # The confirmation is bound to this exact draft: a later restore cannot swap it.
                token = secrets.token_hex(8)
                with self._lock:
                    self._drafts[token] = text
                    while len(self._drafts) > 20:
                        self._drafts.pop(next(iter(self._drafts)))
                res["token"] = token
            return {"restore": res}
        return self._wrap(run)

    def restore_confirm(self, sid: Any, ref: Any = None, token: Any = None) -> dict:
        def run():
            with self._lock:
                text = self._drafts.get(token) if isinstance(token, str) else None
            if not text:
                raise SessionError("draft-expired")
            if not isinstance(ref, str):
                raise SessionError("no-such-session")
            return {"restore": self._m.restore_draft(text, self._sid(sid), True, ref)}
        return self._wrap(run)

    def copy_restored(self, sid: Any, rtoken: Any = None) -> dict:
        def run():
            self._m.copy_restored(self._sid(sid), rtoken if isinstance(rtoken, str) else "")
            return {"notice": "Copied the restored draft · the clipboard clears in 2 minutes"}
        return self._wrap(run)

    # ------------------------------------------------------------------ hotkeys (SPEC 5.1)

    def hotkey_deidentify(self) -> dict:
        """⌃⌥D: de-identify the clipboard. No check items: straight back to the clipboard."""
        def run():
            cc = self._clipboard.change_count() if self._clipboard is not None else None
            text = self._clipboard.read() if self._clipboard is not None else ""
            s = self._m.new(self._text(text))
            v = view(s)
            if not v["blocked"] and v["counts"]["checks"] == 0:
                try:
                    self._m.copy(s.id, None, cc)  # only if the clipboard still holds this note
                    note = f"Hid {v['counts']['hidden']} items · ref {s.ref}"
                    self._notify(note)
                    return {"view": v, "copied": True, "notice": note}
                except SessionError as exc:
                    if exc.code != "clipboard-changed":
                        raise
            self._on_show("check", v)
            return {"view": view(self._m.get(s.id)), "copied": False}
        return self._wrap(run)

    def _clipboard_text(self) -> str:
        """The clipboard text for ⌃⌥R (Python side only: the underscore keeps it off the JS bridge)."""
        try:
            return self._clipboard.read() if self._clipboard is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    def hotkey_restore(self) -> dict:
        out = self.restore(None)
        self._on_show("restore", out)
        return out

    # ------------------------------------------------------------------ app

    def state(self) -> dict:
        def run():
            st = self._settings_dict()
            return {"first_run": not st["first_run_done"], "engine": getattr(self._engine, "status", "unknown"),
                    "restarts": getattr(self._engine, "restarts", 0), "wipes": self._m.wipes}
        return self._wrap(run)

    def selftest(self) -> dict:
        def run():
            if self._engine is None:
                raise SessionError("not-ready")
            return {"selftest": self._engine.selftest()}
        return self._wrap(run)

    def delete_everything(self, confirm: Any) -> dict:
        def run():
            if confirm != "DELETE":
                raise SessionError("confirm")
            self._m.delete_everything()
            return {}
        out = self._wrap(run)
        if not out["ok"] and out["error"] == "confirm":
            out["message"] = "Type DELETE to confirm."
        return out
