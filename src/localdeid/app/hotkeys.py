"""Global hotkeys ⌃⌥D and ⌃⌥R (SPEC 5.1) through Carbon's RegisterEventHotKey.

RegisterEventHotKey needs no Accessibility grant (an NSEvent global monitor does,
and the grant does not stick to a script bundle that execs Python). It also
takes the keystroke, so the front app does not see it. Carbon dispatches the
event on the main thread through the NSApplication run loop.
"""

from __future__ import annotations

import ctypes
from typing import Callable

_CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"
CONTROL, OPTION = 1 << 12, 1 << 11  # controlKey, optionKey
_SIGNATURE = int.from_bytes(b"LDID", "big")


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_KEYBOARD, _PRESSED = _fourcc("keyb"), 5  # kEventClassKeyboard, kEventHotKeyPressed
_DIRECT, _HKID = _fourcc("----"), _fourcc("hkid")  # kEventParamDirectObject, typeEventHotKeyID


class HotKeys:
    """Register key codes with Control+Option. `register()` returns the key codes
    that failed (another app owns them); the caller can fall back for those."""

    def __init__(self) -> None:
        self._lib = ctypes.CDLL(_CARBON)
        lib = self._lib
        lib.GetApplicationEventTarget.restype = ctypes.c_void_p
        lib.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_uint32,
                                            ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p)]
        lib.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p,
                                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        lib.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        lib.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                          ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        self._actions: dict[int, Callable[[], None]] = {}
        self._refs: list[ctypes.c_void_p] = []
        self._proc = _HANDLER(self._on_event)  # kept on self: Carbon holds only the pointer
        spec = _EventTypeSpec(_KEYBOARD, _PRESSED)
        self._handler_ref = ctypes.c_void_p()
        status = lib.InstallEventHandler(lib.GetApplicationEventTarget(), self._proc, 1, ctypes.byref(spec),
                                         None, ctypes.byref(self._handler_ref))
        if status != 0:
            raise OSError(f"InstallEventHandler {status}")

    def _on_event(self, _call_ref, event, _data) -> int:
        hk = _HotKeyID()
        status = self._lib.GetEventParameter(event, _DIRECT, _HKID, None, ctypes.sizeof(hk), None,
                                             ctypes.byref(hk))
        if status == 0 and hk.signature == _SIGNATURE and hk.id in self._actions:
            try:
                self._actions[hk.id]()
            except Exception:  # noqa: BLE001 (an exception must not cross into Carbon)
                pass
            return 0
        return -9874  # eventNotHandledErr

    def register(self, actions: dict[int, Callable[[], None]], modifiers: int = CONTROL | OPTION) -> list[int]:
        failed = []
        target = self._lib.GetApplicationEventTarget()
        for code, fn in actions.items():
            ref = ctypes.c_void_p()
            status = self._lib.RegisterEventHotKey(code, modifiers, _HotKeyID(_SIGNATURE, code), target, 0,
                                                   ctypes.byref(ref))
            if status == 0:
                self._actions[code] = fn
                self._refs.append(ref)
            else:
                failed.append(code)
        return failed

    def close(self) -> None:
        for ref in self._refs:
            self._lib.UnregisterEventHotKey(ref)
        self._refs.clear()
        self._actions.clear()
        if self._handler_ref:  # a later HotKeys may get a callback at this same address
            self._lib.RemoveEventHandler.argtypes = [ctypes.c_void_p]
            self._lib.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()

    def simulate(self, code: int) -> int:
        """Tests: send a hot-key-pressed event for `code` to this app, as Carbon does."""
        lib = self._lib
        lib.CreateEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_double,
                                    ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        lib.SetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t,
                                          ctypes.c_void_p]
        lib.SendEventToEventTarget.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.GetApplicationEventTarget.restype = ctypes.c_void_p
        ev = ctypes.c_void_p()
        status = lib.CreateEvent(None, _KEYBOARD, _PRESSED, 0.0, 0, ctypes.byref(ev))
        if status != 0:
            return status
        hk = _HotKeyID(_SIGNATURE, code)
        lib.SetEventParameter(ev, _DIRECT, _HKID, ctypes.sizeof(hk), ctypes.byref(hk))
        status = lib.SendEventToEventTarget(ev, lib.GetApplicationEventTarget())
        lib.ReleaseEvent.argtypes = [ctypes.c_void_p]
        lib.ReleaseEvent(ev)
        return status
