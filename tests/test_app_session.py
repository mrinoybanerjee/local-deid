"""Session manager: paste, decide, hide, undo, copy, restore by ref and by fingerprint."""

import pytest

from localdeid.app.serialize import result_to_dict, settings_from_dict
from localdeid.app.session import Manager, SessionError, view
from localdeid.app.vault import MemoryKeyStore, Vault
from localdeid.pipeline import deidentify

NOTE = ("Name: Mateo Ruiz   DOB: 03/14/2021\nMother: Lucía Ruiz (919) 555-0142\n"
        "Mateo produced /k/ in 8/10 trials at Little Sprouts Daycare. Hope helped.")


class InProcessEngine:
    """The real engine without the model, behind the same call shape as EngineClient."""

    def __init__(self):
        self.calls = 0
        self.stale = False

    def deid(self, text, settings, *, rev, keep_keys=(), manual_hides=(), ref=None, timeout=300):
        self.calls += 1
        r = deidentify(text, settings_from_dict(settings), keep_keys=keep_keys,
                       manual_hides=[tuple(h) for h in manual_hides], ref=ref)
        return {"ok": True, "rev": rev - 1 if self.stale else rev, "result": result_to_dict(r)}


class FakeClipboard:
    def __init__(self):
        self.text = None
        self.count = 0

    def write(self, text):
        self.text = text
        self.count += 1

    def read(self):
        return self.text or ""

    def change_count(self):
        return self.count


@pytest.fixture
def mgr(tmp_path):
    m = Manager(InProcessEngine(), Vault(tmp_path / "v", MemoryKeyStore()), clipboard=FakeClipboard())
    yield m
    m.close()


def test_paste_check_copy_restore_roundtrip(mgr):
    s = mgr.new(NOTE)
    v = view(s)
    assert v["child"] == "Mateo R." and v["counts"]["hidden"] > 0 and not v["blocked"]
    assert "".join(seg["t"] for seg in v["segments"]) == NOTE
    text = mgr.copy(s.id)
    assert mgr.clipboard.text == text and "Mateo" not in text and text.startswith(f"ref: {s.ref}")
    assert mgr.get(s.id).state == "waiting"
    out = mgr.restore_draft(text)
    assert out["status"] == "restored" and not out["blocked"] and out["text"] == NOTE


def test_unhide_and_undo(mgr):
    s = mgr.new(NOTE)
    row = next(r for r in view(s)["rows"] if r["value"] == "Little Sprouts Daycare")
    s = mgr.decide(s.id, row["key"], hidden=False)
    assert "Little Sprouts Daycare" in mgr.copy(s.id)
    s = mgr.undo_last(s.id)
    assert "Little Sprouts" not in mgr.copy(s.id)


def test_manual_hide(mgr):
    s = mgr.new("The family likes Bluebonnet Bakery on Fridays.")
    a = s.raw.index("Bluebonnet")
    s = mgr.hide_span(s.id, a, a + len("Bluebonnet Bakery"))
    assert "Bluebonnet" not in mgr.copy(s.id)
    with pytest.raises(SessionError):
        mgr.hide_span(s.id, 5, 99999)


def test_session_survives_a_restart(tmp_path):
    vault = Vault(tmp_path / "v", MemoryKeyStore())
    m1 = Manager(InProcessEngine(), vault, clipboard=FakeClipboard())
    s = m1.new(NOTE)
    text = m1.copy(s.id)
    m1.close()
    m2 = Manager(InProcessEngine(), vault, clipboard=FakeClipboard())
    try:
        assert m2.restore_draft(text)["text"] == NOTE
        assert [h.ref for h in m2.history()] == [s.ref]
    finally:
        m2.close()


def test_restore_without_ref_asks_to_confirm(mgr):
    s = mgr.new(NOTE)
    text = mgr.copy(s.id)
    body = "\n".join(text.splitlines()[2:])  # the AI dropped the ref and context lines
    out = mgr.restore_draft(body)
    assert out["status"] == "confirm" and out["best"] == [s.id, s.ref]
    done = mgr.restore_draft(body, sid=s.id, confirmed=True)
    assert done["status"] == "restored" and not done["blocked"]


def test_wrong_or_unknown_ref(mgr):
    mgr.copy(mgr.new(NOTE).id)
    assert mgr.restore_draft("ref: ZZZZ\nhello [CHILD_1]")["status"] == "no-session"


def test_restore_blocks_residue(mgr):
    s = mgr.new(NOTE)
    out = mgr.restore_draft(mgr.copy(s.id) + "\n[CHILD_9] said hi.")
    assert out["blocked"] and any(i["code"] == "unknown-tag" for i in out["issues"])


def test_blocked_session_cannot_copy(mgr, monkeypatch):
    s = mgr.new(NOTE)
    s.result["failures"] = [{"code": "leak", "word": "x", "start": 0, "end": 1, "detail": ""}]
    with pytest.raises(SessionError):
        mgr.copy(s.id)


def test_stale_engine_reply_is_dropped(mgr):
    s = mgr.new(NOTE)
    mgr.engine.stale = True
    row = next(r for r in view(s)["rows"] if r["value"] == "Little Sprouts Daycare")
    with pytest.raises(SessionError):
        mgr.decide(s.id, row["key"], hidden=False)  # the reply carried an old rev
    assert mgr.get(s.id).result is None  # never the old result for the new decisions
    mgr.engine.stale = False
    assert "Little Sprouts Daycare" in mgr.copy(s.id)  # Copy reruns with the Keep applied


@pytest.mark.parametrize("bad", ["", "   ", "x" * 200_001])
def test_bad_input_is_rejected(mgr, bad):
    with pytest.raises(SessionError):
        mgr.new(bad)


def test_unknown_decision_key(mgr):
    s = mgr.new(NOTE)
    with pytest.raises(SessionError):
        mgr.decide(s.id, "nope", hidden=False)


def test_refs_are_unique(mgr):
    refs = {mgr.new(f"Note {i} for Mateo Ruiz.").ref for i in range(30)}
    assert len(refs) == 30


# --------------------------------------------------------------------------- /cr phase 2 round 1


def test_each_copied_ref_keeps_its_own_mapping(mgr):
    s = mgr.new("Zorblax played with Wumbloo.")
    w = s.raw.index("Wumbloo")
    s = mgr.hide_span(s.id, w, w + 7)
    first = mgr.copy(s.id)
    s = mgr.hide_span(s.id, 0, 7)  # another hide after the copy: a new ref, the old one keeps its mapping
    second = mgr.copy(s.id)
    assert first.split()[1] != second.split()[1]
    old = mgr.restore_draft(first)
    new = mgr.restore_draft(second)
    assert old["text"] == "Zorblax played with Wumbloo." and not old["blocked"]
    assert new["text"] == "Zorblax played with Wumbloo." and not new["blocked"]
    assert old["ref"] == first.split()[1] and new["ref"] == second.split()[1]


def test_copied_refs_survive_a_reload_and_are_never_reused(tmp_path):
    vault = Vault(tmp_path / "v", MemoryKeyStore())
    m = Manager(InProcessEngine(), vault, clipboard=FakeClipboard())
    s = m.new("Zorblax played with Wumbloo.")
    w = s.raw.index("Wumbloo")
    s = m.hide_span(s.id, w, w + 7)
    first = m.copy(s.id)
    m.hide_span(s.id, 0, 7)
    m.close()
    m2 = Manager(InProcessEngine(), vault, clipboard=FakeClipboard())
    assert m2.restore_draft(first)["text"] == "Zorblax played with Wumbloo."
    for _ in range(50):
        assert m2.call(m2._fresh_ref) != first.split()[1]
    used = {r for h in vault.list() for r in h.meta["refs"]}
    assert first.split()[1] in used
    m2.close()


def test_a_hide_overrides_an_earlier_keep(mgr):
    s = mgr.new("Zorblax played today.")
    s = mgr.hide_span(s.id, 0, 7)
    key = next(r["key"] for r in view(s)["rows"] if r["value"] == "Zorblax")
    s = mgr.decide(s.id, key, hidden=False)
    assert "Zorblax" in mgr.copy(s.id)
    s = mgr.hide_span(s.id, 0, 7)
    assert "Zorblax" not in mgr.copy(s.id)
    s = mgr.undo_last(s.id)  # undo brings the Keep back
    assert "Zorblax" in mgr.copy(s.id)


class FailingOnce(InProcessEngine):
    def __init__(self):
        super().__init__()
        self.fail_next = False

    def deid(self, *a, **k):
        if self.fail_next:
            self.fail_next = False
            from localdeid.app.engine import EngineError
            raise EngineError("timeout")
        return super().deid(*a, **k)


def test_a_failed_rerun_never_leaves_an_old_result_to_copy(tmp_path):
    eng = FailingOnce()
    m = Manager(eng, Vault(tmp_path / "v", MemoryKeyStore()), clipboard=FakeClipboard())
    s = m.new("The family likes Bluebonnet Bakery on Fridays.")
    a = s.raw.index("Bluebonnet")
    eng.fail_next = True
    with pytest.raises(Exception):
        m.hide_span(s.id, a, a + len("Bluebonnet Bakery"))
    assert m.get(s.id).result is None
    text = m.copy(s.id)  # Copy reruns the checks for the current decisions first
    assert "Bluebonnet" not in text
    m.close()


def test_delete_everything_and_expiry_evict_cached_sessions(tmp_path):
    from localdeid.app.vault import DAY, VaultError

    now = [1_000_000.0]
    vault = Vault(tmp_path / "v", MemoryKeyStore(), clock=lambda: now[0])
    m = Manager(InProcessEngine(), vault, clipboard=FakeClipboard(), clock=lambda: now[0])
    s = m.new(NOTE)
    m.delete_everything()
    with pytest.raises((SessionError, VaultError)):
        m.copy(s.id)  # the cached session is gone, so it cannot be saved again under the new key
    assert vault.list() == []
    s2 = m.new(NOTE)
    m.set_state(s2.id, "done")
    now[0] += 31 * DAY
    m.expire()
    with pytest.raises((SessionError, VaultError)):
        m.get(s2.id)
    m.close()


def test_a_decision_after_a_failed_run_rebuilds_the_list_first(tmp_path):
    eng = FailingOnce()
    m = Manager(eng, Vault(tmp_path / "v", MemoryKeyStore()), clipboard=FakeClipboard())
    s = m.new(NOTE)
    row = next(r for r in view(s)["rows"] if r["value"] == "Little Sprouts Daycare")
    other = next(r for r in view(s)["rows"] if r["value"] == "Mateo")
    eng.fail_next = True
    with pytest.raises(Exception):
        m.decide(s.id, other["key"], hidden=True)
    s = m.decide(s.id, row["key"], hidden=False)  # "Try again" from the visible list works
    assert "Little Sprouts Daycare" in m.copy(s.id)
    a = s.raw.index("Hope")
    eng.fail_next = True
    with pytest.raises(Exception):
        m.decide(s.id, row["key"], hidden=True)
    s = m.hide_span(s.id, a, a + 4)
    assert "Hope" not in m.copy(s.id)
    m.close()


def test_roster_confirmation_holds_after_a_failed_run(tmp_path):
    from localdeid.app.serialize import settings_to_dict
    from localdeid.pipeline import Settings
    from localdeid.roster import Child

    eng = FailingOnce()
    settings = settings_to_dict(Settings(children=[Child("Mateo", ["Ruiz"])]))
    m = Manager(eng, Vault(tmp_path / "v", MemoryKeyStore()), clipboard=FakeClipboard(),
                settings_loader=lambda: settings)
    s = m.new(NOTE)
    key = next(r["key"] for r in view(s)["rows"] if r["value"] == "Mateo")
    eng.fail_next = True
    with pytest.raises(Exception):
        m.hide_span(s.id, 0, 4)  # the run fails: the result is dropped
    with pytest.raises(SessionError) as e:
        m.decide(s.id, key, hidden=False)  # rebuilt first, then still asks
    assert e.value.code == "confirm-roster" and e.value.detail == "Mateo"
    assert "Mateo" not in m.copy(s.id)
    s = m.decide(s.id, key, hidden=False, confirmed=True)
    assert "Mateo" in m.copy(s.id)
    m.close()


def test_ref_less_draft_ranks_every_copied_mapping(mgr):
    s = mgr.new("Zorblax played with Wumbloo.")
    w = s.raw.index("Wumbloo")
    s = mgr.hide_span(s.id, w, w + 7)
    first = mgr.copy(s.id)  # PERSON_1 = Wumbloo
    first_ref = first.split()[1]
    s = mgr.hide_span(s.id, 0, 7)
    mgr.copy(s.id)  # PERSON_1 = Zorblax now
    body = "\n".join(first.splitlines()[2:])  # the AI dropped the ref line
    out = mgr.restore_draft(body)
    assert out["status"] == "confirm"
    assert {c["ref"] for c in out["candidates"]} >= {first_ref}
    with pytest.raises(SessionError) as e:
        mgr.restore_draft(body, sid=s.id, confirmed=True)  # copies differ: which one?
    assert e.value.code == "which-copy"
    done = mgr.restore_draft(body, sid=s.id, confirmed=True, ref=first_ref)
    assert done["text"] == "Zorblax played with Wumbloo." and not done["blocked"]


def test_a_failed_run_is_saved_so_a_restart_cannot_copy_the_old_result(tmp_path):
    eng = FailingOnce()
    vault = Vault(tmp_path / "v", MemoryKeyStore())
    m = Manager(eng, vault, clipboard=FakeClipboard())
    s = m.new("The family likes Bluebonnet Bakery on Fridays.")
    a = s.raw.index("Bluebonnet")
    eng.fail_next = True
    with pytest.raises(Exception):
        m.hide_span(s.id, a, a + len("Bluebonnet Bakery"))
    m.close()
    m2 = Manager(InProcessEngine(), vault, clipboard=FakeClipboard())  # the app restarted
    assert m2.get(s.id).result is None
    assert "Bluebonnet" not in m2.copy(s.id)
    assert m2.history()[0].meta["child"] == ""  # no child in this note; the label survives a rerun
    m2.close()


def test_copies_of_one_note_do_not_crowd_out_another_child(mgr):
    a = mgr.new("Zorblax said ball and cup at snack today.")
    a = mgr.hide_span(a.id, 0, 7)
    mgr.copy(a.id)
    b = mgr.new("Quillon said ball and cup at snack today.")
    b = mgr.hide_span(b.id, 0, 7)
    for _ in range(4):
        mgr.copy(b.id)  # the same mapping four times
    out = mgr.restore_draft("[PERSON_1] said ball and cup at snack today. Great work.")
    ids = [c["id"] for c in out["candidates"]]
    assert a.id in ids and b.id in ids and len(ids) == len(set(ids))


def test_a_session_without_copies_restores_from_its_result(mgr):
    s = mgr.new("Zorblax said ball today.")
    s = mgr.hide_span(s.id, 0, 7)
    text = mgr.copy(s.id)
    sess = mgr.get(s.id)
    sess.copies.clear()  # as saved before copies were kept
    out = mgr.restore_draft("\n".join(text.splitlines()[2:]))
    assert out["status"] == "confirm" and out["candidates"][0]["ref"] == s.ref
    done = mgr.restore_draft("\n".join(text.splitlines()[2:]), sid=s.id, confirmed=True, ref=s.ref)
    assert done["text"] == "Zorblax said ball today."


def test_every_distinct_copy_stays_selectable_and_same_values_dedupe(mgr):
    s = mgr.new("Name: Sol\nSol said sol.")
    first = mgr.copy(s.id)
    a = s.raw.rindex("sol")
    s = mgr.hide_span(s.id, a, a + 3)  # the lowercase word too: same canonical value, different instances
    second = mgr.copy(s.id)
    for _ in range(4):
        other = mgr.new(f"Name: Zorblax\nZorblax said hi {_}.")
        mgr.copy(other.id)
    body = "\n".join(second.splitlines()[2:])
    out = mgr.restore_draft(body)
    mine = next(c for c in out["candidates"] if c["id"] == s.id)
    assert {c["ref"] for c in mine["copies"]} == {first.split()[1], second.split()[1]}
    done = mgr.restore_draft(body, sid=s.id, confirmed=True, ref=second.split()[1])
    assert done["text"] == "Name: Sol\nSol said sol." and not done["blocked"]


def test_each_copy_carries_its_own_child_label(mgr):
    from localdeid.app.serialize import settings_to_dict
    from localdeid.pipeline import Settings

    s = mgr.new("Name: Mateo Ruiz\nSibling: Sofía Ruiz\nMateo and Sofía played.")
    first = mgr.copy(s.id)
    key = next(r["key"] for r in view(s)["rows"] if r["value"] == "Mateo")
    s = mgr.decide(s.id, key, hidden=False)
    second = mgr.copy(s.id)
    out = mgr.restore_draft("\n".join(first.splitlines()[2:]))
    mine = next(c for c in out["candidates"] if c["id"] == s.id)
    labels = {k["ref"]: k["child"] for k in mine["copies"]}
    assert labels[first.split()[1]].startswith("Mateo")
    assert first.split()[1] in labels and second.split()[1] in labels


def test_every_waiting_session_is_ranked_for_a_ref_less_draft(mgr):
    old = mgr.new("Zorblax built a tower of nine red blocks with Quillon.")
    old = mgr.hide_span(old.id, 0, 7)
    first = mgr.copy(old.id)
    for i in range(51):  # newer waiting sessions with other notes
        s = mgr.new(f"Note {i}: Ana said ball and cup at snack.")
        mgr.copy(s.id)
    out = mgr.restore_draft("\n".join(first.splitlines()[2:]))
    assert out["status"] == "confirm" and out["candidates"][0]["id"] == old.id
    assert old.id in mgr._open  # it was open already; the others were only peeked
    assert len(mgr._open) <= 53


def test_close_finishes_queued_work_and_refuses_new_work(tmp_path):
    import threading

    m = Manager(InProcessEngine(), Vault(tmp_path / "v", MemoryKeyStore()), clipboard=FakeClipboard())
    gate = threading.Event()
    m.submit(gate.wait, 10)
    late = m.submit(lambda: m.clipboard.write("queued copy"))
    threading.Timer(0.3, gate.set).start()
    m.close(timeout=10)  # returns only after the queued write ran
    assert late.done() and m.clipboard.text == "queued copy"
    with pytest.raises(SessionError) as e:
        m.new(NOTE)
    assert e.value.code == "closed"


def test_child_label_uses_only_the_childs_own_surname():
    from localdeid.app.session import child_label

    src = "Mother: Lucía García\nName: Mateo Ruiz"
    r = {"source": src, "canonical": {"CHILD_1": "Mateo", "SURNAME_1": "García", "SURNAME_2": "Ruiz",
                                      "PARENT_1": "Lucía"},
         "items": [{"kind": "PARENT", "hidden": True, "tag": "PARENT_1", "start": 8, "end": 13},
                   {"kind": "SURNAME", "hidden": True, "tag": "SURNAME_1", "start": 14, "end": 20},
                   {"kind": "CHILD", "hidden": True, "tag": "CHILD_1", "start": 27, "end": 32},
                   {"kind": "SURNAME", "hidden": True, "tag": "SURNAME_2", "start": 33, "end": 37}]}
    assert child_label(r) == "Mateo R."
    r["items"] = r["items"][:3]  # no surname after the child's name: no initial
    assert child_label(r) == "Mateo"


def test_a_copy_aborted_at_the_last_check_leaves_no_copy_record(tmp_path):
    vault = Vault(tmp_path / "v", MemoryKeyStore())
    m = Manager(InProcessEngine(), vault, clipboard=FakeClipboard())
    s = m.new("Call (919) 555-0142 on 3/14/2026.")
    cc = m.clipboard.change_count()
    real_save = m._save
    calls = {"n": 0}

    def save_then_user_copies(sess):
        real_save(sess)
        calls["n"] += 1
        if calls["n"] == 1:
            m.clipboard.write("The clinician copied this meanwhile")

    m._save = save_then_user_copies
    with pytest.raises(SessionError) as e:
        m.copy(s.id, None, cc)
    assert e.value.code == "clipboard-changed"
    m._save = real_save
    assert m.clipboard.text == "The clinician copied this meanwhile"
    h = vault.list()[0]
    assert h.state == "check" and h.meta["refs"] == [s.ref] and m.get(s.id).copies == {}
    m.close()
