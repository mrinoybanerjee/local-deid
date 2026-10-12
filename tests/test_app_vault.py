"""Vault: round trip, tamper detection, crash safety, expiry, delete everything."""

import json
import os

import pytest

from localdeid.app.vault import DAY, MemoryKeyStore, Vault, VaultError


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "LocalDeID", MemoryKeyStore(), Clock())


SID = "0123456789abcdef0123456789abcdef"


def test_roundtrip(vault):
    vault.save(SID, "K7F2", "check", {"child": "Mateo R."}, {"source": "Mateo Ruiz ...", "n": [1, 2]})
    h, body = vault.load(SID)
    assert h.ref == "K7F2" and h.state == "check" and h.meta == {"child": "Mateo R."}
    assert body == {"source": "Mateo Ruiz ...", "n": [1, 2]}
    assert vault.refs_in_use() == {"K7F2"}


def test_files_hold_no_plaintext(vault):
    vault.save(SID, "K7F2", "check", {"child": "Mateo R."}, {"source": "Mateo Ruiz secret"})
    raw = b"".join(p.read_bytes() for p in vault.root.rglob("*") if p.is_file())
    assert b"Mateo" not in raw and b"secret" not in raw


@pytest.mark.parametrize("field", ["state", "ref", "created", "body", "meta", "wrapped_key"])
def test_tampering_is_detected(vault, field):
    vault.save(SID, "K7F2", "waiting", {"child": "M"}, {"x": 1})
    p = next(vault.dir.glob("*.ldv"))
    h = json.loads(p.read_bytes())
    if field in ("body", "meta", "wrapped_key"):
        raw = bytearray(__import__("base64").b64decode(h[field]))
        raw[0] ^= 1
        h[field] = __import__("base64").b64encode(bytes(raw)).decode()
    elif field == "created":
        h[field] -= 1
    else:
        h[field] = "done" if field == "state" else "ZZZZ"
    p.write_text(json.dumps(h))
    with pytest.raises(VaultError):
        vault.load(SID)
    # listing never crashes: a damaged header or meta is skipped; a damaged body still lists
    # (History shows it; opening it reports "session-damaged")
    assert len(vault.list()) == (1 if field == "body" else 0)


def test_wrong_master_key_cannot_read(tmp_path):
    v1 = Vault(tmp_path / "v", MemoryKeyStore())
    v1.save(SID, "K7F2", "check", {}, {"x": 1})
    v2 = Vault(tmp_path / "v", MemoryKeyStore())
    with pytest.raises(VaultError):
        v2.load(SID)


def test_interrupted_write_keeps_the_old_file(vault, monkeypatch):
    vault.save(SID, "K7F2", "check", {}, {"v": 1})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        vault.save(SID, "K7F2", "waiting", {}, {"v": 2})
    monkeypatch.undo()
    assert vault.load(SID)[1] == {"v": 1}
    vault.expire()  # cleans the temp leftover
    assert not list(vault.tmp.glob("*.tmp"))


def test_state_change_keeps_created_time(vault):
    vault.save(SID, "K7F2", "check", {}, {})
    created = vault.load(SID)[0].created
    vault.clock.t += 3600
    vault.save(SID, "K7F2", "done", {}, {})
    h = vault.load(SID)[0]
    assert h.created == created and h.state == "done" and len(list(vault.dir.glob("*.ldv"))) == 1


def test_expiry_and_warnings(vault):
    for i, state in enumerate(["check", "waiting", "done"]):
        vault.save(f"{i + 1:032x}", f"RF{i}A", state, {}, {})
    vault.clock.t += 28 * DAY
    assert len(vault.expire()) == 3  # warned, none deleted
    vault.clock.t += 3 * DAY
    left = vault.expire()
    assert [s.state for s in vault.list()] == ["waiting"] and [s.state for s in left] == ["waiting"]


def test_delete_and_delete_everything(vault):
    vault.save(SID, "K7F2", "check", {}, {})
    vault.save_json("caseload", [{"first": "Mateo"}])
    vault.delete(SID)
    assert vault.list() == []
    assert vault.load_json("caseload") == [{"first": "Mateo"}]
    vault.delete_everything()
    assert not vault.root.exists() and vault.keystore.key is None


def test_bad_fields_are_rejected(vault):
    with pytest.raises(VaultError):
        vault.save("not-hex", "K7F2", "check", {}, {})
    with pytest.raises(VaultError):
        vault.save(SID, "k7f2", "check", {}, {})
    with pytest.raises(VaultError):
        vault.save(SID, "K7F2", "archived", {}, {})


def test_permissions_are_private(vault):
    vault.save(SID, "K7F2", "check", {}, {})
    assert (os.stat(vault.root).st_mode & 0o077) == 0
    assert all((os.stat(p).st_mode & 0o077) == 0 for p in vault.dir.glob("*.ldv"))


def test_short_write_keeps_the_old_session_file(tmp_path, monkeypatch):
    import os

    from localdeid.app import vault as vmod

    v = vmod.Vault(tmp_path / "v", vmod.MemoryKeyStore())
    sid = v.new_id()
    v.save(sid, "AB12", "check", {"child": "x"}, {"n": 1})
    real = os.write
    calls = {"n": 0}

    def short(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return real(fd, bytes(data[:10]))
        return 0  # the disk is full

    monkeypatch.setattr(vmod.os, "write", short)
    with pytest.raises(OSError):
        v.save(sid, "AB12", "check", {"child": "x"}, {"n": 2})
    monkeypatch.setattr(vmod.os, "write", real)
    assert v.load(sid)[1] == {"n": 1}
    assert list((tmp_path / "v" / "tmp").glob("*.tmp")) == []


def test_partial_writes_are_completed(tmp_path, monkeypatch):
    import os

    from localdeid.app import vault as vmod

    v = vmod.Vault(tmp_path / "v", vmod.MemoryKeyStore())
    real = os.write
    monkeypatch.setattr(vmod.os, "write", lambda fd, data: real(fd, bytes(data[:7])))
    sid = v.new_id()
    v.save(sid, "AB12", "check", {"child": "x"}, {"long": "y" * 5000})
    v.save_json("settings", {"a": 1})
    monkeypatch.setattr(vmod.os, "write", real)
    assert v.load(sid)[1] == {"long": "y" * 5000} and v.load_json("settings") == {"a": 1}


def test_a_leftover_file_after_a_ref_change_never_wins(tmp_path):
    import shutil

    now = [1000.0]
    v = Vault(tmp_path / "v", MemoryKeyStore(), clock=lambda: now[0])
    sid = v.new_id()
    v.save(sid, "AAAA", "waiting", {"child": "x"}, {"n": 1})
    old = next((tmp_path / "v" / "sessions").glob("*.ldv"))
    keep = tmp_path / "old.ldv"
    shutil.copy(old, keep)
    now[0] += 5
    v.save(sid, "BBBB", "check", {"child": "x"}, {"n": 2})  # renamed: new ref
    shutil.copy(keep, old)  # a crash left the old file behind
    assert [h.ref for h in v.list()] == ["BBBB"]
    h, body = v.load(sid)
    assert h.ref == "BBBB" and body == {"n": 2}
    assert not old.exists()


def test_delete_everything_reports_a_failure_and_keeps_the_key(tmp_path, monkeypatch):
    import shutil

    from localdeid.app import vault as vmod

    ks = MemoryKeyStore()
    v = Vault(tmp_path / "v", ks)
    v.save_json("caseload", "Mateo Ruiz")
    key = ks.get_or_create()
    real_rmtree = shutil.rmtree

    def fail(path):
        raise PermissionError("busy")

    monkeypatch.setattr(vmod.shutil, "rmtree", fail)
    with pytest.raises(VaultError) as e:
        v.delete_everything()
    assert str(e.value) == "delete-failed" and ks.key == key  # the files are still readable
    monkeypatch.setattr(vmod.shutil, "rmtree", real_rmtree)
    assert v.load_json("caseload") == "Mateo Ruiz"
    v.delete_everything()
    assert ks.key is None and not (tmp_path / "v").exists()
    v.delete_everything()  # already gone: still fine
