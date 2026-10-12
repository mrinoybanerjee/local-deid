"""Clipboard (SPEC 4.6): concealed writes, cleared after 2 minutes.

Writes are marked `org.nspasteboard.ConcealedType` (and TransientType), so
clipboard managers that honor the convention skip them. A timer clears the
clipboard 120 seconds later, but only if it still holds our copy: anything
the clinician copied since then is left alone.
"""

from __future__ import annotations

import threading

CLEAR_AFTER_S = 120.0
CONCEALED = "org.nspasteboard.ConcealedType"
TRANSIENT = "org.nspasteboard.TransientType"


class MacClipboard:
    def __init__(self, clear_after_s: float = CLEAR_AFTER_S):
        from AppKit import NSPasteboard, NSPasteboardTypeString

        self._pb = NSPasteboard.generalPasteboard()
        self._type = NSPasteboardTypeString
        self.clear_after_s = clear_after_s
        self._ours: int | None = None
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()  # writes, timers, and clear_now never interleave
        self._gen = 0

    def change_count(self) -> int:
        return int(self._pb.changeCount())

    def read(self) -> str:
        s = self._pb.stringForType_(self._type)
        return str(s) if s is not None else ""

    def write(self, text: str) -> None:
        with self._lock:
            pb = self._pb
            pb.clearContents()
            pb.declareTypes_owner_([self._type, CONCEALED, TRANSIENT], None)
            pb.setString_forType_(text, self._type)
            pb.setString_forType_("", CONCEALED)
            pb.setString_forType_("", TRANSIENT)
            self._ours = pb.changeCount()
            self._gen += 1
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.clear_after_s, self._clear_if_ours, args=(self._gen,))
            self._timer.daemon = True
            self._timer.start()

    def _clear_if_ours(self, gen: int | None = None) -> None:
        with self._lock:
            if gen is not None and gen != self._gen:
                return  # an older copy's timer: a newer copy owns the clipboard now
            if self._ours is not None and self._pb.changeCount() == self._ours:
                self._pb.clearContents()
            self._ours = None

    def clear_now(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._clear_if_ours()
