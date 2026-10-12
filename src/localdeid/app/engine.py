"""UI-side client for the engine worker (SPEC 4.1).

Spawns `python -m localdeid.app.worker`, sends one JSON line per request, and
reads replies on a reader thread. If the worker dies (crash, kill, pipe break)
the next call respawns it and resends once; the caller sees `restarting` in
the meantime. Replies carry the session's `rev`; the session layer drops stale
ones. An idle worker is stopped after `idle_kill_s` to free memory on an 8 GB
Mac (SPEC 4.7); the next request reloads it.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path


class EngineError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


class EngineClient:
    def __init__(self, models: Path, *, backend: str = "mlx_8bit", gliner: bool = False, model: bool = True,
                 idle_kill_s: float = 600.0, python: str | None = None, worker_args: list[str] | None = None):
        self.models = Path(models)
        self.backend = backend
        self.gliner = gliner
        self.model = model
        self.idle_kill_s = idle_kill_s
        self.python = python or sys.executable
        self.worker_args = worker_args  # tests: run a fake worker instead
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, Future] = {}
        self._ids = itertools.count(1)
        self._ready: Future | None = None
        self._last_use = time.monotonic()
        self.restarts = 0
        self._crashed = False  # the last worker died without stop()
        self.status = "stopped"  # stopped | loading | ready | restarting
        self._stop = threading.Event()
        self._idle_thread = threading.Thread(target=self._idle_loop, daemon=True)
        self._idle_thread.start()

    # ------------------------------------------------------------------ process

    def _cmd(self) -> list[str]:
        if self.worker_args is not None:
            return [self.python, *self.worker_args]
        cmd = [self.python, "-m", "localdeid.app.worker", str(self.models), "--backend", self.backend]
        if self.gliner:
            cmd.append("--gliner")
        if not self.model:
            cmd.append("--no-model")
        return cmd

    def _spawn_locked(self) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self._proc = subprocess.Popen(self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env, bufsize=1)
        self._ready = Future()
        self.status = "loading" if self.restarts == 0 else "restarting"
        threading.Thread(target=self._reader, args=(self._proc, self._ready), daemon=True).start()

    def _reader(self, proc: subprocess.Popen, ready: Future) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("op") == "ready":
                if not ready.done():
                    ready.set_result(msg)
                with self._lock:
                    if proc is self._proc:
                        self.status = "ready"
                continue
            with self._lock:  # the cleanup of an exiting worker iterates this dict under the lock
                entry = self._pending.pop(msg.get("id"), None) if msg.get("id") is not None else None
            if entry is not None and not entry[1].done():
                entry[1].set_result(msg)
        # the worker exited: fail everything still waiting on it
        if not ready.done():
            ready.set_exception(EngineError("worker-exited"))
        with self._lock:
            if proc is self._proc:
                self._proc = None
                self._crashed = True
                self.status = "stopped"
            # only this worker's requests: a replacement may already be serving new ones
            mine = [rid for rid, (p, _) in self._pending.items() if p is proc]
            pending = [self._pending.pop(rid)[1] for rid in mine]
        for fut in pending:
            if not fut.done():
                fut.set_exception(EngineError("worker-exited"))

    def _ensure(self, timeout: float) -> None:
        with self._lock:
            if self._stop.is_set():
                raise EngineError("closed")  # the app is quitting: never start another worker
            if self._proc is None or self._proc.poll() is not None:
                if self._crashed or self._proc is not None:
                    self.restarts += 1
                self._crashed = False
                self._spawn_locked()
            ready = self._ready
        assert ready is not None
        ready.result(timeout=timeout)

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            self.status = "stopped"
        if proc is not None:
            proc.kill()
            proc.wait(timeout=10)

    def close(self) -> None:
        self._stop.set()
        self.stop()

    def cancel(self) -> None:
        """Kill a long job; the next call restarts the worker (SPEC 4.1)."""
        self.stop()
        with self._lock:
            self._crashed = True

    def _idle_loop(self) -> None:
        while not self._stop.wait(5.0):
            with self._lock:
                idle = self._proc is not None and not self._pending and \
                    time.monotonic() - self._last_use > self.idle_kill_s
            if idle:
                self.stop()

    # ------------------------------------------------------------------ calls

    def call(self, op: str, *, timeout: float = 300.0, load_timeout: float = 300.0, **fields) -> dict:
        """Send one request; resend once if the worker died under it."""
        for attempt in (0, 1):
            self._last_use = time.monotonic()
            try:
                self._ensure(load_timeout)
            except EngineError as exc:
                if attempt or exc.code == "closed":
                    raise
                continue
            except TimeoutError:  # alive but never ready: stop it, so the next call starts fresh
                self.cancel()
                raise EngineError("timeout") from None
            rid = next(self._ids)
            fut: Future = Future()
            with self._lock:  # bookkeeping only: never held across a blocking pipe write
                proc = self._proc
                if proc is None:
                    continue
                self._pending[rid] = (proc, fut)
            line = json.dumps({"id": rid, "op": op, **fields}, ensure_ascii=False) + "\n"
            sent, failed = threading.Event(), []

            def send() -> None:
                try:
                    with self._write_lock:  # one request at a time on the pipe
                        assert proc.stdin is not None
                        proc.stdin.write(line)
                        proc.stdin.flush()
                except (BrokenPipeError, OSError, ValueError) as exc:
                    failed.append(exc)
                finally:
                    sent.set()

            threading.Thread(target=send, daemon=True).start()
            if not sent.wait(timeout):  # a worker that stops reading: the deadline covers sending too
                with self._lock:
                    self._pending.pop(rid, None)
                self.cancel()
                raise EngineError("timeout")
            if failed:
                with self._lock:
                    self._pending.pop(rid, None)
                proc.kill()
                continue
            try:
                msg = fut.result(timeout=timeout)
            except EngineError:
                if attempt:
                    raise
                continue
            except TimeoutError:
                self.cancel()
                raise EngineError("timeout") from None
            self._last_use = time.monotonic()
            if not msg.get("ok"):
                raise EngineError(msg.get("error", "error"), msg.get("detail", ""))
            return msg
        raise EngineError("worker-exited")

    def deid(self, text: str, settings: dict, *, rev: int, keep_keys=(), manual_hides=(), ref: str | None = None,
             timeout: float = 300.0) -> dict:
        return self.call("deid", timeout=timeout, text=text, settings=settings, rev=rev, keep_keys=list(keep_keys),
                         manual_hides=[list(h) for h in manual_hides], ref=ref)

    def selftest(self) -> dict:
        return self.call("selftest")["result"]
