"""Engine client: restart after a crash, resend once, timeouts, idle stop, ordering."""

import sys
import threading
from pathlib import Path

import pytest

from localdeid.app.engine import EngineClient, EngineError

FAKE = str(Path(__file__).parent / "fixtures" / "fake_worker.py")


def client(tmp_path, **kw):
    return EngineClient(tmp_path, worker_args=[FAKE, str(tmp_path / "crashed")], **kw)


def test_echo_and_rev(tmp_path):
    c = client(tmp_path)
    try:
        m = c.deid("hello", {}, rev=7)
        assert m["result"]["echo"] == "hello" and m["rev"] == 7 and c.status == "ready"
    finally:
        c.close()


def test_crash_is_restarted_and_resent_once(tmp_path):
    c = client(tmp_path)
    try:
        first_pid = c.deid("a", {}, rev=1)["result"]["pid"]
        m = c.deid("crash-once", {}, rev=2)
        assert m["result"]["echo"] == "crash-once" and m["result"]["pid"] != first_pid
        assert c.restarts == 1
    finally:
        c.close()


def test_a_worker_that_always_crashes_raises(tmp_path):
    c = client(tmp_path)
    try:
        with pytest.raises(EngineError):
            c.deid("crash-always", {}, rev=1)
        assert c.deid("ok again", {}, rev=2)["result"]["echo"] == "ok again"
    finally:
        c.close()


def test_worker_error_is_an_engine_error(tmp_path):
    c = client(tmp_path)
    try:
        with pytest.raises(EngineError) as e:
            c.deid("bad", {}, rev=1)
        assert e.value.code == "ValueError"
    finally:
        c.close()


def test_timeout_cancels_and_next_call_works(tmp_path):
    c = client(tmp_path)
    try:
        with pytest.raises(EngineError) as e:
            c.deid("slow", {}, rev=1, timeout=0.5)
        assert e.value.code == "timeout"
        assert c.deid("fast", {}, rev=2)["result"]["echo"] == "fast"
    finally:
        c.close()


def test_idle_worker_is_stopped_and_restarts_on_use(tmp_path):
    c = client(tmp_path, idle_kill_s=0.2)
    try:
        c.deid("x", {}, rev=1)
        threading.Event().wait(6)
        assert c.status == "stopped"
        assert c.deid("y", {}, rev=2)["result"]["echo"] == "y"
    finally:
        c.close()


def test_concurrent_calls_get_their_own_replies(tmp_path):
    c = client(tmp_path)
    out = {}
    try:
        ts = [threading.Thread(target=lambda i=i: out.__setitem__(i, c.deid(f"t{i}", {}, rev=i)["result"]["echo"]))
              for i in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert out == {i: f"t{i}" for i in range(20)}
    finally:
        c.close()


def test_an_exiting_worker_fails_only_its_own_requests():
    import subprocess
    import sys
    from concurrent.futures import Future
    from pathlib import Path

    from localdeid.app.engine import EngineClient, EngineError

    c = EngineClient(Path("."), worker_args=["-c", "pass"])
    old = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE, text=True)
    new = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], stdout=subprocess.PIPE, text=True)
    f_old, f_new = Future(), Future()
    c._pending = {1: (old, f_old), 2: (new, f_new)}
    c._proc = new  # the replacement is already serving
    c._reader(old, Future())
    assert isinstance(f_old.exception(timeout=1), EngineError) and not f_new.done()
    assert 2 in c._pending
    new.kill()
    c.close()


def test_large_concurrent_requests_never_deadlock_the_pipe(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    c = client(tmp_path)
    big = ["x" * 200_000 + str(i) for i in range(10)]  # replies larger than the pipe buffer
    with ThreadPoolExecutor(10) as ex:
        outs = list(ex.map(lambda t: c.deid(t, {}, rev=1, timeout=60), big))
    assert [o["result"]["echo"] for o in outs] == big
    c.close()


def test_a_worker_that_never_gets_ready_is_stopped(tmp_path):
    from localdeid.app.engine import EngineClient, EngineError

    c = EngineClient(tmp_path, worker_args=[FAKE, str(tmp_path / "m"), "never-ready"])
    with pytest.raises(EngineError) as e:
        c.call("ping", timeout=1, load_timeout=0.5)
    assert e.value.code == "timeout" and c._proc is None  # the stuck worker was stopped
    c.close()


def test_a_worker_that_stops_reading_times_out_while_sending(tmp_path):
    import time

    from localdeid.app.engine import EngineClient, EngineError

    c = EngineClient(tmp_path, worker_args=[FAKE, str(tmp_path / "m"), "never-reads"])
    t = time.monotonic()
    with pytest.raises(EngineError) as e:
        c.deid("x" * 2_000_000, {}, rev=1, timeout=1)  # larger than the pipe buffer
    assert e.value.code == "timeout" and time.monotonic() - t < 10
    c.close()


def test_close_during_a_call_never_restarts_the_worker(tmp_path):
    import threading

    from localdeid.app.engine import EngineError

    c = client(tmp_path)
    c.call("ping")
    errors = []
    t = threading.Thread(target=lambda: errors.append(_raises(lambda: c.deid("slow", {}, rev=1, timeout=30))))
    t.start()
    import time

    time.sleep(0.5)
    c.close()  # Quit while the call runs
    t.join(10)
    assert isinstance(errors[0], EngineError)
    time.sleep(0.5)
    assert c._proc is None


def _raises(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return exc
    return None
