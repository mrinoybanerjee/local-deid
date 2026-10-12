"""Clipboard: concealed marker, read back, timed clear only of our own copy (macOS only)."""

import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS pasteboard")


@pytest.fixture
def pb():
    from AppKit import NSPasteboard, NSPasteboardTypeString

    p = NSPasteboard.generalPasteboard()
    saved = p.stringForType_(NSPasteboardTypeString)
    yield p
    p.clearContents()
    if saved is not None:
        p.setString_forType_(saved, NSPasteboardTypeString)


def test_write_is_concealed_and_cleared(pb):
    from localdeid.app.clipboard import CONCEALED, MacClipboard

    c = MacClipboard(clear_after_s=0.3)
    c.write("ref: K7F2\n[CHILD_1] said hi.")
    assert c.read() == "ref: K7F2\n[CHILD_1] said hi."
    assert CONCEALED in list(pb.types())
    time.sleep(0.6)
    assert c.read() == ""


def test_newer_copy_is_not_cleared(pb):
    from AppKit import NSPasteboardTypeString

    from localdeid.app.clipboard import MacClipboard

    c = MacClipboard(clear_after_s=0.3)
    c.write("ours")
    pb.clearContents()
    pb.setString_forType_("The clinician's own copy", NSPasteboardTypeString)
    time.sleep(0.6)
    assert c.read() == "The clinician's own copy"


def test_an_old_timer_never_clears_a_newer_copy():
    from localdeid.app.clipboard import MacClipboard

    class PB:
        def __init__(self):
            self.n, self.text = 0, None

        def clearContents(self):
            self.n += 1
            self.text = None

        def declareTypes_owner_(self, *_):
            pass

        def setString_forType_(self, s, t):
            if t == "public.utf8-plain-text":
                self.text = s

        def changeCount(self):
            return self.n

    import threading

    c = MacClipboard.__new__(MacClipboard)
    c._pb, c._type, c.clear_after_s = PB(), "public.utf8-plain-text", 999
    c._ours, c._timer, c._lock, c._gen = None, None, threading.RLock(), 0
    c.write("first")
    old_gen = c._gen
    c.write("second")
    c._clear_if_ours(old_gen)  # the first copy's timer fires late
    assert c._pb.text == "second" and c._ours is not None
    c.clear_now()  # shutdown still clears the newer copy
    assert c._pb.text is None
    c._timer.cancel()
