"""Block the network inside the process (SPEC 4.1). Import this first.

Every socket constructor and name lookup raises, and offline settings are
forced for libraries that would otherwise try the network. Unix-domain socket
pairs stay allowed (Python's subprocess and selectors do not need them, but
some libraries create them for wake-up pipes); anything that can reach another
host cannot be created. The OS-level check of the whole process tree is
tests/test_app_offline.py.
"""

from __future__ import annotations

import os
import socket

_INSTALLED = False


class NetworkBlocked(OSError):
    pass


def _refuse(*_a, **_k):
    raise NetworkBlocked("network access is disabled in Local DeID")


class _GuardedSocket(socket.socket):
    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):  # noqa: A002 (stdlib signature)
        if fileno is None and family not in (socket.AF_UNIX, -1):
            _refuse()
        if fileno is None and family == -1:
            _refuse()  # the default family is AF_INET
        super().__init__(family, type, proto, fileno)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    for k, v in {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "ORT_DISABLE_TELEMETRY": "1",
                 "TIKTOKEN_CACHE_DIR": "", "NO_PROXY": "*", "DO_NOT_TRACK": "1"}.items():
        os.environ[k] = v
    socket.socket = _GuardedSocket  # type: ignore[misc]
    socket.create_connection = _refuse  # type: ignore[assignment]
    socket.getaddrinfo = _refuse  # type: ignore[assignment]
    socket.gethostbyname = _refuse  # type: ignore[assignment]
    socket.gethostbyname_ex = _refuse  # type: ignore[assignment]
    socket.create_server = _refuse  # type: ignore[assignment]
    _INSTALLED = True


install()
