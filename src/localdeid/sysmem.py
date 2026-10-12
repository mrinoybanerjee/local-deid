"""Process memory on macOS through task_info(TASK_VM_INFO).

`phys_footprint` is the number macOS uses for memory pressure and jetsam (and
what Activity Monitor shows as "Memory"); it includes GPU/Metal allocations
charged to the process. `ledger_phys_footprint_peak` is the kernel's lifetime
peak of that number for this task, so no sampling is needed to catch spikes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from dataclasses import dataclass

TASK_VM_INFO = 22
# Byte offsets in struct task_vm_info (osfmk/mach/task_info.h).
_OFF_RESIDENT = 16
_OFF_RESIDENT_PEAK = 24
_OFF_PHYS_FOOTPRINT = 144  # rev1
_OFF_LEDGER_FOOTPRINT_PEAK = 168  # rev3
_NEED_BYTES = _OFF_LEDGER_FOOTPRINT_PEAK + 8
_BUF_WORDS = 128  # larger than any revision of the struct; natural_t = 4 bytes


@dataclass(frozen=True)
class MemInfo:
    footprint: int
    footprint_peak: int
    resident: int
    resident_peak: int


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.mach_task_self.restype = ctypes.c_uint32
    lib.task_info.argtypes = [
        ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
    ]
    lib.task_info.restype = ctypes.c_int
    return lib


_LIBC = _libc() if sys.platform == "darwin" else None


def meminfo() -> MemInfo:
    if _LIBC is None:
        raise OSError("task_info is only available on macOS")
    buf = (ctypes.c_uint32 * _BUF_WORDS)()
    count = ctypes.c_uint32(_BUF_WORDS)
    task = _LIBC.mach_task_self()
    kr = _LIBC.task_info(task, TASK_VM_INFO, ctypes.byref(buf), ctypes.byref(count))
    if kr != 0:
        raise OSError(f"task_info failed: kern_return {kr}")
    if count.value * 4 < _NEED_BYTES:
        raise OSError(f"task_vm_info too short ({count.value * 4} bytes)")
    raw = bytes(buf)

    def u64(off: int) -> int:
        return int.from_bytes(raw[off : off + 8], "little", signed=False)

    return MemInfo(
        footprint=u64(_OFF_PHYS_FOOTPRINT),
        footprint_peak=u64(_OFF_LEDGER_FOOTPRINT_PEAK),
        resident=u64(_OFF_RESIDENT),
        resident_peak=u64(_OFF_RESIDENT_PEAK),
    )


_RUSAGE_INFO_V4 = 4
_OFF_RI_PHYS_FOOTPRINT = 72
_OFF_RI_LIFETIME_MAX_FOOTPRINT = 240  # struct rusage_info_v4 (sys/resource.h)


def peak_footprint(pid: int) -> int | None:
    """Lifetime peak physical footprint of another process of this user, in bytes,
    from proc_pid_rusage(RUSAGE_INFO_V4). None if the process cannot be read."""
    if sys.platform != "darwin":
        return None
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    buf = (ctypes.c_uint64 * 64)()
    if lib.proc_pid_rusage(int(pid), _RUSAGE_INFO_V4, ctypes.byref(buf)) != 0:
        return None
    raw = bytes(buf)
    peak = int.from_bytes(raw[_OFF_RI_LIFETIME_MAX_FOOTPRINT:_OFF_RI_LIFETIME_MAX_FOOTPRINT + 8], "little")
    return peak or None
