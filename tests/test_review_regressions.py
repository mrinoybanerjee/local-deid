"""Regression tests for the GPT-6.1 Sol review of commit f00ffe1 (2026-10-10).

Each test reproduces the reviewer's failing input."""

import pytest

from localdeid import fields, rules
from localdeid.evaldata import parse
from localdeid.pipeline import Settings, deidentify
from localdeid.restore import copy_block, restore
from localdeid.roster import Child, Related
from localdeid.gliner_onnx import OrgSpan


def hidden_texts(r):
    return {r.source[it.start:it.end] for it in r.items if it.hidden}


def test_word_name_decisions_are_per_mention():
    # A lowercase word-name in running prose is a per-mention check item (SPEC 4.3).
    text = "Practiced sol today. His sister Sol watched."
    s = Settings(children=[Child("Mateo", related=[Related("Sol", "sister")], target_words=["sol"])])
    r = deidentify(text, s)
    target = next(it for it in r.items if r.source[it.start:it.end] == "sol")
    assert target.hidden and not target.sure
    r2 = deidentify(text, s, keep_keys=[target.decision_key])
    assert "Practiced sol today" in r2.output  # the target word is kept
    assert "sister Sol" not in r2.output  # the sister stays hidden
    assert r2.failures == []


def test_clinician_exemption_only_covers_the_clinician():
    r = deidentify("Name: Mateo López\nValeria wrote the note.", Settings(keep_names=["Valeria López"]))
    assert "López" not in r.output and "Valeria wrote" in r.output
    r = deidentify("Evaluator: Valeria López", Settings(keep_names=["Valeria López"]))
    assert "Valeria López" in r.output and r.failures == []


@pytest.mark.parametrize("text", [
    "Mother: Reports that Mateo is talking more.",
    "Parent: She said he is doing better.",
    "Teacher: Is concerned about attention.",
])
def test_sentence_valued_person_fields_are_rejected(text):
    assert fields.parse(text) == []


def test_lowercase_listed_names_are_accepted_in_fields():
    assert [(h.kind) for h in fields.parse("Mother: lucia ramirez")] == ["PARENT"]


def test_triggered_names_stop_at_line_breaks():
    text = "His mother Ana\nRecommendations\nContinue practice."
    names = {text[c.start:c.end] for c in rules.find(text) if c.kind == "PARENT"}
    assert names == {"Ana"}
    r = deidentify(text)
    assert "Recommendations\nContinue practice." in r.output


def test_restore_keeps_source_lines_that_look_like_metadata():
    text = "Language: English.\nThe child produced /k/.\nref: ABCD is a note"
    r = deidentify(text)
    back = restore(copy_block(r), r)
    assert not back.blocked and back.text == r.source


def test_restore_removes_the_copied_context_with_gender_override():
    r = deidentify("Name: Mateo Ruiz\nMateo did well.", Settings(children=[Child("Mateo", ["Ruiz"])]))
    back = restore(copy_block(r, gender="boy"), r)
    assert not back.blocked and back.text == r.source


def test_new_label_value_row_ends_header_table():
    text = "Name\tDOB\nMateo\t3/4/2022\nMother\tLucía Rodríguez\n"
    hits = [(text[h.start:h.end], h.kind) for h in fields.parse(text)]
    assert ("Mother", "CHILD") not in hits
    assert ("Lucía Rodríguez", "PARENT") in hits


@pytest.mark.parametrize("text", ["Use (CHILD_1) here.", "Use {CHILD_1} here.", "Use [CHILD_1]'s here."])
def test_nested_tag_like_text_escapes_once(text):
    r = deidentify(text)
    assert r.failures == []
    back = restore(copy_block(r), r)
    assert not back.blocked and back.text == r.source


@pytest.mark.parametrize("text", ["Note [XYZ_1] here.", "A [REDACTED] line.", "An [CHILD fragment.",
                                  "Stray ⟦ and ⟧ marks.", "Odd ⟦7⟧ token."])
def test_source_residue_round_trips(text):
    r = deidentify(text)
    back = restore(copy_block(r), r)
    assert not back.blocked, back.issues
    assert back.text == r.source


@pytest.mark.parametrize("draft", ["[xyz_1] ok", "[child did well.", "child_1] ok", "[niño 1] ok", "⟦99⟧ ok"])
def test_lowercase_unknown_and_broken_tags_block(draft):
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    assert restore("ref: K7F2\n" + draft, r).blocked


def test_ordinary_brackets_in_drafts_do_not_block():
    r = deidentify("Name: Mateo Ruiz\nGoal 2 (PLS-5).", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    draft = "ref: K7F2\n[CHILD_1] (mother present) met Goal 2 (PLS-5) [sic] [w] for /r/ [EW:goed]."
    back = restore(draft, r)
    assert not back.blocked, back.issues


def test_kept_values_do_not_trip_the_leak_check():
    assert deidentify("DOB: 2021").failures == []
    assert deidentify("Evaluator: Valeria López", Settings(keep_names=["Valeria López"])).failures == []


@pytest.mark.parametrize("text,kept", [
    ("DOB: 03/14/2021", "/2021"), ("Seen 2021-03-14.", "2021-"), ("DOB 3-14-21", "-21"),
])
def test_years_kept_in_numeric_dates(text, kept):
    r = deidentify(text)
    assert kept in r.output and "[DATE_1]" in r.output and r.failures == []
    back = restore(copy_block(r), r)
    assert back.text == r.source


def test_roster_given_names_split_correctly():
    s = Settings(children=[Child("María José", ["García"])])
    r = deidentify("Name: María José García\nMaría José came.", s)
    tags = {r.source[it.start:it.end]: it.tag for it in r.items}
    assert tags["María José"].startswith("CHILD_") and tags["García"].startswith("SURNAME_")
    assert not any(v.startswith("SURNAME_") and k.startswith("José") for k, v in tags.items())


def test_unknown_names_need_surname_specific_evidence():
    r = deidentify("Parent/Guardian: Ana James Smith")
    by = {r.source[it.start:it.end]: it.tag for it in r.items}
    assert any(t.startswith("SURNAME") for k, t in by.items() if k.endswith("Smith"))


def test_escaped_literal_keeps_gold_offsets():
    text, spans = parse("Text [[[[ then [[CHILD|Mateo]] came.")
    assert text == "Text [[ then Mateo came."
    assert text[spans[0].start:spans[0].end] == "Mateo"


# --------------------------------------------------------------------------- review of 396e20d..746ec31


def test_roster_child_sharing_clinician_first_name_stays_hidden():
    s = Settings(children=[Child("Valeria")], keep_names=["Valeria López"])
    r = deidentify("Valeria produced /k/ in 8/10 trials.", s)
    assert "Valeria" not in r.output and r.failures == []


def test_inline_value_stops_at_next_label():
    r = deidentify("DOB: 01/02/2022  Age: 4;2")
    assert "Age: 4;2" in r.output and "/2022" in r.output
    r = deidentify("DOB: 01/02/2022 Age: 4;2")
    assert "Age: 4;2" in r.output
    hits = [(h.kind) for h in fields.parse("Name: Mateo Ruiz  Grade: K")]
    assert hits == ["CHILD"]


def test_two_session_refs_block():
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    draft = "ref: K7F2\n[CHILD_1] did well.\nref: ABCD\n[CHILD_1] also did well."
    back = restore(draft, r)
    assert back.blocked and back.issues[0].code == "wrong-ref"
    # the same ref twice is fine
    back = restore("ref: K7F2\n[CHILD_1] ok\nref: K7F2", r)
    assert not back.blocked and "ref:" not in back.text


@pytest.mark.parametrize("draft", ["[CHILD_1A] ok", "[child_1 extra] ok", "(CHILD_1x) ok", "[CHILD_ extra] ok"])
def test_damaged_closed_tags_block(draft):
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    assert restore("ref: K7F2\n" + draft, r).blocked


def test_four_digit_tag_numbers_round_trip():
    text = "\n".join(f"Phone: 919-555-{i:04d}" for i in range(1005))
    r = deidentify(text)
    assert "[PHONE_1000]" in r.output and r.failures == []
    back = restore(copy_block(r), r)
    assert not back.blocked and back.text == r.source


def test_roster_role_beats_field_role():
    s = Settings(children=[Child("Mateo", related=[Related("Carmen", "mother")])])
    r = deidentify("Teacher: Carmen Ruiz\nCarmen agreed.", s)
    kinds = {it.kind for it in r.items if r.source[it.start:it.end] == "Carmen"}
    assert kinds == {"PARENT"}


def test_word_name_mentions_stay_reviewable_after_sure_trigger():
    s = Settings(children=[Child("Mateo", related=[Related("Sol", "sister")])])
    r = deidentify("His sister Sol watched.", s)
    sol = next(it for it in r.items if r.source[it.start:it.end] == "Sol")
    assert sol.check_item and sol.hidden


def test_identity_ignores_spacing():
    s = Settings(children=[Child("María José", ["García"])])
    r = deidentify("Name: María José\nMaría  José came.", s)
    assert {it.tag for it in r.items if it.kind == "CHILD"} == {"CHILD_1"}


def test_review_load_counts_per_mention_decisions():
    from localdeid.evaldata import Doc
    from localdeid.evaluate import score_doc

    text = "Sol jumped. Sol laughed."
    r = deidentify(text, Settings(children=[Child("Sol")]))
    score = score_doc(Doc("d", text, [], {}), r)
    assert score.check_items == 1 and score.decisions == 2  # one row, two per-mention decisions


def test_recall_reported_per_fine_type():
    from localdeid.evaldata import Doc, GoldSpan
    from localdeid.evaluate import score_doc, summarize

    text = "Call 919-555-0142 or write ana@example.com."
    doc = Doc("d", text, [GoldSpan(5, 17, "PHONE"), GoldSpan(27, 42, "EMAIL")], {"language": "en"})
    summary = summarize([score_doc(doc, deidentify(text))])["ALL"]
    assert set(summary["recall_by_type"]) == {"PHONE", "EMAIL"}


def test_unpinned_calibration_file_is_refused(tmp_path):
    import json

    from localdeid.pf.model import _pinned_biases

    d = tmp_path / "pf-x"
    d.mkdir()
    (tmp_path / "MANIFEST.json").write_text(json.dumps({"artifacts": {"pf-x": {"files": {}}}}))
    assert _pinned_biases(d) is None
    (d / "viterbi_calibration.json").write_text("{}")
    with pytest.raises(RuntimeError):
        _pinned_biases(d)


# --------------------------------------------------------------------------- review of f17056b..f197565


@pytest.mark.parametrize("text,value", [("ZIP: 92507", "92507"), ("Cary, NC 92507", "92507")])
def test_cpt_lookalike_zip_is_hidden(text, value):
    r = deidentify(text)
    assert value not in r.output and r.failures == []


def test_cpt_after_therapy_abbreviation_stays_protected():
    r = deidentify("Billed PT 97110 and CPT 92507 today.")
    assert "PT 97110" in r.output and "CPT 92507" in r.output


def test_compound_given_name_particles_are_not_names():
    s = Settings(children=[Child("María de los Ángeles", ["Ruiz"])])
    r = deidentify("María de los Ángeles vino. La casa de los abuelos es grande.", s)
    assert "casa de los abuelos" in r.output
    assert "María" not in r.output


@pytest.mark.parametrize("text,value", [("Physician: Rossetti", "Rossetti"), ("Mother: Bayley", "Bayley")])
def test_identity_fields_override_test_name_protection(text, value):
    r = deidentify(text)
    assert value not in r.output and r.failures == []


@pytest.mark.parametrize("text,kept", [("Accuracy: 8/10", "8/10"), ("Precisión: 7/10 hoy", "7/10"),
                                       ("Score = 4/5", "4/5")])
def test_preceding_score_labels_protect_ratios(text, kept):
    assert kept in deidentify(text).output


def test_title_before_hidden_name_is_not_a_leak():
    r = deidentify("Seen by Dr. Patel. Referred by Dra. Gómez.")
    assert r.failures == [] and "Patel" not in r.output and "Dr." in r.output


def test_employer_tag_any_case_restores():
    r = deidentify("Speech by Bright Path Therapy.", Settings(employer_names=["Bright Path Therapy"]), ref="K7F2")
    back = restore("ref: K7F2\nSee [employer] and [Employer].", r)
    assert not back.blocked and back.text.count("Bright Path Therapy") == 2


@pytest.mark.parametrize("draft", ["[XYZ] ok", "{XYZ_1} ok", "[FOO_BAR] ok"])
def test_unknown_closed_placeholders_block(draft):
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    assert restore("ref: K7F2\n" + draft, r).blocked


def test_salt_codes_in_drafts_do_not_block():
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    assert not restore("ref: K7F2\n[CHILD_1] said goed [EW] [FP] [PRO].", r).blocked


def test_surname_fields_get_surname_tags():
    r = deidentify("First Name: Mateo\nLast Name: Ramírez\nName: Mateo Ramírez")
    kinds = {r.source[it.start:it.end]: it.kind for it in r.items}
    assert kinds["Ramírez"] == "SURNAME" and kinds["Mateo"] == "CHILD"
    assert {it.tag for it in r.items if r.source[it.start:it.end] == "Ramírez"} == {"SURNAME_1"}


def test_gazetteer_pairs_do_not_cross_lines():
    r = deidentify("Name: Mateo\nLast Name: Ruiz")
    assert "Last Name:" in r.output


@pytest.mark.parametrize("text", ["ref: ABCD\nThe child did well.", "Notes\nref: K7F2\nmore"])
def test_source_ref_lines_round_trip(text):
    r = deidentify(text, ref="K7F2")
    back = restore(copy_block(r), r)
    assert not back.blocked and back.text == r.source


@pytest.mark.parametrize("marked,surface", [("[[KEEP|[EW:goed]]]", "[EW:goed]"), ("[[KEEP|[w]]] for /r/", "[w]"),
                                            ("x [[CHILD|Mateo]] y", "Mateo")])
def test_markup_surfaces_with_brackets(marked, surface):
    text, spans = parse(marked)
    assert text[spans[0].start:spans[0].end] == surface


def test_true_median_review_load():
    from localdeid.evaluate import Summary, DocScore

    s = Summary()
    for n in (0, 10):
        s.add(DocScore("d", "en/note", {}, {}, [], [], n, False))
    assert s.as_dict()["median_check_items"] == 5


# --------------------------------------------------------------------------- review round 4 (f17056b..3eff5d6)


def test_field_override_keeps_codes_in_date_values():
    r = deidentify("Date of service: March 3, 2026 CPT 92507")
    assert "CPT 92507" in r.output and r.failures == []
    r = deidentify("Location: home; produced /k/ in 8/10 trials")
    assert "/k/" in r.output and "8/10 trials" in r.output


def test_manual_hide_stays_whole():
    r = deidentify("Jane 12345 Doe", manual_hides=[(0, 14)])
    assert "12345" not in r.output and r.failures == []


def test_word_name_in_identifier_field():
    r = deidentify("Name: Will")
    assert "Will" not in r.output
    it = next(i for i in r.items if r.source[i.start:i.end] == "Will")
    assert it.check_item  # a word-name is still decided per mention


@pytest.mark.parametrize("draft", ["CHILD_1A did well.", "child_12x ok", "CHILD_ did well."])
def test_damaged_bare_tags_block(draft):
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    assert restore("ref: K7F2\n" + draft, r).blocked


def test_coordinated_names_are_separate_people():
    r = deidentify("Parents: John Smith and Jane Doe\nJohn called. Jane agreed.")
    assert " and " in r.output.split("\n")[0]
    tags = {r.source[it.start:it.end]: it.tag for it in r.items}
    assert tags["John"] != tags["Jane"]
    assert len({it.tag for it in r.items if r.source[it.start:it.end] == "John"}) == 1


@pytest.mark.parametrize("text,gone", [("Seen on JANUARY 3, 2026.", "JANUARY 3"), ("Seen on march 3, 2026.", "march 3"),
                                       ("DOB 14 MAR 2021", "14 MAR")])
def test_english_dates_any_case(text, gone):
    r = deidentify(text)
    assert gone not in r.output


# --------------------------------------------------------------------------- dev sample gaps


@pytest.mark.parametrize("text,gone", [
    ("record ITP-0038421 noted", "ITP-0038421"), ("ID NC-SLP-04521", "NC-SLP-04521"),
    ("Billed by Carolina Pediatric Speech Partners, PLLC.", "Carolina Pediatric"),
    ("Mom works at Catawba Furniture Works.", "Catawba"),
    ("the Henderson County CDSA called", "Henderson County CDSA"),
    ("con el CDSA del Oeste de Carolina del Norte", "Oeste"),
    ("el DSS del Condado de Catawba", "Catawba"),
])
def test_dev_sample_identifiers_hidden(text, gone):
    assert gone not in deidentify(text).output


@pytest.mark.parametrize("text", ["Speech Therapy Services began.", "PLS-5 and ICD-10 F80.2 noted.",
                                  "CELF Preschool and the Preschool Language Scales were given."])
def test_generic_program_words_kept(text):
    assert deidentify(text).output == text


def test_agency_names_hidden_per_spec():
    # SPEC 6: agency involvement can identify a family; hide the name, keep the type.
    r = deidentify("The CDSA and WIC are involved; Smart Start too.")
    assert r.output == "The [AGENCY_1] and [AGENCY_2] are involved; [AGENCY_3] too."


# --------------------------------------------------------------------------- review round 5 (f17056b..28531fb)


@pytest.mark.parametrize("text,gone", [('Name: "mateo"', "mateo"), ('Mother: "lucia ramirez"', "lucia")])
def test_quoted_values_in_name_fields_are_hidden(text, gone):
    r = deidentify(text)
    assert gone not in r.output and r.failures == []


@pytest.mark.parametrize("draft", ["(CHILD_1 did well.", "{CHILD_1 ok", "(child_1 ok"])
def test_incomplete_tags_with_other_delimiters_block(draft):
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    assert restore("ref: K7F2\n" + draft, r).blocked


def test_protected_target_is_not_a_leak():
    r = deidentify('Name: Rosa\nPracticed "rosa" today.')
    assert r.failures == [] and '"rosa"' in r.output and "Name: Rosa" not in r.output


@pytest.mark.parametrize("text,label", [("Medicaid ID: 912345678K", "Medicaid ID:"),
                                        ("Insurance ID: A12345", "Insurance ID:")])
def test_id_field_labels_are_not_places(text, label):
    assert label in deidentify(text).output


def test_sanitize_keeps_real_ids_and_relabels_codes():
    from localdeid.evaldata import Doc, GoldSpan, sanitize

    text = "Member ID: 93012. CPT 92507 billed. Dx F80.2."
    spans = [GoldSpan(text.index(v), text.index(v) + len(v), "ID") for v in ("93012", "92507", "F80.2")]
    doc = Doc("d", text, spans, {})
    fixed, n = sanitize(doc)
    assert [s.type for s in fixed.spans] == ["ID", "KEEP", "KEEP"] and n == 2


def test_every_issued_copy_block_restores():
    r = deidentify("Name: Mateo Ruiz\nMateo did well.", Settings(children=[Child("Mateo", ["Ruiz"])]))
    first = copy_block(r, gender="boy")
    copy_block(r, gender="girl")
    back = restore(first, r)
    assert not back.blocked and back.text == r.source


def test_percent_from_ratio_is_not_a_new_number():
    r = deidentify("Mateo scored 8/10 trials.", Settings(children=[Child("Mateo")]), ref="K7F2")
    back = restore("ref: K7F2\n[CHILD_1] scored 80%.", r)
    assert not any(i.code == "new-number" for i in back.issues)


# --------------------------------------------------------------------------- dev tuning (generic words, coverage)


@pytest.mark.parametrize("name", ["Ella", "Nina", "Tia", "Don", "Al"])
def test_names_that_look_like_generic_words_are_detected(name):
    r = deidentify(f"Name: {name} Smith\n{name} did well.")
    assert f"{name} did" not in r.output


def test_kin_words_never_become_names():
    r = deidentify("Mamá Rosa Elena Vásquez vino. Mamá comenta que todo va bien. Se recomienda a mamá practicar.")
    assert "Mamá comenta" in r.output and "a mamá practicar" in r.output and "Vásquez" not in r.output


def test_sentence_initial_preposition_is_not_part_of_an_org():
    r = deidentify("At Little Sprouts Bilingual Preschool he plays. Staff at Little Sprouts Bilingual Preschool agree.")
    assert r.output.startswith("At [") and "Staff at [" in r.output


@pytest.mark.parametrize("text,gone", [
    ("Mom works at Compass Logistics.", "Compass"),
    ("Dad is a supervisor at Blue Ridge Freight Lines.", "Blue Ridge"),
    ("Speech by Carolina Bilingual Speech & Language Therapy, PLLC.", "Carolina Bilingual"),
    ("Terapias Pediátricas Bilingües de Carolina, PLLC", "Terapias"),
    ("referred by Pediatric Therapy Partners of NC", "Partners of NC"),
    ("Autism Society of North Carolina helped.", "Autism Society"),
    ("Guilford County WIC Program enrolled them.", "Guilford"),
    ("CDSA de la Región del Piedmont", "Piedmont"),
    ("Departamento de Servicios Sociales del Condado de Cumberland", "Cumberland"),
    ("Orange County Schools", "Orange County"),
    ("lives in Wake County", "Wake"),
    ("on Startown Road in Hickory", "Startown"),
    ("Centro Infantil Arcoíris", "Arcoíris"),
    ("Little Sprouts Daycare Center", "Center"),
    ("UNC Children's Surgery Center", "Surgery Center"),
    ("M.F.R. attended", "M.F.R."),
    ("Intelligibility approximately 3/10 for unfamiliar listeners", "[DATE"),
])
def test_dev_coverage(text, gone):
    assert gone not in deidentify(text).output


@pytest.mark.parametrize("text", ["She has an M.S. and lives in the U.S.",
                                  "approximately 3/10 for unfamiliar listeners"])
def test_dev_coverage_negatives(text):
    assert deidentify(text).output == text



# --------------------------------------------------------------------------- review-load tuning


def test_state_names_are_not_roster_children():
    r = deidentify("Carolina lives in North Carolina, en Carolina del Norte.", Settings(children=[Child("Carolina")]))
    assert "North Carolina" in r.output and "Carolina del Norte" in r.output and r.output.startswith("[CHILD_1]")
    assert r.failures == []


def test_fuzzy_roster_ignores_lowercase_words():
    r = deidentify("Usa media palabra. Medinna came.", Settings(children=[Child("Ana", ["Medina"])]))
    assert "media palabra" in r.output and "Medinna" not in r.output


def test_surname_only_bare_words_are_not_check_items():
    r = deidentify("The Case Manager reviewed the Record. We saw Diego wave.")
    keys = {r.source[i.start:i.end] for i in r.items if i.check_item}
    assert "Case" not in keys and "Record" not in keys and "Diego" in keys


def test_split_part_does_not_inherit_word_name_status():
    r = deidentify("Mother: Esperanza Medina\nEsperanza Medina called.")
    medina = [i for i in r.items if r.source[i.start:i.end] == "Medina"]
    assert medina and not any("@" in i.decision_key for i in medina)


def test_roster_name_conflicts_pick_closer_relation_without_check_item():
    kids = [Child("Mateo", ["Ruiz"]), Child("Ana", related=[Related("Mateo", "brother")])]
    r = deidentify("Mateo did well.", Settings(children=kids))
    it = next(i for i in r.items if r.source[i.start:i.end] == "Mateo")
    assert it.kind == "CHILD" and "role-conflict" not in it.notes


# --------------------------------------------------------------------------- dev tuning batches C-K


@pytest.mark.parametrize("text,kept", [
    ("puntuación de Apgar de 9/9", "9/9"), ("omitió consonantes en el 8/10 de las oportunidades", "8/10"),
    ("(7/10)", "7/10"), ("approximately 3/10 for unfamiliar listeners and 6/10 for familiar listeners", "6/10"),
])
def test_clinical_ratios_kept(text, kept):
    assert kept in deidentify(text).output


@pytest.mark.parametrize("text,gone", [
    ("Madre: R.A.", "A."), ("Clinician: Hannah R. Soto, M.S., CCC-SLP", "Hannah"),
    ("Jugó con el perro de la familia, Canela, hoy", "Canela"), ("Cliente: Santiago (apodo: \"Santi\")", '"Santi"'),
    ("Cliente: Santiago (apodo: \"Santi\")", "Santiago"),
    ("the dog Ne-ne barked", "Ne-ne"), ("record NC-SLP-04521", "SLP-04521"), ("Planta Avícola del Condado", "Avícola"),
    ("Construcción Vargas y Asociados", "Vargas"), ("TherapyWorks of the Piedmont", "TherapyWorks"),
    ("Mom works at Food Lion.", "Food Lion"), ("Agencia de Servicios de Desarrollo Infantil (CDSA) del Piedmont", "Piedmont"),
    ("Smart Start de las Montañas del Oeste", "Montañas"), ("CDSA Regional del Triángulo", "Triángulo"),
    ("en el condado de Durham", "Durham"), ("Family lives in Wake.", "Wake"), ("near San Pedro Sula", "San Pedro"),
    ("from Riobamba, Ecuador", "Riobamba"), ("Pt resides in Siler City.", "Siler City"),
    ("Mateo de la Cruz Alvarado came", "de la"), ("Parent: Ruiz, Ana", "Ana"),
])
def test_dev_tuning_identifiers_hidden(text, gone):
    assert gone not in deidentify(text).output


@pytest.mark.parametrize("text", [
    "Terapia del habla y lenguaje semanal.", "EVALUACIÓN DEL HABLA Y DEL LENGUAJE", "I. DATOS GENERALES",
    "Usted puede ver más detalles en el informe.", "Recommend Early Intervention services.",
    "The family went to the store at 9.",
])
def test_dev_tuning_text_kept(text):
    r = deidentify(text)
    assert r.output == text and r.failures == []


def test_model_dates_without_digits_or_months_are_dropped():
    from localdeid.pf.model import PFSpan

    text = "Seen next Tuesday and aprox. 3 semanas ago."
    r = deidentify(text, pf_detect=lambda t: [PFSpan(5, 17, "private_date", 0.9), PFSpan(22, 37, "private_date", 0.9)])
    assert "next Tuesday" in r.output



# --------------------------------------------------------------------------- review round 6 (f17056b..3c4c69d)


def test_lowercase_given_name_after_comma_in_field():
    r = deidentify("Mother: perez, lucia")
    assert "lucia" not in r.output and "perez" not in r.output


def test_spanish_address_with_number_and_unit():
    r = deidentify("Vive en 412 Calle Maple, Apt. 3B.")
    assert "412" not in r.output and "3B" not in r.output


@pytest.mark.parametrize("draft", ["{CHILD_1 extra} ok", "{CHILD_} ok"])
def test_damaged_brace_tags_block(draft):
    r = deidentify("Name: Mateo Ruiz", Settings(children=[Child("Mateo", ["Ruiz"])]), ref="K7F2")
    assert restore("ref: K7F2\n" + draft, r).blocked


def test_model_duration_is_not_a_date():
    from localdeid.pf.model import PFSpan

    text = "He returned after 2 weeks."
    r = deidentify(text, pf_detect=lambda t: [PFSpan(18, 25, "private_date", 0.9)])
    assert r.output == text


@pytest.mark.parametrize("text,gone", [("Age: 94;2", "94;2"), ("su abuela, 92 years old", "92"), ("tiene 91 años", "91")])
def test_ages_over_89_are_hidden(text, gone):
    r = deidentify(text)
    assert gone not in r.output and r.failures == []


def test_ages_under_90_stay():
    assert deidentify("Age 4;2 and CA: 89;11").output == "Age 4;2 and CA: 89;11"


def test_sanitize_respects_identity_fields():
    from localdeid.evaldata import Doc, GoldSpan, sanitize

    text = "Member ID: 92507\nName: Bayley\nMother: Rossetti"
    spans = [GoldSpan(text.index(v), text.index(v) + len(v), t) for v, t in
             (("92507", "ID"), ("Bayley", "CHILD"), ("Rossetti", "PARENT"))]
    fixed, n = sanitize(Doc("d", text, spans, {}))
    assert [s.type for s in fixed.spans] == ["ID", "CHILD", "PARENT"] and n == 0


def test_scoring_requires_given_name_after_comma():
    from localdeid.evaluate import required_chars

    t = "Smith, John"
    assert "".join(t[i] for i in required_chars(t, 0, len(t), "PARENT")) == "SmithJohn"
    t = "Carla Fuentes, M.S., CCC-SLP"
    assert "".join(t[i] for i in required_chars(t, 0, len(t), "PROVIDER")) == "CarlaFuentes"


def test_repeated_unsure_names_are_linear():
    import time

    text = "We saw Diego. " * 400
    t = time.time()
    r = deidentify(text)
    assert time.time() - t < 5 and r.failures == []


def test_scores_normalize_both_ways():
    r = deidentify("Mateo scored 80% accuracy.", Settings(children=[Child("Mateo")]), ref="K7F2")
    back = restore("ref: K7F2\n[CHILD_1] scored 8/10.", r)
    assert not any(i.code == "new-number" for i in back.issues)


def test_health_heading_is_not_a_hospital():
    assert deidentify("Oral Health and Mental Health were discussed.").output == "Oral Health and Mental Health were discussed."


@pytest.mark.parametrize("text,gone", [("He attends Sunny Path every day.", "Sunny Path"),
                                       ("Enrolled at Little Lambs Learning Nest.", "Little Lambs"),
                                       ("Medicaid plan: Trillium Health Resources", "Trillium")])
def test_org_context_triggers(text, gone):
    assert gone not in deidentify(text).output


# --------------------------------------------------------------------------- review round 7 (3c4c69d..6e12f44)


def test_context_line_never_shows_over_89_age():
    r = deidentify("Name: Mateo Ruiz\nGrandmother age: 94;2. Mateo is 4;2.", Settings(children=[Child("Mateo", ["Ruiz"])]))
    block = copy_block(r)
    assert "94;2" not in block and "[CHILD_1]: 4;2." in block


@pytest.mark.parametrize("text", ["He was referred to Physical Therapy.", "Taught through Total Communication.",
                                  "He has a Tailored Plan.", "Mental Health and Oral Health topics."])
def test_clinical_phrases_are_not_organizations(text):
    assert deidentify(text).output == text


@pytest.mark.parametrize("text,gone", [("Seen at Riverbend Health.", "Riverbend"), ("Services at Northstar Health.", "Northstar")])
def test_named_health_providers_hidden(text, gone):
    assert gone not in deidentify(text).output


def test_percent_precision_warns():
    r = deidentify("Mateo scored 80% accuracy.", Settings(children=[Child("Mateo")]), ref="K7F2")
    back = restore("ref: K7F2\n[CHILD_1] scored 80.4%.", r)
    assert any(i.code == "new-number" and i.text == "80.4%" for i in back.issues)
    back = restore("ref: K7F2\n[CHILD_1] scored 4/5.", r)
    assert not any(i.code == "new-number" for i in back.issues)


def test_zero_padded_age_kept_even_if_model_says_date():
    from localdeid.pf.model import PFSpan

    r = deidentify("Age: 04;02", pf_detect=lambda t: [PFSpan(5, 10, "private_date", 0.9)])
    assert r.output == "Age: 04;02"


# --- dev2 roster-mode clinical hides (DECISIONS 21) -------------------------


def _kid(**kw):
    return Settings(children=[Child("Mateo", ["Ruiz"], **kw)])


def test_one_letter_roster_surname_never_matches():
    s = _kid(related=[Related("Emma", "peer", ["W"]), Related("Lucía", "peer", ["G"])])
    r = deidentify("Produced [w] for /r/ and /g/ for /d/; w/ mod cues. Emma W. joined.", s)
    assert "[w]" in r.output and "/g/" in r.output and "w/ mod" in r.output
    assert "Emma" not in r.output


@pytest.mark.parametrize("text", [
    'Target word "sol," then "pan."',
    "Produjo 'sol' como [tol].",
    "Imitó /sol/ con modelo.",
    'Dijo "flor" producido como "for".',
])
def test_lowercase_roster_word_in_stimulus_stays(text):
    s = _kid(related=[Related("Sol", "sister"), Related("Flor", "aunt")])
    r = deidentify(text, s)
    assert r.output == r.source and not r.failures


def test_capitalized_roster_word_in_quotes_stays_a_check_item():
    s = _kid(related=[Related("Sol", "sister")])
    r = deidentify('He said "Sol" at home.', s)
    items = [it for it in r.items if r.source[it.start:it.end] == "Sol"]
    assert items and all(it.hidden and not it.sure for it in items)


def test_max_cue_level_is_not_the_pet_max():
    s = _kid(related=[Related("Max", "pet")])
    r = deidentify("Stimulable only w/ max cues. Trabajado c/ máx cues táctiles. His dog Max barked.", s)
    assert "w/ max cues" in r.output and "c/ máx cues" in r.output
    assert "dog Max" not in r.output


@pytest.mark.parametrize("text,term", [
    ("Progreso en Comprension Auditiva hoy.", "Comprension Auditiva"),
    ("El area de Expresion del Lenguaje mejoró.", "Expresion del Lenguaje"),
    ("Usando el enfoque de Cycles.", "Cycles"),
    ("MLU 2.1 morphemes.", "MLU"),
])
def test_clinical_terms_without_accents_are_protected(text, term):
    from localdeid.protect import protect

    assert any(text[p.start:p.end] == term for p in protect(text))


def test_model_org_span_across_table_cells_is_dropped():
    from localdeid.pipeline import _model_org_ok

    t = "MLU\t2.1\nRiverside Kids Academy"
    assert not _model_org_ok(t, 0, 5, [])
    assert not _model_org_ok(t, 4, 22, [])
    assert _model_org_ok(t, 8, 30, [])


@pytest.mark.parametrize("text,hidden", [
    ('Target word "sol" today.', False),
    ("Target: /s/ in initial position.", False),
    ("Practice at Target words level.", False),
    ("Mom works at Target on weekends.", True),
    ("Papá trabaja en Target.", True),
])
def test_target_is_a_store_only_after_a_workplace_preposition(text, hidden):
    assert ("Target" not in deidentify(text).output) is hidden


@pytest.mark.parametrize("text,kept", [
    ("Father works at Construcción Hernández (in English, Hernández Construction). We speak English.",
     ["(in English, ", "We speak English."]),
    ("Assessed via the BESA Semantics subtest.", ["BESA Semantics"]),
    ("Clinical Evaluation of Language Fundamentals, Preschool-3 Spanish (CELF-5 Spanish)", ["Preschool-3 Spanish"]),
    ("Visión evaluada por pediatra, WNL. Sin hx.", ["WNL"]),
    ("Speech sample: 3/10 single-word utterances intelligible.", ["3/10 single-word"]),
    ("Produjo 7/10 enunciados.", ["7/10"]),
])
def test_dev2_clinical_hides_stay_visible(text, kept):
    r = deidentify(text)
    for k in kept:
        assert k in r.output
    assert not r.failures


@pytest.mark.parametrize("text,hidden", [
    ("IFSP #1 (continuado, modificado): Mateo usará palabras.", None),
    ("Group number: G7", "G7"),
    ("IFSP #: 2024-0042", "2024-0042"),
])
def test_ifsp_outcome_number_is_not_an_id(text, hidden):
    from localdeid import fields

    ids = [text[c.start:c.end] for c in fields.find(text) if c.kind == "ID"]
    assert ids == ([hidden] if hidden else [])


def test_family_child_care_home_is_one_daycare():
    r = deidentify("He attends Pequeños Pasos Family Child Care Home four days a week, and at home he plays.")
    assert "[DAYCARE_1] four days" in r.output and "at home he plays" in r.output and not r.failures


def test_masked_tags_do_not_create_field_values():
    r = deidentify("Fecha de nacimiento: 5 de marzo de 2024\nDistrito escolar: Distrito Escolar de Salinas: Escuela Roosevelt")
    assert not r.failures


@pytest.mark.parametrize("text,out", [
    ("Seen at Sunrise Pediatric clinic weekly.", "Seen at [HOSPITAL_1] weekly."),
    ("Born at Duke hospital.", "Born at [HOSPITAL_1]."),
    ("In the ENT clinic, hearing was normal.", None),
    ("GI clinic visit.", None),
    ("The Pediatric clinic called.", None),
    ("Mateo attends speech clinic.", None),
])
def test_lowercase_clinic_after_a_name(text, out):
    r = deidentify(text)
    assert r.output == (out or text) and not r.failures


def test_table_row_language_words_are_not_a_leak():
    t = ("Service\tFrequency\tLocation\tLanguage\n"
         "Speech therapy\t2x/week\tSunrise Pediatric clinic\tSpanish with Dr. Laura Ortiz, English secondary\n")
    r = deidentify(t)
    assert not r.failures and "Ortiz" not in r.output and "English secondary" in r.output


def test_scorer_org_connectors_are_optional_but_names_required():
    from localdeid.evaluate import required_chars

    t = "Carolina ENT at Novant Health, campus Mebane"
    req = "".join(t[i] for i in required_chars(t, 0, len(t), "HOSPITAL"))
    assert req == "CarolinaENTNovantHealthMebane"
    t = "Juan de la Cruz"
    assert "".join(t[i] for i in required_chars(t, 0, len(t), "PARENT")) == "JuandelaCruz"


# --- review round 8 ---------------------------------------------------------


def test_org_run_stops_before_trailing_clinical_finding():
    r = deidentify("Seen at Maplecrest Clinic, Normal Hearing.")
    assert r.output == "Seen at [HOSPITAL_1], Normal Hearing."
    r = deidentify("Duke Health Ear, Nose & Throat – Page Road Clinic follow-up.")
    assert r.output == "[HOSPITAL_1] follow-up."


def test_parenthesized_id_value_is_hidden():
    r = deidentify("IFSP #: (2024-0042)")
    assert "2024-0042" not in r.output and not r.failures


@pytest.mark.parametrize("text", [
    "Mother works at a Target store.",
    "Parent workplace: Target",
    "Papá trabaja en Target.",
    "Mom has worked at Target for two years.",
])
def test_target_in_workplace_contexts_is_hidden(text):
    assert "Target" not in deidentify(text).output


@pytest.mark.parametrize("text", ["Target words: sol, pan.", "Target: /s/ initial.", "Target level: phrase."])
def test_target_as_slp_word_stays(text):
    assert deidentify(text).output == text


def test_table_cells_stay_separate_organizations():
    r = deidentify("School\tDaycare\nLakewood Elementary School\tLittle Sprouts Daycare\n"
                   "Lakewood Elementary School called.")
    assert r.output == "School\tDaycare\n[SCHOOL_1]\t[DAYCARE_1]\n[SCHOOL_1] called."


@pytest.mark.parametrize("text", ["School Attendance: regular.", "Library Visit: read books.",
                                  "Church Activities: weekly.", "Daycare Schedule: M-F."])
def test_generic_org_labels_stay(text):
    assert deidentify(text).output == text


# --- review round 9 ---------------------------------------------------------


@pytest.mark.parametrize("text,name", [
    ("A report from Kids Academy documented progress.", "Kids Academy"),
    ("Learning Academy staff met.", "Learning Academy"),
])
def test_type_word_names_stay_hidden(text, name):
    assert name not in deidentify(text).output


def test_identifying_comma_suffix_stays_in_the_name():
    assert deidentify("Seen at Maplecrest Clinic, Oakwood Branch.").output == "Seen at [HOSPITAL_1]."


@pytest.mark.parametrize("text", ["Accuracy with Target /s/ improved.", "Accuracy for Target /s/ improved.",
                                  "Practice in Target [s] words.", "Worked on Target: /k/."])
def test_target_before_phoneme_notation_stays(text):
    assert deidentify(text).output == text


# --- review round 10 --------------------------------------------------------


@pytest.mark.parametrize("finding", ["Hearing Loss", "Reduced Hearing", "Speech Sound Disorder", "Normal Hearing"])
def test_comma_finding_after_clinic_stays(finding):
    assert deidentify(f"Seen at Maplecrest Clinic, {finding}.").output == f"Seen at [HOSPITAL_1], {finding}."


@pytest.mark.parametrize("text", ["Seen at Maplecrest Clinic, Oakwood Branch.",
                                  "Seen at Duke Children's Hospital, Durham."])
def test_comma_suffix_with_evidence_stays_in_name(text):
    assert deidentify(text).output == "Seen at [HOSPITAL_1]."


@pytest.mark.parametrize("text", ["The child receives Early Learning services.", "He is in the Preschool Classroom.",
                                  "School Attendance: regular.", "Daycare Schedule: M-F."])
def test_generic_type_phrases_stay(text):
    assert deidentify(text).output == text


@pytest.mark.parametrize("text,name", [("A report from Kids Academy documented progress.", "Kids Academy"),
                                       ("Learning Academy staff met.", "Learning Academy")])
def test_ambiguous_type_names_are_hidden_check_items(text, name):
    r = deidentify(text)
    items = [it for it in r.items if r.source[it.start:it.end] == name]
    assert items and all(it.hidden and not it.sure for it in items)


@pytest.mark.parametrize("text", ["Father is a cashier at Target (weekends).", "Mother has a job with Target."])
def test_target_workplace_with_parenthesis_or_with(text):
    assert "Target" not in deidentify(text).output


# --- review round 11 --------------------------------------------------------


def test_ambiguous_name_before_descriptor_is_a_check_item():
    r = deidentify("A report from Kids Academy services documented progress.")
    items = [it for it in r.items if r.source[it.start:it.end] == "Kids Academy"]
    assert items and all(it.hidden and not it.sure for it in items)


@pytest.mark.parametrize("tail", ["Audiology Results", "Medical History"])
def test_clinical_tail_with_keyword_stays(tail):
    assert deidentify(f"Seen at Maplecrest Clinic, {tail}.").output == f"Seen at [HOSPITAL_1], {tail}."


def test_parenthesized_phoneme_after_target_stays():
    t = "Accuracy for Target (/s/) improved."
    assert deidentify(t).output == t


# --- review round 12 --------------------------------------------------------


@pytest.mark.parametrize("text", ["Father is a stocker at Target (night shift).",
                                  "Papá es guardia en Target (turno nocturno)."])
def test_target_before_plain_parenthesis_is_hidden(text):
    assert "Target" not in deidentify(text).output


@pytest.mark.parametrize("text", ["Tongue position for Target: /s/ was modeled.",
                                  "Accuracy for Target (80%) improved."])
def test_target_before_clinical_notation_stays(text):
    assert deidentify(text).output == text


def test_identifying_segment_after_generic_department():
    r = deidentify("Seen at Maplecrest Clinic, Audiology Department, Oakwood Branch.")
    assert "Oakwood" not in r.output and "Maplecrest" not in r.output and not r.failures


# --- review round 13 --------------------------------------------------------


@pytest.mark.parametrize("text", ["Father is a cashier at Target: night shift.",
                                  "Father is a cashier at Target (20 hours per week).",
                                  "Mother took a position with Target.", "He works for Target."])
def test_target_with_employment_evidence_is_hidden(text):
    assert "Target" not in deidentify(text).output


@pytest.mark.parametrize("text", ["Accuracy for Target (minimal pairs) improved.",
                                  "Tongue position for Target: /s/ was modeled."])
def test_target_without_employment_evidence_stays(text):
    assert deidentify(text).output == text


def test_findings_between_two_clinics_stay():
    r = deidentify("Seen at Maplecrest Clinic, Reduced Hearing, Page Road Clinic.")
    assert r.output == "Seen at [HOSPITAL_1], Reduced Hearing, [HOSPITAL_2]." and not r.failures


def test_acronym_after_an_organization_is_hidden_everywhere():
    r = deidentify("Referred by Alamance Partnership for Children (APC) last year. APC called.")
    assert "APC" not in r.output and not r.failures
    assert deidentify("Seen at Duke Hospital (ENT) today.").output == "Seen at [HOSPITAL_1] (ENT) today."


# --- review round 14 --------------------------------------------------------


@pytest.mark.parametrize("text", ["Father drives for Target.", "Papá conduce para Target."])
def test_target_after_a_parent_subject_is_hidden(text):
    assert "Target" not in deidentify(text).output


def test_branch_after_skipped_generic_first_segment_is_hidden():
    r = deidentify("Seen at Audiology Department, Reduced Hearing, Oakwood Branch.")
    assert "Oakwood" not in r.output and "Reduced Hearing" in r.output and not r.failures


@pytest.mark.parametrize("text", ["The new position for Target sounds was modeled.", "She practiced for Target words."])
def test_target_clinical_words_after_employment_phrase_stay(text):
    assert deidentify(text).output == text


@pytest.mark.parametrize("acr", ["ASD", "OT", "SLP"])
def test_inferred_acronym_respects_protection(acr):
    r = deidentify(f"Seen at Maplecrest Clinic ({acr}).", Settings(employer_names=["Maplecrest Clinic"]))
    assert r.output == f"Seen at [EMPLOYER] ({acr})." and not r.failures


def test_employer_acronym_takes_the_employer_tag():
    r = deidentify("Seen at Maplecrest Clinic (MCC). MCC called.", Settings(employer_names=["Maplecrest Clinic"]))
    assert r.output == "Seen at [EMPLOYER] ([EMPLOYER]). [EMPLOYER] called."


@pytest.mark.parametrize("text,acr", [
    ("Coordinación de Servicios Tempranos del Piedmont, Incorporada (CSTP). Luego CSTP llamó.", "CSTP"),
    ("Carolina Speech Partners, PLLC (CSP) called. CSP agreed.", "CSP"),
])
def test_acronym_after_legal_suffix_is_hidden_everywhere(text, acr):
    r = deidentify(text)
    assert acr not in r.output and "Incorporada" not in r.output and not r.failures


def test_legal_suffix_needs_a_word_boundary():
    from localdeid import rules

    t = "Ana Incognito visited."
    assert not [c for c in rules.find(t) if c.note == "rule:company"]


# --- review round 15 (and a sentence-boundary bug found while fixing it) ----


def test_acronym_across_generic_segment_is_hidden():
    r = deidentify("Seen at Maplecrest Clinic, Audiology Department (MCC). MCC called.")
    assert "MCC" not in r.output and "Audiology Department" in r.output and not r.failures


@pytest.mark.parametrize("text", ["She practiced for Target (minimal pairs) today.", "He practiced for Target (80%) today."])
def test_pronoun_without_employment_verb_keeps_target(text):
    assert deidentify(text).output == text


@pytest.mark.parametrize("text,out", [
    ("Seen at Duke Hospital. He has ASD (autism).", "Seen at [HOSPITAL_1]. He has ASD (autism)."),
    ("Referred to Wake County Public School System. Mom agreed.", "Referred to [SCHOOL_1]. Mom agreed."),
    ("Attends St. Mary's Preschool daily.", "Attends [DAYCARE_1] daily."),
])
def test_org_runs_stop_at_sentence_ends(text, out):
    assert deidentify(text).output == out


def test_acronym_takes_the_nearest_organization():
    r = deidentify("Seen at Duke Hospital, Durham, and Wake County Schools (WCPSS) later.")
    assert r.output == "Seen at [HOSPITAL_1], and [SCHOOL_1] ([SCHOOL_2]) later."


# --- review round 16 --------------------------------------------------------


@pytest.mark.parametrize("text,out", [
    ("Seen at Maplecrest Clinic. Oakwood Church called.", "Seen at [HOSPITAL_1]. [ORG_1] called."),
    ("Seen at Maplecrest Clinic. Cedarcrest Public Library called.", "Seen at [HOSPITAL_1]. [ORG_1] called."),
    ("Attends Little Sprouts Daycare.", "Attends [DAYCARE_1]."),
    ("Visits Cary Public Library weekly.", "Visits [ORG_1] weekly."),
])
def test_org_after_sentence_end_and_initial_verbs(text, out):
    assert deidentify(text).output == out


@pytest.mark.parametrize("text", ["She delivers groceries for Target.", "Father also drives trucks for Target.",
                                  "Papá reparte paquetes para Target."])
def test_target_after_employment_verb_with_object(text):
    assert "Target" not in deidentify(text).output


def test_acronym_of_a_finding_after_a_clinic_stays():
    t = "Seen at Maplecrest Clinic, Hearing Loss (HL) noted. HL is bilateral."
    assert deidentify(t).output == "Seen at [HOSPITAL_1], Hearing Loss (HL) noted. HL is bilateral."


# --- review round 17 --------------------------------------------------------


@pytest.mark.parametrize("text,out", [
    ("Maplecrest Children's Hospital called.", "[HOSPITAL_1] called."),
    ("Cedarcrest Oakwood School called.", "[SCHOOL_1] called."),
    ("Attends Public Library weekly.", "Attends [ORG_1] weekly."),
    ("Uses Public Library weekly.", "Uses [ORG_1] weekly."),
])
def test_only_listed_prose_words_are_trimmed(text, out):
    assert deidentify(text).output == out


@pytest.mark.parametrize("verb", ["conduce", "reparte"])
def test_spanish_weekend_modifier_before_target(verb):
    assert "Target" not in deidentify(f"Papá {verb} los fines de semana para Target.").output


# --- review round 18 --------------------------------------------------------


@pytest.mark.parametrize("text,out", [("Ayer Elementary School sent the report.", "[SCHOOL_1] sent the report."),
                                      ("Hoy Clinic called.", "[HOSPITAL_1] called.")])
def test_prose_word_that_is_a_name_keeps_the_name_hidden(text, out):
    # Without the first word no name remains, so it stays in the name (round 19).
    assert deidentify(text).output == out


@pytest.mark.parametrize("text,verb", [
    ("Discussed Cedarcrest School placement.", "Discussed"),
    ("Received Duke Hospital records.", "Received"),
    ("Recibe Guardería Los Pinitos apoyo.", "Recibe"),
])
def test_clinical_verbs_before_organizations_stay(text, verb):
    r = deidentify(text)
    assert r.output.startswith(verb + " [") and not r.failures


def test_listed_name_with_verb_shape_stays_in_the_name():
    assert deidentify("Reed Elementary School called.").output == "[SCHOOL_1] called."


# --- review round 19 --------------------------------------------------------


def test_namey_prose_word_is_reviewable_or_kept():
    r = deidentify("Shared Beginnings Daycare called.")
    shared = [it for it in r.items if r.source[it.start:it.end] == "Shared"]
    assert shared and all(it.hidden and not it.sure for it in shared)
    assert deidentify("Shared Care Clinic called.").output == "[HOSPITAL_1] called."


@pytest.mark.parametrize("text", ["Staff Training was completed.", "Per Parent Report, the child used phrases."])
def test_no_collision_item_without_an_organization(text):
    assert deidentify(text).output == text


@pytest.mark.parametrize("text,word", [
    ("Shining Stars Preschool sent notes.", "Shining"),
    ("Growing Minds Learning Center called.", "Growing"),
    ("Blessed Trinity Catholic School called.", "Blessed"),
])
def test_verb_shaped_name_words_are_never_left_visible(text, word):
    # Round 20: an "-ed"/"-ing" first word is a reviewable boundary: hidden by default.
    r = deidentify(text)
    assert word not in r.output and not r.failures


# --- review round 20 --------------------------------------------------------


def test_ambiguous_word_before_a_clinical_phrase_emits_nothing():
    t = "Shared Medical History with the parent."
    assert deidentify(t).output == t


@pytest.mark.parametrize("text,verb", [("Exploring Cedarcrest School placement.", "Exploring"),
                                       ("Considering Duke Hospital options.", "Considering"),
                                       ("Encouraged Little Sprouts Daycare staff.", "Encouraged")])
def test_unlisted_verb_boundary_is_reviewable(text, verb):
    r = deidentify(text)
    items = [it for it in r.items if r.source[it.start:it.end] == verb]
    assert items and all(it.hidden and not it.sure for it in items)
    assert r.output.startswith("[PERSON_1] [")


def test_ambiguous_word_before_generic_org_words_is_one_unsure_item():
    r = deidentify("Shared Care Clinic called.")
    assert r.output == "[HOSPITAL_1] called."
    assert all(not it.sure for it in r.items if it.hidden)


# --- review round 21 --------------------------------------------------------


def test_label_like_word_in_name_keeps_the_school_reviewable():
    r = deidentify("Blessed Day School (BDS) called. BDS sent notes.")
    assert "Blessed" not in r.output and "BDS" not in r.output and not r.failures


def test_initial_boundary_judged_in_first_comma_group():
    r = deidentify("Shining Church, Reduced Hearing, Oakwood Branch called.")
    assert "Shining" not in r.output and "Oakwood" not in r.output and "Reduced Hearing" in r.output


# --- review round 22 --------------------------------------------------------


def test_clinical_first_group_does_not_drop_later_names():
    r = deidentify("Shared Medical History, Oakwood Church (OC) sent records. OC called.")
    assert r.output.startswith("Shared Medical History, [") and "Oakwood" not in r.output and "OC" not in r.output


def test_ambiguous_word_before_a_label_head_stays_text():
    t = "Shared Community Center Schedule with the family."
    assert deidentify(t).output == t


# --- review round 23 --------------------------------------------------------


def test_named_program_with_acronym_is_reviewable():
    r = deidentify("Inspired Learning Program (ILP) called. ILP sent notes.")
    assert "Inspired" not in r.output and "ILP" not in r.output and not r.failures


def test_later_segment_prose_label_stays_text():
    t = "Shared Medical History, Shared Community Center Schedule with the family."
    assert deidentify(t).output == t


# --- review round 24 --------------------------------------------------------


def test_prose_shaped_name_after_comma_is_reviewable():
    r = deidentify("Shared Medical History, Blessed Church (BC) sent records. BC called.")
    assert "Blessed" not in r.output and "BC" not in r.output and "Shared Medical History" in r.output
    blessed = [it for it in r.items if r.source[it.start:it.end] == "Blessed Church"]
    assert blessed and all(it.hidden and not it.sure for it in blessed)


def test_descriptor_head_without_acronym_stays_text():
    t = "Exploring Learning Approaches with the family."
    assert deidentify(t).output == t


# --- review round 25 --------------------------------------------------------


def test_program_name_without_acronym_is_reviewable():
    r = deidentify("Inspired Learning Program called.")
    assert r.output == "[DAYCARE_1] called." and all(not it.sure for it in r.items if it.hidden)


def test_finding_like_word_with_distinct_name_word_is_a_name():
    r = deidentify("Shared Medical History, Normal Heights Church (NHC) sent records. NHC called.")
    assert "Heights" not in r.output and "NHC" not in r.output and "Shared Medical History" in r.output


def test_activity_head_after_comma_stays_text():
    t = "Shared Medical History, Exploring Learning Approaches with the family."
    assert deidentify(t).output == t


# --- advisor (Fable 5.1), before gate2 ----------------------------------------


@pytest.mark.parametrize("name", ["Kids Time Daycare", "New Day Learning Center", "Learning Time Daycare"])
def test_label_word_inside_a_daycare_name_is_not_a_skip(name):
    r = deidentify(f"{name} called. Mom likes {name}.")
    assert name not in r.output and not r.failures


def test_sanitize_drops_keep_spans_that_hold_a_gold_name():
    from localdeid.evaldata import parse, sanitize, Doc

    text, spans = parse('[[PET|Canela]] barked. He said [[KEEP|"Canela run fast"]] and [[KEEP|/k/]].')
    d, n = sanitize(Doc("t", text, spans, {}))
    keeps = [text[s.start:s.end] for s in d.keeps]
    assert keeps == ["run fast", "/k/"]  # the clinical words stay KEEP (/cr round 27)
    # the name inside the quote is now labelled too (DECISIONS 26 label repair): two changes
    assert [text[s.start:s.end] for s in d.identifiers] == ["Canela", "Canela"] and n == 2


@pytest.mark.parametrize("text", ["Standard score 78 with 90% CI 73-83 today.", "Total Language Score: 73, 95% CI (66–78)",
                                  "AE 2-3 years", "intervalo de confianza 70–82"])
def test_score_ranges_after_a_score_cue_stay(text):
    r = deidentify(text, pf_detect=lambda t: [])
    assert r.output == text


# --- dev2 over-hides found by the extra-hides metric (DECISIONS 25) -------------


@pytest.mark.parametrize("text,kept", [
    ("Interpreter: Carla Jiménez (Spanish, on-site)\nPrimary Language(s): Spanish (home); English (emerging)",
     ["(Spanish, on-site)", "Spanish (home); English"]),
    ("CG Rosa voiced concern. The primary daytime CG is grandma.", ["CG ", "daytime CG"]),
    ("Location: [X] Home  [X] Daycare\nHe is called Teo at home.", ["Home", "at home"]),
    ("Date of Service: October 8, 2026", ["Date of Service"]),
    ("Seen by Dr. Samuel Brooks, Au.D., today.", ["Au.D."]),
])
def test_common_words_are_not_names_or_places(text, kept):
    r = deidentify(text, pf_detect=lambda t: [])
    for k in kept:
        assert k in r.output
    assert not r.failures


def test_unsure_name_parts_spread_to_other_mentions():
    from localdeid.pf.model import PFSpan

    t = 'Pt: Jose "Pepe" Hernandez Martinez\nS: CG reports Pepe used 3-word phrases.'
    s = t.index("Jose")
    r = deidentify(t, pf_detect=lambda x: [PFSpan(s, t.index('"', t.index("Pepe")), "private_person", 0.3)])
    assert "Pepe" not in r.output and not r.failures


# --- dev3 batch (DECISIONS 25 work order) --------------------------------------


@pytest.mark.parametrize("text", [
    "- Apgar al nacer: 8/9.",
    "Progress toward Outcome 1 (8/10, 80%, up from baseline 3/10)",
    "he was presented with the target sol (sun) alongside casa",
    "Evaluación Clínica de Fundamentos del Lenguaje, Preescolar-3 (CELF Preescolar-3) | Núcleo",
    "c) Escalas Bayley de Desarrollo Infantil-4 (Bayley Scales of Infant and Toddler Development)",
])
def test_dev3_clinical_content_stays(text):
    from localdeid.roster import Child, Related

    s = Settings(children=[Child("Mateo", ["Ruiz"], related=[Related("Sol", "sister")])])
    r = deidentify(text, s, pf_detect=lambda t: [])
    assert r.output == r.source


@pytest.mark.parametrize("text,gone", [
    ("Referida por la Agencia de Coordinación de Servicios para la Primera Infancia del Piamonte (ACSPIP). "
     "ACSPIP llamó.", ["Piamonte", "ACSPIP"]),
    ("Kim, del Programa Regional de Intervención Temprana y Apoyo a la Familia (PRITAF). El equipo de PRITAF.",
     ["Temprana y Apoyo", "PRITAF"]),
    ("Enrolled in the NC Infant-Toddler Program since May.", ["Infant-Toddler"]),
    ("Coordinadora de CSF de Wake y ACSI de Guilford.", ["CSF", "ACSI", "Guilford"]),
])
def test_spanish_agency_long_forms_and_acronyms(text, gone):
    r = deidentify(text, pf_detect=lambda t: [])
    for g in gone:
        assert g not in r.output
    assert not r.failures


def test_clinical_acronym_before_a_town_is_not_an_agency():
    r = deidentify("She sees OT Raleigh weekly.", pf_detect=lambda t: [])
    assert "OT" in r.output


def test_gliner_job_title_is_not_an_org():
    t = "Mom works the night shift as CNA at a hospital."
    s = t.index("CNA")
    r = deidentify(t, pf_detect=lambda x: [], org_detect=lambda x: [OrgSpan(s, s + 3, "organization", 0.9)])
    assert "CNA" in r.output


def test_scorer_trailing_lowercase_descriptor_is_optional():
    from localdeid.evaluate import required_chars

    t = "Guilford County CDSA office"
    assert "".join(t[i] for i in required_chars(t, 0, len(t), "AGENCY")) == "GuilfordCountyCDSA"


# --- review round 27 --------------------------------------------------------


def test_common_word_surnames_with_name_evidence_stay_hidden():
    from localdeid.roster import Child

    r = deidentify("Child: Early\nEarly smiled.", Settings(children=[Child("Early")]), pf_detect=lambda t: [])
    assert "Early" not in r.output
    r = deidentify("Seen by Dr. Sam English today.", pf_detect=lambda t: [])
    assert "English" not in r.output


def test_apgar_does_not_protect_a_nearby_birth_date():
    r = deidentify("Apgar reviewed. DOB 3/10/2022.", pf_detect=lambda t: [])
    assert "3/10" not in r.output


def test_date_range_is_not_a_score_range():
    from localdeid.protect import protect

    t = "Date range: 3-10 through 3-12."
    assert not [p for p in protect(t) if p.reason == "score"]


def test_never_name_words_in_names_are_not_case_folded_leaks():
    from localdeid.roster import Child, Related

    s = Settings(children=[Child("Mateo", ["Ruiz"], related=[Related("Alan", "teacher", ["Primary Care"])])])
    r = deidentify("Alan Primary Care called. He gets routine pediatric care.", s, pf_detect=lambda t: [])
    assert not r.failures and "routine pediatric care" in r.output


def test_name_field_ends_before_credentials_and_descriptor():
    r = deidentify("Evaluator: Mara Jennings, M.S., CCC-SLP, Bilingual (English/Spanish)", pf_detect=lambda t: [])
    assert r.output.endswith(", M.S., CCC-SLP, Bilingual (English/Spanish)") and "Jennings" not in r.output


# --- review round 28 --------------------------------------------------------


def test_maiden_name_in_parentheses_keeps_the_surname_hidden():
    r = deidentify("Mother: Ana (Early) Salazar", pf_detect=lambda t: [])
    assert "Salazar" not in r.output and "Ana" not in r.output


def test_model_person_span_drops_a_trailing_descriptor():
    from localdeid.pf.model import PFSpan

    t = "Seen with Ana bilingual today."
    s = t.index("Ana")
    r = deidentify(t, pf_detect=lambda x: [PFSpan(s, s + len("Ana bilingual"), "private_person", 0.9)])
    assert "bilingual today" in r.output and "Ana" not in r.output


def test_apgar_after_birth_history_descriptor_stays():
    t = "Apgar (born at term): 8/9."
    assert deidentify(t, pf_detect=lambda x: []).output == t


def test_soft_word_name_from_a_field_propagates():
    r = deidentify("Child: Sam English\nEnglish smiled.", pf_detect=lambda t: [])
    assert "English" not in r.output and not r.failures


# --- review round 29 --------------------------------------------------------


@pytest.mark.parametrize("text,gone", [("Apgar unknown, born 8/9.", "8/9"),
                                       ("Apgar unknown, born 08/09/2022.", "08/09"),
                                       ("Apgar 8/9; nació el 3/10/2021.", "3/10")])
def test_birth_dates_near_apgar_are_hidden(text, gone):
    assert gone not in deidentify(text, pf_detect=lambda t: []).output


@pytest.mark.parametrize("text", ["Apgar (born at term): 8/9.", "Apgar scores 8 and 9, born at 38 weeks."])
def test_apgar_scores_with_birth_descriptors_stay(text):
    assert deidentify(text, pf_detect=lambda t: []).output == text


@pytest.mark.parametrize("text", ["Apgar del recién nacido: 9/8.", "Apgar (nació a término): 9/8.",
                                  "Apgar 9/8 at 1 and 5 minutes."])
def test_declining_apgar_after_spanish_descriptors_stays(text):
    assert deidentify(text, pf_detect=lambda t: []).output == text


# --- review round 31: Apgar decided by the word before the numbers ----------


@pytest.mark.parametrize("text,gone", [("Apgar no registrado, nació 8/9.", "8/9"),
                                       ("Apgar desconocido, nació: 8/9.", "8/9"),
                                       ("Apgar unknown, DOB 3/10.", "3/10")])
def test_birth_cue_right_before_numbers_makes_a_date(text, gone):
    assert gone not in deidentify(text, pf_detect=lambda t: []).output


# --- review round 32: Apgar area closed (DECISIONS 24) ----------------------


@pytest.mark.parametrize("text,gone", [("Apgar unavailable, DOB was 9/8.", "9/8")])
def test_date_cue_anywhere_in_the_gap_makes_a_date(text, gone):
    assert gone not in deidentify(text, pf_detect=lambda t: []).output


@pytest.mark.parametrize("text", ["Apgar al nacimiento: 9/8.", "Apgar (nació a término): 9/8.",
                                  "Apgar del recién nacido: 9/8.", "Apgar (born at term): 8/9."])
def test_birth_history_descriptors_keep_the_apgar_score(text):
    assert deidentify(text, pf_detect=lambda t: []).output == text


def test_scorer_id_label_and_state_are_optional():
    from localdeid.evaluate import required_chars

    t = "ID #SLP-48217-C"
    assert "".join(t[i] for i in required_chars(t, 0, len(t), "ID")) == "SLP48217C"
    t = "Greensboro, Carolina del Norte"
    assert "".join(t[i] for i in required_chars(t, 0, len(t), "PLACE")) == "Greensboro"


@pytest.mark.parametrize("text,gone", [
    ("Date | SS | %ile | CI\n08/14/2026 | 71 | 3rd | 65-77", "08/14"),
    ("con oficina en 204 Avenida Roxboro, Durham.", "Durham"),
    ("CDSA, 204 Birchwood Lane, Raleigh, 27610\nNext", "27610"),
])
def test_dev3_daily_note_misses_are_hidden(text, gone):
    assert gone not in deidentify(text, pf_detect=lambda t: []).output


def test_ci_with_a_range_stays_protected():
    from localdeid.pf.model import PFSpan

    t = "standard score 78 (8th percentile) with 90% CI 73-83; consistent"
    s = t.index("CI")
    r = deidentify(t, pf_detect=lambda x: [PFSpan(s, s + 2, "account_number", 0.9)])
    assert r.output == t


# --- review round 33 --------------------------------------------------------


def test_duration_comma_is_not_an_address():
    t = "Duration: 10 minutes, Bath time practiced with picture cards."
    assert deidentify(t, pf_detect=lambda x: []).output == t


def test_multiline_score_field_stays_protected():
    from localdeid.pf.model import PFSpan

    t = "CI\n73-83"
    r = deidentify(t, pf_detect=lambda x: [PFSpan(0, 2, "account_number", 0.9)])
    assert r.output == t


@pytest.mark.parametrize("value,typ,req", [("Numero 12345", "ID", "12345"), ("IDSLP-48217-C", "ID", "IDSLP48217C"),
                                           ("Azusa", "PLACE", "Azusa"), ("Mont Blanc", "PLACE", "MontBlanc"),
                                           ("Raleigh, NC", "PLACE", "Raleigh")])
def test_scorer_label_and_state_need_boundaries(value, typ, req):
    from localdeid.evaluate import required_chars

    assert "".join(value[i] for i in required_chars(value, 0, len(value), typ)) == req


# --- review round 34 --------------------------------------------------------


@pytest.mark.parametrize("text,gone", [("Date | SS | %ile | CI\n08.14.2026 | 71", "08.14"),
                                       ("Home visit: 204 Maple Terrace, Durham.", "Durham")])
def test_round34_dates_and_cities_are_hidden(text, gone):
    assert gone not in deidentify(text, pf_detect=lambda t: []).output


def test_scorer_state_after_nbsp_is_optional():
    from localdeid.evaluate import required_chars

    v = "Raleigh NC"
    assert "".join(v[i] for i in required_chars(v, 0, len(v), "PLACE")) == "Raleigh"


# --- review round 35 --------------------------------------------------------


@pytest.mark.parametrize("text,gone", [
    ("Fecha | SS | CI\n3 de marzo de 2026 | 71 | 73-83", "3 de marzo"),
    ("Home visit: 204 Magnificent Whispering Meadows Avenue, Durham.", "Durham"),
    ("Home visit: 204 Oak ST, Durham.", "Durham"),
    ("Visita: 204 avenida Roxboro, Durham.", "Durham"),
])
def test_round35_table_rows_and_cities_are_hidden(text, gone):
    from localdeid.pf.model import PFSpan

    spans = []
    if "marzo" in text:
        s = text.index("3 de marzo")
        spans = [PFSpan(s, s + len("3 de marzo de 2026"), "private_date", 0.99)]
    r = deidentify(text, pf_detect=lambda t: spans)
    assert gone not in r.output


# --- review round 36 --------------------------------------------------------


def test_indented_multiline_score_stays_protected():
    from localdeid.pf.model import PFSpan

    t = "\tSS\n78"
    r = deidentify(t, pf_detect=lambda x: [PFSpan(1, 3, "account_number", 0.9)])
    assert r.output == t


def test_number_and_lowercase_place_word_is_not_an_address():
    t = "Duration: 10 minutes in place, Bath time practiced with picture cards."
    assert deidentify(t, pf_detect=lambda x: []).output == t


def test_phone_keeps_its_opening_parenthesis():
    from localdeid.pipeline import deidentify

    r = deidentify("Mother: Lucía Ruiz (919) 555-0142\nCall (919) 555-0142 today.")
    assert "([PHONE" not in r.output and "(919" not in r.output
    assert r.output.count("[PHONE_1]") == 2 and not r.blocked


def test_strip_keeps_only_partnered_parentheses():
    from localdeid.spans import _strip

    t = "x (919) 555-0142 y"
    assert t[slice(*_strip(t, 2, 16))] == "(919) 555-0142"
    t = "at Little Sprouts (LS) today"
    assert t[slice(*_strip(t, 3, 22))] == "Little Sprouts (LS)"
    t = "(Ana)"
    assert t[slice(*_strip(t, 0, 5))] == "Ana"  # balanced on both sides: both go
    t = "(see Ana)"
    assert t[slice(*_strip(t, 5, 9))] == "Ana"


def test_sanitize_labels_unlabelled_repeats_of_a_labelled_identifier():
    from localdeid.evaldata import Doc, GoldSpan, sanitize

    text = ("Mom works at Ingles Market. Carolina called. Ingles Market again; the family moved to "
            "Carolina del Norte and North Carolina. Mark the date.")
    spans = [GoldSpan(text.index("Ingles", 20), text.index("Ingles", 20) + 13, "ORG"),
             GoldSpan(text.index("Carolina called"), text.index("Carolina called") + 8, "PARENT")]
    doc, n = sanitize(Doc("t", text, spans, {}))
    got = sorted((text[s.start:s.end], s.type, s.start) for s in doc.identifiers)
    assert [g[:2] for g in got].count(("Ingles Market", "ORG")) == 2
    assert [g[:2] for g in got].count(("Carolina", "PARENT")) == 1  # the state names stay unlabelled
    assert n == 1


def test_label_repair_never_labels_a_clinical_term():
    from localdeid.evaldata import Doc, GoldSpan, sanitize

    text = "The IFSP meeting is set. Next IFSP review in May. IFSP outcomes attached."
    doc, _ = sanitize(Doc("t", text, [GoldSpan(4, 8, "AGENCY")], {}))  # a wrong seed label
    assert sum(1 for s in doc.identifiers if text[s.start:s.end] == "IFSP") <= 1


def test_label_repair_keeps_a_repeated_name_with_credentials():
    from localdeid.evaldata import Doc, GoldSpan, sanitize

    name = "Daniela Ortiz Vega, M.S., CCC-SLP"
    text = f"Provider: {name}\nGoals reviewed.\nSigned: {name}"
    doc, _ = sanitize(Doc("t", text, [GoldSpan(10, 10 + len(name), "PROVIDER")], {}))
    assert sum(1 for s in doc.identifiers if text[s.start:s.end] == name) == 2
