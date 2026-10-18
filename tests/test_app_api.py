"""JS bridge: validation, plain errors, caseload, settings, hotkeys, never raises."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from localdeid.app.api import Api
from localdeid.app.caseload import CaseloadError, parse
from localdeid.app.session import Manager
from localdeid.app.vault import MemoryKeyStore, Vault

from test_app_session import NOTE, FakeClipboard, InProcessEngine


@pytest.fixture
def api(tmp_path):
    vault = Vault(tmp_path / "v", MemoryKeyStore())
    shown, notes = [], []
    holder = {}
    mgr = Manager(InProcessEngine(), vault, clipboard=FakeClipboard(),
                  settings_loader=lambda: holder["api"]._engine_settings())
    a = Api(mgr, vault, clipboard=mgr.clipboard, on_show=lambda v, d: shown.append(v), notify=notes.append)
    holder["api"] = a
    a.shown, a.notes = shown, notes
    yield a
    mgr.close()


def test_caseload_parse_and_errors():
    kids = parse("# my kids\nMateo Ruiz López; nickname Teo; mom Lucía Ruiz; sister Sol; school Little Sprouts; "
                 "targets sol, pan\nAna\n")
    assert kids[0].first == "Mateo" and kids[0].surnames == ["Ruiz López"] and kids[0].nicknames == ["Teo"]
    assert [(r.name, r.relation, r.surnames) for r in kids[0].related] == [("Lucía", "mother", ["Ruiz"]),
                                                                            ("Sol", "sister", [])]
    assert kids[0].schools == ["Little Sprouts"] and kids[0].target_words == ["sol", "pan"]
    assert kids[1].first == "Ana"
    with pytest.raises(CaseloadError) as e:
        parse("Mateo; phone 9195550142")
    assert e.value.line == 1
    with pytest.raises(CaseloadError):
        parse("Mateo; cousin Ana")


def test_caseload_drives_detection(api):
    assert api.set_caseload("Quillon Abernathy; sister Wren")["ok"]
    out = api.paste("Quillon played with Wren today.")
    assert out["ok"]
    hidden = {r["value"] for r in out["view"]["rows"] if r["hidden"]}
    assert {"Quillon", "Wren"} <= hidden


def test_caseload_error_message_names_the_line(api):
    out = api.set_caseload("Mateo\nAna; phone 9195550142")
    assert not out["ok"] and out["message"].startswith("Line 2")


def test_settings_validation(api):
    assert not api.set_settings({"template_en": "no placeholder"})["ok"]
    out = api.set_settings({"template_en": "Write a SOAP note:\n{note}", "keep_names": "Valeria López\n",
                            "employer_names": ["Carolina Speech"], "bogus": 1})
    assert out["ok"] and out["settings"]["keep_names"] == ["Valeria López"] and "bogus" not in out["settings"]


def test_copy_with_template(api):
    api.set_settings({"template_en": "Write a SOAP note:\n{note}"})
    v = api.paste(NOTE)["view"]
    assert api.copy(v["id"], True)["ok"]
    assert api._m.clipboard.text.startswith("Write a SOAP note:\nref: ")


def test_hotkey_with_check_items_opens_check(api):
    api.set_caseload("Mateo Ruiz; sister Rosa")
    api._m.clipboard.text = "Rosa helped Mateo with the blocks."  # a name that is also a word: a check item
    api._clipboard.read = lambda: api._m.clipboard.text
    out = api.hotkey_deidentify()
    assert out["ok"] and not out["copied"] and api.shown == ["check"]


def test_hotkey_without_check_items_copies_straight_back(api):
    api._m.clipboard.text = "Call (919) 555-0142 on 3/14/2026."
    api._clipboard.read = lambda: api._m.clipboard.text
    out = api.hotkey_deidentify()
    assert out["ok"] and out["copied"] and api.notes and api._m.clipboard.text.startswith("ref: ")
    r = api.hotkey_restore()
    assert r["ok"] and r["restore"]["status"] == "restored"


def test_restore_flow_with_confirm(api):
    v = api.paste(NOTE)["view"]
    api.copy(v["id"])
    body = "\n".join(api._m.clipboard.text.splitlines()[2:])
    out = api.restore(body)
    assert out["restore"]["status"] == "confirm"
    best_id, best_ref = out["restore"]["best"]
    done = api.restore_confirm(best_id, best_ref, out["restore"]["token"])
    assert done["ok"] and done["restore"]["text"] == NOTE
    assert api.copy_restored(v["id"])["ok"] is False  # no token: not the restore on screen
    assert api.copy_restored(v["id"], done["restore"]["rtoken"])["ok"] and api._m.clipboard.text == NOTE


def test_errors_are_plain(api):
    out = api.paste("")
    assert out == {"ok": False, "error": "empty-text", "message": out["message"]} and "empty" in out["message"]
    assert api.open("../../etc/passwd")["error"] == "no-such-session"
    assert not api.delete_everything("yes")["ok"]


def test_delete_everything_then_keep_working(api):
    api.paste(NOTE)
    assert api.delete_everything("DELETE")["ok"]
    assert api.history()["rows"] == []
    assert api.paste(NOTE)["ok"]


junk = st.recursive(st.none() | st.booleans() | st.integers() | st.text(max_size=20),
                    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=5), c, max_size=3), max_leaves=6)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(args=st.lists(junk, max_size=3))
def test_every_method_survives_junk(api, args):
    for name in ("paste", "open", "decide", "hide_span", "undo", "copy", "set_state", "delete", "restore",
                 "restore_confirm", "copy_restored", "set_caseload", "set_settings", "delete_everything"):
        fn = getattr(api, name)
        try:
            out = fn(*args)
        except TypeError:
            continue  # wrong arity: pywebview reports it to JS; nothing ran
        assert isinstance(out, dict) and "ok" in out


def test_lost_key_stops_checks_with_a_plain_message_until_delete_everything(api):
    assert api.set_caseload("Mateo Ruiz")["ok"] and api.set_settings({"keep_names": ["Valeria"]})["ok"]
    api._vault.keystore.delete()  # the Keychain item is gone: the saved files cannot be read
    api._vault._master = None
    out = api.paste(NOTE)
    assert out["ok"] is False and "Delete everything" in out["message"]  # fail closed: no check without the caseload
    assert api.state()["ok"] is False and api.get_caseload()["ok"] is False
    assert api.delete_everything("DELETE")["ok"]
    assert api.paste(NOTE)["ok"] and api.state()["first_run"] is True


def test_keychain_errors_say_unlock():
    from localdeid.app.api import _msg

    assert "Unlock" in _msg("keychain-read--25308")


def test_keeping_a_caseload_name_needs_confirmation(api):
    assert api.set_caseload("Mateo Ruiz")["ok"]
    v = api.paste(NOTE)["view"]
    key = next(r["key"] for r in v["rows"] if r["value"] == "Mateo")
    out = api.decide(v["id"], key, False)
    assert out["ok"] is False and out["error"] == "confirm-roster" and "caseload" in out["message"]
    assert "Mateo" not in api._m.copy(v["id"])
    out = api.decide(v["id"], key, False, True)
    assert out["ok"] and "Mateo" in api._m.copy(v["id"])
    other = next(r["key"] for r in out["view"]["rows"] if r["value"] == "Little Sprouts Daycare")
    assert api.decide(v["id"], other, False)["ok"]  # not on the caseload: no question


def test_caseload_first_name_part_and_relative_surnames():
    from localdeid.pipeline import Settings, deidentify

    kids = parse("Juan Pablo Ruiz; first Juan Pablo; mom Zerelda Wumbloo Zorblax")
    assert kids[0].first == "Juan Pablo" and kids[0].surnames == ["Ruiz"]
    r = deidentify("pablito worked on /k/. zorblax called about progress. Wumbloo came too.",
                   Settings(children=kids))
    assert "pablito" not in r.output and "zorblax" not in r.output and "Wumbloo" not in r.output
    assert "/k/" in r.output
    with pytest.raises(CaseloadError):
        parse("Juan Pablo Ruiz; first Pedro")


def test_delete_everything_drops_open_sessions(api):
    v = api.paste(NOTE)["view"]
    assert api.delete_everything("DELETE")["ok"]
    assert api.copy(v["id"])["ok"] is False  # not saved again under the new key
    assert api.history()["rows"] == []


def test_restore_confirmation_is_bound_to_its_draft(api):
    a = api.paste("Zorblax read with Wumbloo today.")["view"]
    api.hide_span(a["id"], 0, 7)
    api.hide_span(a["id"], 18, 25)
    api.copy(a["id"])
    first = "\n".join(api._m.clipboard.text.splitlines()[2:])
    b = api.paste("Quillon read alone today.")["view"]
    api.hide_span(b["id"], 0, 7)
    api.copy(b["id"])
    second = "\n".join(api._m.clipboard.text.splitlines()[2:])
    one = api.restore(first)["restore"]
    two = api.restore(second)["restore"]  # a second question while the first is still open
    assert one["status"] == two["status"] == "confirm" and one["token"] != two["token"]
    a_ref = api._m.get(a["id"]).ref
    done = api.restore_confirm(a["id"], a_ref, one["token"])
    assert done["ok"] and done["restore"]["text"] == "Zorblax read with Wumbloo today."
    assert api.restore_confirm(a["id"], a_ref, "nope")["error"] == "draft-expired"


def test_copy_restored_copies_exactly_the_restore_it_was_shown_for(api):
    v = api.paste("Zorblax said ball today.")["view"]
    api.hide_span(v["id"], 0, 7)
    api.copy(v["id"])
    text = api._m.clipboard.text
    one = api.restore(text.replace("ball", "cup"))["restore"]
    two = api.restore(text)["restore"]  # a second restore of the same note finished later
    assert api.copy_restored(v["id"], one["rtoken"])["ok"] and "cup" in api._m.clipboard.text
    assert api.copy_restored(v["id"], two["rtoken"])["ok"] and "ball" in api._m.clipboard.text
    assert api.copy_restored(v["id"], "unknown")["error"] == "restore-changed"


def test_a_caseload_change_makes_old_results_stale(api):
    v = api.paste(NOTE)["view"]
    key = next(r["key"] for r in v["rows"] if r["value"] == "Mateo")
    assert api.set_caseload("Mateo Ruiz")["ok"]
    out = api.decide(v["id"], key, False)  # the result is rebuilt with the caseload first
    assert out["ok"] is False and out["error"] == "confirm-roster"
    assert "Mateo" not in api._m.copy(v["id"])


def test_the_bridge_refuses_a_foreign_page_and_hides_settings(api):
    assert not hasattr(api, "settings") and not hasattr(api, "engine_settings")
    public = [n for n in dir(api) if not n.startswith("_")]
    assert "_engine_settings" not in public and "_clipboard_text" not in public
    api._set_guard(lambda: False)
    out = api.history()
    assert out["ok"] is False and out["error"] == "foreign-page"
    assert api.get_caseload()["ok"] is False
    api._set_guard(lambda: True)
    assert api.history()["ok"]


def test_a_keep_made_before_a_name_joined_the_caseload_needs_confirming(api):
    v = api.paste("Name: Mateo Ruiz\nMateo said ball.")["view"]
    key = next(r["key"] for r in v["rows"] if r["value"] == "Mateo")
    assert api.decide(v["id"], key, False)["ok"]  # not on the caseload yet: no question
    assert api.set_caseload("Mateo Ruiz")["ok"]
    assert "Mateo" not in api._m.copy(v["id"])  # hidden again until confirmed
    out = api.decide(v["id"], key, False)
    assert out["error"] == "confirm-roster"
    assert api.decide(v["id"], key, False, True)["ok"] and "Mateo" in api._m.copy(v["id"])


def test_caseload_saves_wait_for_the_session_queue(api):
    import threading

    gate = threading.Event()
    api._m.submit(gate.wait, 10)  # an engine run in progress
    done = threading.Event()
    threading.Thread(target=lambda: (api.set_caseload("Zorblax"), done.set()), daemon=True).start()
    assert not done.wait(0.5)  # the save waits its turn
    gate.set()
    assert done.wait(5)


def test_two_settings_saves_during_a_run_keep_both_updates(api):
    import threading

    gate = threading.Event()
    api._m.submit(gate.wait, 10)  # an engine run in progress
    t1 = threading.Thread(target=api.set_settings, args=({"first_run_done": True},), daemon=True)
    t2 = threading.Thread(target=api.set_settings, args=({"ai_tip": "Temporary Chat."},), daemon=True)
    t1.start()
    t2.start()
    gate.set()
    t1.join(5)
    t2.join(5)
    s = api.get_settings()["settings"]
    assert s["first_run_done"] is True and s["ai_tip"] == "Temporary Chat."


def test_hotkey_keeps_a_newer_clipboard_and_opens_check(api):
    cb = api._m.clipboard
    cb.write("Called (919) 555-0142 on 03/14/2025 about the visit.")

    real_new = api._m.new

    def new_then_user_copies(text):
        s = real_new(text)
        cb.write("Something the clinician copied meanwhile")  # newer clipboard content
        return s

    api._m.new = new_then_user_copies
    out = api.hotkey_deidentify()
    assert out["ok"] and out["copied"] is False
    assert cb.text == "Something the clinician copied meanwhile" and api.shown[-1] == "check"


def test_reads_wait_for_a_queued_save(api):
    import threading

    gate = threading.Event()
    api._m.submit(gate.wait, 10)  # an engine run in progress
    saver = threading.Thread(target=api.set_caseload, args=("Zorblax Quill",), daemon=True)
    saver.start()
    import time

    time.sleep(0.3)  # the save is queued first
    got = {}
    reader = threading.Thread(target=lambda: got.update(api.get_caseload()), daemon=True)
    reader.start()
    gate.set()
    saver.join(5)
    reader.join(5)
    assert got["text"] == "Zorblax Quill"  # the read came after the save, not before it


def test_delete_everything_discards_pending_restore_drafts(api):
    v = api.paste(NOTE)["view"]
    api.copy(v["id"])
    body = "\n".join(api._m.clipboard.text.splitlines()[2:])
    pending = api.restore(body)["restore"]
    assert pending["status"] == "confirm" and api._drafts
    assert api.delete_everything("DELETE")["ok"]
    assert api._drafts == {}
    assert api.restore_confirm(*pending["best"], pending["token"])["error"] == "draft-expired"


def test_restore_finishing_after_delete_cannot_cache_old_text(api):
    original = api._m.restore_draft

    def restore_then_delete(text):
        result = original(text)
        api.delete_everything("DELETE")
        return result

    v = api.paste(NOTE)["view"]
    api.copy(v["id"])
    body = "\n".join(api._m.clipboard.text.splitlines()[2:])
    api._m.restore_draft = restore_then_delete
    assert api.restore(body)["error"] == "draft-expired"
    assert api._drafts == {}
