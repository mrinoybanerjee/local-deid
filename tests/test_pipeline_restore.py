"""End-to-end engine tests (no ML model) and restore tests (SPEC 3, 4.4, 4.5)."""

import random
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from localdeid.pipeline import (Item, Settings, _mask_tags, deidentify, rebuild, render,
                                run_checks)
from localdeid.restore import (best_session, copy_block, context_line, find_ref, language_of,
                               restore)
from localdeid.roster import Child, Related

NOTE = """Name: Mateo Ramírez        DOB: 03/14/2021
Parent/Guardian: Lucía Rodríguez | Phone: (919) 555-0142
Daycare: Little Acorns Learning Center
Date of service: March 3, 2026     CPT 92507

Mateo (4;2) attended a 45-minute session at Little Acorns Learning Center. Given moderate
verbal cues, Mateo produced /k/ in CV words in 7/10 trials (70%). His mother, Lucía, reported
that his sister Sol is "copying" him at home. Su mamá dijo que el 5 de marzo Mateo dijo "sol"
tres veces. Ms. Whitfield's class uses PECS. Next visit 3/10/2026 at 9:30 AM.
"""

KIDS = [Child("Mateo", ["Ramírez"], [], [Related("Lucía", "mother", ["Rodríguez"]),
                                          Related("Sol", "sister")], [], ["sol"])]


@pytest.fixture(scope="module")
def result():
    return deidentify(NOTE, Settings(children=KIDS), ref="K7F2")


def test_no_failures_and_integrity(result):
    assert result.failures == []
    assert rebuild(result.output, result.source, result.instances) == result.source


def test_identifiers_hidden(result):
    out = result.output
    for value in ["Mateo", "Ramírez", "Lucía", "Rodríguez", "919", "555-0142", "03/14/2021",
                  "Little Acorns", "Whitfield"]:
        assert value not in out, value
    assert "[CHILD_1]" in out and "[SURNAME_1]" in out and "[PARENT_1]" in out


def test_clinical_content_kept(result):
    out = result.output
    for value in ["4;2", "/k/", "7/10 trials", "70%", "CPT 92507", "PECS", '"copying"', "45-minute"]:
        assert value in out, value


def test_year_kept_and_dates_tagged(result):
    assert "[DATE_" in result.output and ", 2026" in result.output


def test_same_value_same_tag(result):
    child_tags = {it.tag for it in result.items if result.source[it.start:it.end].lower() == "mateo"}
    assert child_tags == {"CHILD_1"}


def test_quoted_target_word_named_like_sister_stays_visible(result):
    # DECISIONS 21: a lowercase roster hit inside quotes is the stimulus word, not the sister.
    assert not [it for it in result.items if result.source[it.start:it.end] == "sol"]
    assert 'dijo "sol"' in result.output
    sister = [it for it in result.items if result.source[it.start:it.end] == "Sol"]
    assert sister and all(it.hidden for it in sister)


def test_keep_key_unhides():
    r1 = deidentify(NOTE, Settings(children=KIDS), ref="K7F2")
    key = next(it.key for it in r1.items if r1.source[it.start:it.end] == "Little Acorns Learning Center")
    r2 = deidentify(NOTE, Settings(children=KIDS), keep_keys=[key], ref="K7F2")
    assert "Little Acorns Learning Center" in r2.output and r2.failures == []


def test_clinician_kept_and_employer_tagged():
    text = "Evaluator: Valeria López. Speech by Bright Path Therapy. Valeria saw the child."
    r = deidentify(text, Settings(keep_names=["Valeria López"], employer_names=["Bright Path Therapy"]))
    assert "Valeria López" in r.output and "[EMPLOYER]" in r.output


def test_tag_like_source_text_is_escaped_and_restored():
    text = "The template said [CHILD_1] and (PARENT 2) and NIÑO_3 — Ana Ruiz attended."
    r = deidentify(text, Settings(children=[Child("Ana", ["Ruiz"])]))
    assert r.failures == []
    assert "[CHILD_1] and" not in r.output.split("said", 1)[1][:14]
    back = restore(copy_block(r), r)
    assert not back.blocked and back.text == r.source


def test_nfc_change_reported():
    r = deidentify("José vino.", Settings())
    assert r.nfc_changed and r.source == "José vino."


def test_empty_and_whitespace():
    for t in ["", "   \n\t"]:
        r = deidentify(t)
        assert r.output == t and r.failures == []


# --------------------------------------------------------------------------- checks


def test_leak_check_catches_unhidden_mention():
    text = "Ana Ruiz came. Later Ana left."
    items = [Item(0, 3, "CHILD_1", "CHILD", "NAME:ana", True, True, ("roster",), ())]
    out, inst, _ = render(text, items)
    fails = run_checks(text, items, [], out, inst, Settings())
    assert any(f.code == "leak" and f.word == "Ana" for f in fails)


def test_protected_check_catches_hidden_clinical_token():
    from localdeid.protect import Protected
    text = "PLS-5 total"
    items = [Item(0, 5, "PERSON_1", "PERSON", "NAME:pls-5", True, True, ("model",), ())]
    out, inst, _ = render(text, items)
    fails = run_checks(text, items, [Protected(0, 5, "term")], out, inst, Settings())
    assert any(f.code == "protected" for f in fails)


def test_integrity_check_catches_tampered_output():
    text = "Ana came."
    items = [Item(0, 3, "CHILD_1", "CHILD", "NAME:ana", True, True, ("roster",), ())]
    out, inst, _ = render(text, items)
    fails = run_checks(text, items, [], out.replace("came", "come"), inst, Settings())
    assert any(f.code == "integrity" for f in fails)


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=80), st.lists(st.tuples(st.integers(0, 80), st.integers(1, 10)), max_size=6))
def test_render_rebuild_roundtrip(text, raw):
    n = len(text)
    items, last = [], 0
    for s, ln in sorted(raw):
        s = max(s, last)
        e = min(n, s + ln)
        if s >= e:
            continue
        items.append(Item(s, e, f"PERSON_{len(items) + 1}", "PERSON", f"k{len(items)}", True, True, (), ()))
        last = e
    out, inst, _ = render(text, items)
    assert rebuild(out, text, inst) == text


# --------------------------------------------------------------------------- restore


def test_restore_unedited_copy_is_exact(result):
    back = restore(copy_block(result), result)
    assert not back.blocked
    assert back.text == result.source


def test_copy_block_lines(result):
    block = copy_block(result).split("\n")
    assert block[0] == "ref: K7F2 — keep this line and all [TAGS] exactly as written."
    assert block[1].startswith("[CHILD_1]: 4;2. Language: ")
    assert find_ref(copy_block(result)) == "K7F2"



@pytest.mark.parametrize("name,pronoun", [("Mateo", "She"), ("Valentina", "He"), ("Mateo", "")])
def test_context_does_not_infer_gender_from_name(name, pronoun):
    text = f"Name: {name} Ruiz\nAge: 4;2. {pronoun} practiced /k/."
    r = deidentify(text, Settings(children=[Child(name, ["Ruiz"])]))
    block = copy_block(r)
    context = block.splitlines()[1]
    assert "boy" not in context and "girl" not in context
    assert "4;2" in context and "Language:" in context
    assert restore(block, r).text == text


def test_explicit_gender_context_restores_after_serialization_and_default_copy():
    from localdeid.app.serialize import result_from_dict, result_to_dict

    text = "Name: Mateo Ruiz\nShe practiced /k/."
    r = deidentify(text, Settings(children=[Child("Mateo", ["Ruiz"])]))
    issued = copy_block(r, gender="girl")
    assert "[CHILD_1]: girl." in issued.splitlines()[1]
    reloaded = result_from_dict(result_to_dict(r))
    default = copy_block(reloaded)
    assert "boy" not in default.splitlines()[1] and "girl" not in default.splitlines()[1]
    for block in (issued, default):
        restored = restore(block, reloaded)
        assert not restored.blocked and restored.text == text


def test_language_detection():
    assert language_of("The child and the mother were in the room.") == "English"
    assert language_of("El niño y la mamá estaban en la casa con su hermano.") == "Spanish"
    assert language_of("The child y la mamá said that el niño and the dog were en la casa.") == "Spanish/English"


def test_restore_tolerant_tags(result):
    draft = ("ref: K7F2\n[child 1] made progress; [Child_1]'s mom, [MADRE_1], and (CHILD-1) "
             "practiced. NIÑO_1 did well. [SURNAME_1] family. [PARENT1] agreed.")
    back = restore(draft, result)
    assert not back.blocked, back.issues
    assert "Mateo made progress; Mateo's mom, Lucía" in back.text
    assert "NIÑO" not in back.text and "Ramírez family" in back.text


def test_restore_blocks_wrong_ref_unknown_tag_fragment_and_missing_ref(result):
    assert restore("ref: ZZZZ\n[CHILD_1]", result).issues[0].code == "wrong-ref"
    assert restore("[CHILD_1] did well", result).issues[0].code == "no-session"
    assert any(i.code == "unknown-tag" for i in restore("ref: K7F2\n[CHILD_9] ok", result).issues)
    assert any(i.code == "fragment" for i in restore("ref: K7F2\n[CHILD ok", result).issues)
    assert any(i.code == "fragment" for i in restore("ref: K7F2\n[REDACTED] ok", result).issues)
    assert restore("ref: K7F2\n[CHILD_9] ok", result).blocked


def test_restore_warns_missing_tags_and_new_numbers(result):
    back = restore("ref: K7F2\n[CHILD_1] got 9/10 trials today.", result)
    assert not back.blocked
    codes = {(i.code, i.text) for i in back.issues}
    assert ("new-number", "90%") in codes  # scores are compared as canonical percentages
    assert any(c == "missing-tag" for c, _ in codes)
    # numbers from the source, written differently, do not warn
    back = restore("ref: K7F2\n[CHILD_1] got seven out of ten (70%).", result)
    assert not any(i.code == "new-number" for i in back.issues)


def test_restore_after_confirmed_fingerprint(result):
    back = restore("[CHILD_1] produced /k/ in 7/10 trials.", result, ref_confirmed=True)
    assert not back.blocked and "Mateo produced" in back.text


def test_fingerprint_picks_session_or_asks():
    a = deidentify(NOTE, Settings(children=KIDS), ref="AAAA")
    other = NOTE.replace("7/10", "3/9").replace("70%", "33%").replace("copying", "hiding")
    b = deidentify(other.replace("Mateo", "Diego"), Settings(children=[Child("Diego")]), ref="BBBB")
    draft = "[CHILD_1] produced /k/ in 7/10 trials (70%) while copying."
    assert best_session(draft, [a, b]) is a
    assert best_session(draft, [a, a]) is None  # two equal scores: ask


@settings(max_examples=150, deadline=None)
@given(st.randoms(use_true_random=False))
def test_fuzz_llm_style_tag_edits(rnd: random.Random):
    """LLM edits to tags restore to canonical values; corrupted tags always block."""
    r = deidentify(NOTE, Settings(children=KIDS), ref="K7F2")
    body = r.output
    tags = sorted({inst.tag for inst in r.instances if inst.tag})

    def variant(tag):
        t, _, n = tag.rpartition("_")
        return rnd.choice([f"[{tag}]", f"[{tag.lower()}]", f"[{t} {n}]", f"[ {tag} ]", f"[{t}-{n}]",
                           f"[{t.title()}_{n}]", f"[{t}{n}]", f"({tag})"])

    edited = re.sub(r"\[([A-Z_]+_\d+)\]", lambda m: variant(m.group(1)), body)
    back = restore("ref: K7F2\n" + edited, r)
    assert not back.blocked, back.issues
    for tag in tags:
        assert f"[{tag}]" not in back.text
    # corrupt one tag at random: must block (unknown) or leave a fragment that blocks
    target = rnd.choice(tags)
    t, _, n = target.rpartition("_")
    broken = body.replace(f"[{target}]", rnd.choice([f"[{t}_99]", f"[{t}", f"[XYZ_{n}]", "[REDACTED]"]), 1)
    if broken != body:
        assert restore("ref: K7F2\n" + broken, r).blocked


def test_mask_tags():
    from localdeid.pipeline import _MASK_FILL, Instance

    src = "a Mateo b [CHILD_1] c [X] d"
    out = "a [CHILD_1] b ⟦1⟧ c [X] d"
    inst = [Instance("CHILD_1", 2, 7, "Mateo"), Instance("", 10, 19, "[CHILD_1]", 1)]
    assert _mask_tags(out, inst) == "a " + _MASK_FILL * 9 + " b " + _MASK_FILL * 3 + " c [X] d"
    with pytest.raises(ValueError):
        _mask_tags("a Mateo b", inst)


def test_checkbox_rows_do_not_join_into_names():
    r = deidentify("Setting:\n[X] Home   [X] Daycare   [ ] Community\n")
    assert r.output == r.source and not r.failures


def test_context_line_without_child():
    r = deidentify("Session went well.")
    assert context_line(r) == "Language: English."
