"""Sessions and the one serialized queue (SPEC 4.1, 4.5, 5).

Every change to a session runs on one worker thread, in order. A session holds
the pasted text, the clinician's decisions (keep keys, manual hides, an undo stack),
and the engine's last Result. Each change bumps `rev`; an engine reply for an
older rev is dropped. Every change is saved to the vault before the call
returns, including each Copy, because restore needs the exact context line
that was copied.
"""

from __future__ import annotations

import queue
import re
import secrets
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable

from ..pipeline import new_ref
from ..restore import best_session, copy_block, find_refs, fingerprint_scores, restore
from .serialize import result_from_dict, result_to_dict, restored_to_dict

MAX_TEXT = 200_000  # characters; a 5,000-word evaluation is about 35,000


class SessionError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class Session:
    id: str
    ref: str
    raw: str
    state: str = "check"
    keep_keys: list[str] = field(default_factory=list)
    manual_hides: list[list[int]] = field(default_factory=list)
    undo: list[tuple[list[str], list[list[int]]]] = field(default_factory=list)
    rev: int = 0
    result: dict | None = None
    created: float = field(default_factory=time.time)
    restored: str = ""
    child: str = ""  # History label; kept while a rerun has no result yet
    settings_key: str = ""  # digest of the caseload and settings the result was made with
    restored_token: str = ""  # the latest restore's token
    restorations: dict[str, str] = field(default_factory=dict)  # token -> restored text (last 10)
    roster_ok: list[str] = field(default_factory=list)  # caseload names the clinician confirmed showing (SPEC 4.3)
    # Every copied ref keeps the exact result it was copied from, so a draft made
    # from an earlier copy restores with that copy's mapping (refs never change meaning).
    copies: dict[str, dict] = field(default_factory=dict)

    def body(self) -> dict:
        return {"raw": self.raw, "keep_keys": self.keep_keys, "manual_hides": self.manual_hides,
                "undo": [[k, h] for k, h in self.undo[-50:]], "rev": self.rev, "result": self.result,
                "restored": self.restored, "copies": self.copies, "settings_key": self.settings_key,
                "restored_token": self.restored_token, "roster_ok": self.roster_ok,
                "restorations": self.restorations}

    @classmethod
    def from_body(cls, sid: str, ref: str, state: str, created: float, b: dict) -> "Session":
        return cls(id=sid, ref=ref, raw=b["raw"], state=state, keep_keys=list(b["keep_keys"]),
                   manual_hides=[list(h) for h in b["manual_hides"]],
                   undo=[(list(k), [list(x) for x in h]) for k, h in b.get("undo", [])], rev=b["rev"],
                   result=b["result"], created=created, restored=b.get("restored", ""),
                   copies=dict(b.get("copies", {})), settings_key=b.get("settings_key", ""),
                   restored_token=b.get("restored_token", ""), roster_ok=list(b.get("roster_ok", [])),
                   restorations=dict(b.get("restorations", {})))

    def refs(self) -> list[str]:
        return sorted(set(self.copies) | {self.ref})


def json_key(result: dict) -> str:
    """Everything a restore uses from a result (canonical values, every instance,
    escapes, context), as a comparable string. Only the ref is left out."""
    import json

    return json.dumps({k: v for k, v in result.items() if k != "ref"}, sort_keys=True, ensure_ascii=False)


def child_label(result: dict | None) -> str:
    """'Mateo R.' from the first CHILD value; '' when none (shown large in the UI)."""
    if not result:
        return ""
    src = result.get("source", "")
    for it in result["items"]:
        if it["kind"] == "CHILD" and it["hidden"]:
            parts = result["canonical"].get(it["tag"], "").split()
            if parts:
                # only a surname written right after this child's name is the child's
                sur = next((result["canonical"].get(x["tag"], "") for x in result["items"]
                            if x["kind"] == "SURNAME" and x["hidden"] and x["start"] >= it["end"]
                            and src[it["end"]:x["start"]].strip() == ""), "")
                return parts[0] + (f" {sur[0]}." if sur else "")
    return ""


class Manager:
    def __init__(self, engine, vault, *, clipboard=None, settings_loader: Callable[[], dict] | None = None,
                 clock: Callable[[], float] = time.time):
        self.engine = engine
        self.vault = vault
        self.clipboard = clipboard
        self.clock = clock
        self._settings_loader = settings_loader or (lambda: {})
        self._open: dict[str, Session] = {}
        self._q: queue.Queue = queue.Queue()
        self._closing = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="session-queue")
        self._thread.start()

    # ------------------------------------------------------------------ queue

    def _run(self) -> None:
        while True:
            fn, args, fut = self._q.get()
            if fn is None:
                return
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn(*args))
                except BaseException as exc:  # noqa: BLE001 (delivered to the caller)
                    fut.set_exception(exc)

    def submit(self, fn: Callable, *args) -> Future:
        fut: Future = Future()
        with self._closing:
            if self._closed:
                fut.set_exception(SessionError("closed"))  # the app is quitting
                return fut
            self._q.put((fn, args, fut))
        return fut

    def call(self, fn: Callable, *args, timeout: float = 600.0) -> Any:
        return self.submit(fn, *args).result(timeout=timeout)

    def close(self, timeout: float = 10.0) -> None:
        """Take no new work, finish what is queued, and wait for the queue thread, so
        nothing writes the clipboard after the caller clears it."""
        with self._closing:
            if not self._closed:
                self._closed = True
                self._q.put((None, (), None))
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ helpers (queue thread only)

    def _settings(self) -> dict:
        return self._settings_loader()

    @staticmethod
    def _key(settings: dict) -> str:
        import hashlib
        import json

        return hashlib.sha256(json.dumps(settings, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _get(self, sid: str) -> Session:
        if sid in self._open:
            return self._open[sid]
        h, body = self.vault.load(sid)
        s = Session.from_body(h.id, h.ref, h.state, h.created, body)
        s.child = h.meta.get("child", "")
        self._open[sid] = s
        return s

    def _peek(self, sid: str) -> Session:
        """A session for reading only: from the cache, else loaded without caching it."""
        if sid in self._open:
            return self._open[sid]
        h, body = self.vault.load(sid)
        return Session.from_body(h.id, h.ref, h.state, h.created, body)

    def _save(self, s: Session) -> None:
        r = s.result or {}
        if s.result is not None:
            s.child = child_label(r)
        meta = {"child": s.child, "hidden": sum(1 for i in r.get("items", []) if i["hidden"]),
                "unsure": sum(1 for i in r.get("items", []) if i["hidden"] and not i["sure"]),
                "blocked": bool(r.get("failures")), "refs": s.refs()}
        self.vault.save(s.id, s.ref, s.state, meta, s.body(), created=s.created)

    def _run_engine(self, s: Session) -> None:
        """Rerun the engine for the session's current decisions. The old result is
        dropped first: if the run fails, nothing stale can be copied (Copy reruns)."""
        if s.ref in s.copies:
            s.ref = self._fresh_ref()  # this ref was copied: an edit gets a new ref, the old one keeps its mapping
        s.result = None
        s.rev += 1
        if s.id in self._open:
            self._save(s)  # the new decisions and "no result" reach the vault before the run can fail
        rev = s.rev
        settings = self._settings()
        s.settings_key = self._key(settings)
        msg = self.engine.deid(s.raw, settings, rev=rev, keep_keys=s.keep_keys,
                               manual_hides=s.manual_hides, ref=s.ref)
        if msg.get("rev") != s.rev:
            raise SessionError("not-ready")  # a stale reply (SPEC 4.1)
        if msg["result"].get("ref") != s.ref:
            raise SessionError("ref-mismatch")
        # A Keep made before a name joined the caseload was never confirmed for it:
        # it goes, and the name is hidden again until the clinician confirms (SPEC 4.3).
        unconfirmed = {(it["decision_key"] or it["key"]) for it in msg["result"]["items"]
                       if any(n.startswith("roster:") for n in it["notes"])} & set(s.keep_keys)
        unconfirmed -= set(s.roster_ok)
        if unconfirmed:
            s.keep_keys = [k for k in s.keep_keys if k not in unconfirmed]
            self._run_engine(s)
            return
        s.result = msg["result"]

    @staticmethod
    def _last_copy(s: Session) -> dict | None:
        return list(s.copies.values())[-1] if s.copies else s.result

    def _ensure_result(self, s: Session) -> None:
        """A result for the current decisions and the current caseload and settings:
        a caseload change since the last run makes the old result stale."""
        if s.result is None or s.settings_key != self._key(self._settings()):
            self._run_engine(s)
            self._save(s)

    def _fresh_ref(self) -> str:
        used = self.vault.refs_in_use() | {r for s in self._open.values() for r in s.refs()}
        used |= {r for h in self.vault.list() for r in h.meta.get("refs", [h.ref])}
        for _ in range(1000):
            r = new_ref()
            if r not in used:
                return r
        raise SessionError("no-free-ref")

    # ------------------------------------------------------------------ operations (queue thread)

    def _new(self, text: str) -> Session:
        if not isinstance(text, str) or not text.strip():
            raise SessionError("empty-text")
        if len(text) > MAX_TEXT:
            raise SessionError("text-too-long")
        s = Session(self.vault.new_id(), self._fresh_ref(), text, created=self.clock())
        self._run_engine(s)
        self._open[s.id] = s
        self._save(s)
        return s

    def _decide(self, sid: str, decision_key: str, hidden: bool, confirmed: bool = False) -> Session:
        s = self._get(sid)
        self._ensure_result(s)  # after a failed run, rebuild the list the user is looking at
        items = [it for it in s.result["items"] if (it["decision_key"] or it["key"]) == decision_key]
        if not items:
            raise SessionError("unknown-item")
        roster = next((it for it in items if any(n.startswith("roster:") for n in it["notes"])), None)
        if not hidden and roster is not None and not confirmed:
            # SPEC 4.3: un-hiding a caseload name needs a confirmation. Checked here, on the
            # queue thread and after any rebuild, so no path can skip it.
            raise SessionError("confirm-roster", s.result["source"][roster["start"]:roster["end"]])
        s.undo.append((list(s.keep_keys), [list(h) for h in s.manual_hides]))
        keep = set(s.keep_keys)
        if hidden:
            keep.discard(decision_key)
        else:
            keep.add(decision_key)
            if roster is not None and decision_key not in s.roster_ok:
                s.roster_ok.append(decision_key)  # confirmed above
        s.keep_keys = sorted(keep)
        self._run_engine(s)
        self._save(s)
        return s

    def _hide_span(self, sid: str, start: int, end: int) -> Session:
        s = self._get(sid)
        self._ensure_result(s)
        n = len((s.result or {}).get("source", s.raw))
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= n):
            raise SessionError("bad-span")
        s.undo.append((list(s.keep_keys), [list(h) for h in s.manual_hides]))
        s.manual_hides.append([start, end])
        # A hide wins over an earlier Keep of the same words (the undo snapshot keeps the Keep).
        covered = {it["decision_key"] or it["key"] for it in (s.result or {}).get("items", [])
                   if it["start"] < end and start < it["end"]}
        s.keep_keys = [k for k in s.keep_keys if k not in covered]
        self._run_engine(s)
        self._save(s)
        return s

    def _undo(self, sid: str) -> Session:
        s = self._get(sid)
        if not s.undo:
            return s
        s.keep_keys, s.manual_hides = s.undo.pop()
        self._run_engine(s)
        self._save(s)
        return s

    def _copy(self, sid: str, template: str | None, expect_change: int | None = None) -> str:
        if expect_change is not None and self.clipboard is not None and \
                self.clipboard.change_count() != expect_change:
            raise SessionError("clipboard-changed")  # The clinician copied something newer: keep it
        s = self._get(sid)
        self._ensure_result(s)  # after a failed rerun, Copy runs the checks again first
        r = result_from_dict(s.result)
        if r.blocked:
            raise SessionError("blocked")  # never a "copy anyway" (SPEC 5.3)
        text = copy_block(r, template=template)
        s.result = result_to_dict(r)  # keeps the copied context for restore
        if expect_change is not None and self.clipboard is not None and \
                self.clipboard.change_count() != expect_change:
            raise SessionError("clipboard-changed")  # a rerun took time and the clinician copied meanwhile
        before = (s.state, dict(s.copies))
        s.copies[s.ref] = s.result
        s.state = "waiting"
        self._save(s)
        if self.clipboard is not None:
            if expect_change is not None and self.clipboard.change_count() != expect_change:
                s.state, s.copies = before  # nothing was copied: undo the record of a copy
                self._save(s)
                raise SessionError("clipboard-changed")
            self.clipboard.write(text)
        return text

    def _restore(self, draft: str, sid: str | None, confirmed: bool, chosen_ref: str | None = None) -> dict:
        if not isinstance(draft, str) or not draft.strip():
            raise SessionError("empty-text")
        if len(draft) > MAX_TEXT:
            raise SessionError("text-too-long")
        refs = find_refs(draft)
        headers = self.vault.list()
        if sid is None and refs:
            match = [h for h in headers if set(h.meta.get("refs", [h.ref])) & set(refs)]
            if not match:
                return {"status": "no-session", "refs": refs}
            sid = match[0].id
        if sid is None:
            # No ref line: rank every copied mapping of the sessions waiting for a draft by
            # fingerprint (SPEC 4.5). Each candidate is one copy, so a confirmation picks
            # the exact mapping, even when one note was copied twice with different tags.
            cands = [h for h in headers if h.state == "waiting"] or headers
            loaded = []
            for h in cands:  # every eligible session is ranked; only the shown list is cut
                try:
                    s = self._peek(h.id)
                    snaps = list(s.copies.values()) or ([s.result] if s.result else [])
                    loaded += [(h, result_from_dict(r)) for r in snaps]
                except Exception:  # noqa: BLE001 (a damaged session is just not a candidate)
                    continue
            best = best_session(draft, [r for _, r in loaded])
            by_ref = {r.ref: (h, r) for h, r in loaded}
            # One candidate per session (so copies of one note cannot push another child
            # off the list), best first. Each lists its distinct copies, best first, so the
            # confirmation picks the exact mapping.
            sessions: dict[str, dict] = {}
            keys: dict[str, set] = {}
            for sc, r in fingerprint_scores(draft, [r for _, r in loaded]):
                if sc <= 0:
                    continue
                h = by_ref[r.ref][0]
                d = result_to_dict(r)
                c = sessions.setdefault(h.id, {"id": h.id, "ref": r.ref, "child": child_label(d),
                                               "score": round(sc, 3), "copies": []})
                k = json_key(d)
                if k not in keys.setdefault(h.id, set()):
                    keys[h.id].add(k)
                    c["copies"].append({"ref": r.ref, "score": round(sc, 3), "child": child_label(d)})
            return {"status": "confirm",
                    "best": [by_ref[best.ref][0].id, best.ref] if best is not None else None,
                    "candidates": sorted(sessions.values(), key=lambda c: -c["score"])[:5]}
        s = self._get(sid)
        if chosen_ref is not None:
            if chosen_ref in s.copies:
                copied = s.copies[chosen_ref]
            elif chosen_ref == s.ref and s.result is not None and not s.copies:
                copied = s.result  # a session saved before copies were kept: its one result
            else:
                raise SessionError("no-such-session")
        else:
            copied = next((s.copies[x] for x in refs if x in s.copies), None)
        if copied is None and not refs:
            if len({json_key(c) for c in s.copies.values()}) > 1:
                raise SessionError("which-copy")  # copies differ: the confirmation must name one
            copied = self._last_copy(s)
        if copied is None and s.result is None:
            raise SessionError("not-ready")
        r = result_from_dict(copied if copied is not None else s.result)
        out = restore(draft, r, ref_confirmed=confirmed or bool(refs))
        if not out.blocked:
            # Each restore keeps its own token, so "Copy restored draft" copies exactly the
            # restore it was shown for, whatever order the replies arrive in.
            s.restored = out.text
            s.restored_token = secrets.token_hex(8)
            s.restorations[s.restored_token] = out.text
            while len(s.restorations) > 10:
                s.restorations.pop(next(iter(s.restorations)))
            self._save(s)
        d = restored_to_dict(out)
        d["rtoken"] = s.restored_token if not out.blocked else ""
        d.update({"status": "restored", "id": s.id, "ref": r.ref, "source": r.source,
                  "child": child_label(copied if copied is not None else s.result)})
        return d

    def _set_state(self, sid: str, state: str) -> Session:
        if state not in ("check", "waiting", "done"):
            raise SessionError("bad-state")
        s = self._get(sid)
        s.state = state
        self._save(s)
        return s

    def _delete(self, sid: str) -> None:
        self._open.pop(sid, None)
        self.vault.delete(sid)

    def _delete_everything(self) -> None:
        self._open.clear()  # a cached session must not be saved again under the new key
        self.vault.delete_everything()

    def _expire(self) -> list:
        warn = self.vault.expire()
        alive = {h.id for h in self.vault.list()}
        for sid in [k for k in self._open if k not in alive]:
            del self._open[sid]
        return warn

    # ------------------------------------------------------------------ public API (any thread)

    def new(self, text: str) -> Session:
        return self.call(self._new, text)

    def decide(self, sid: str, decision_key: str, hidden: bool, confirmed: bool = False) -> Session:
        return self.call(self._decide, sid, decision_key, hidden, confirmed)

    def hide_span(self, sid: str, start: int, end: int) -> Session:
        return self.call(self._hide_span, sid, start, end)

    def undo_last(self, sid: str) -> Session:
        return self.call(self._undo, sid)

    def copy(self, sid: str, template: str | None = None, expect_change: int | None = None) -> str:
        return self.call(self._copy, sid, template, expect_change)

    def restore_draft(self, draft: str, sid: str | None = None, confirmed: bool = False,
                      ref: str | None = None) -> dict:
        return self.call(self._restore, draft, sid, confirmed, ref)

    def set_state(self, sid: str, state: str) -> Session:
        return self.call(self._set_state, sid, state)

    def delete(self, sid: str) -> None:
        return self.call(self._delete, sid)

    def get(self, sid: str) -> Session:
        return self.call(self._get, sid)

    def history(self) -> list:
        return self.call(self.vault.list)

    def expire(self) -> list:
        return self.call(self._expire)

    def delete_everything(self) -> None:
        return self.call(self._delete_everything)

    def copy_restored(self, sid: str, token: str) -> None:
        """Check the token and write the clipboard in one queued step, so a restore
        finishing in between cannot swap the text (the one on screen is copied)."""
        def run():
            s = self._get(sid)
            text = s.restorations.get(token) if token else None
            if text is None:
                raise SessionError("restore-changed")  # an old or unknown restore: restore it again
            if self.clipboard is not None:
                self.clipboard.write(text)
        return self.call(run)

    def refresh(self, sid: str) -> Session:
        """The session with a result for its current decisions (reruns after a failure)."""
        def run():
            s = self._get(sid)
            self._ensure_result(s)
            return s
        return self.call(run)


# --------------------------------------------------------------------------- view model


def view(s: Session) -> dict:
    """What the Check view shows: text segments with chips, the ranked check list,
    and the header counts (SPEC 5.2). Real values stay on this Mac."""
    r = s.result or {}
    src = r.get("source", s.raw)
    items = sorted(r.get("items", []), key=lambda i: i["start"])
    segs, pos = [], 0
    for it in items:
        if it["start"] < pos:
            continue
        if it["start"] > pos:
            segs.append({"t": src[pos:it["start"]]})
        segs.append({"t": src[it["start"]:it["end"]], "tag": it["tag"], "kind": it["kind"], "hidden": it["hidden"],
                     "sure": it["sure"], "key": it["decision_key"] or it["key"], "start": it["start"], "end": it["end"]})
        pos = it["end"]
    segs.append({"t": src[pos:]})
    rows: dict[str, dict] = {}
    for it in items:
        k = it["decision_key"] or it["key"]
        row = rows.setdefault(k, {"key": k, "tag": it["tag"], "kind": it["kind"], "value": src[it["start"]:it["end"]],
                                  "hidden": it["hidden"], "sure": it["sure"], "mentions": 0, "contexts": [],
                                  "reason": _reason(it["notes"]), "check": bool(_is_check(it))})
        row["mentions"] += 1
        if len(row["contexts"]) < 3:
            a, b = max(0, it["start"] - 30), min(len(src), it["end"] + 30)
            ctx = re.sub(r"\s+", " ", src[a:b])
            if ctx not in row["contexts"]:
                row["contexts"].append(ctx)
    ranked = sorted(rows.values(), key=lambda x: (not x["check"], x["sure"], -x["mentions"], x["tag"]))
    hidden = sum(1 for i in items if i["hidden"])
    unsure = sum(1 for i in items if i["hidden"] and not i["sure"])
    return {"id": s.id, "ref": s.ref, "state": s.state, "rev": s.rev, "child": child_label(r),
            "language": _language(src), "segments": segs, "rows": ranked,
            "counts": {"hidden": hidden, "unsure": unsure, "checks": sum(1 for x in ranked if x["check"])},
            "failures": r.get("failures", []), "blocked": bool(r.get("failures")),
            "nfc_changed": r.get("nfc_changed", False), "can_undo": bool(s.undo)}


def _is_check(it: dict) -> bool:
    return (not it["sure"]) or it.get("protected_overlap") or "role-conflict" in it["notes"] or "@" in it["decision_key"]


def _reason(notes) -> str:
    n = " ".join(notes)
    for key, text in (("word-name", "name that is also a word"), ("roster", "on your caseload"),
                      ("field", "form field"), ("model", "model guess"), ("gazetteer", "name list"),
                      ("rule", "pattern"), ("propagate", "same as another mention"), ("manual", "you hid this")):
        if key in n:
            return text
    return "detected"


def _language(text: str) -> str:
    from ..restore import language_of

    return language_of(text)
