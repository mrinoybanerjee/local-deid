"""Unit tests for every detector stage, English and Spanish (SPEC 4.8)."""

import pytest

from localdeid import fields, gazetteer, roster, rules
from localdeid.protect import protect
from localdeid.roster import Child, Related


def protected_texts(text, **kw):
    return {text[p.start:p.end] for p in protect(text, **kw)}


def rule_hits(text, kind=None):
    return {(text[c.start:c.end], c.kind) for c in rules.find(text) if kind is None or c.kind == kind}


# --------------------------------------------------------------------------- protect


@pytest.mark.parametrize("text,expected", [
    ("produced /k/ in 7/10 trials (70%)", {"/k/", "7/10", "70%"}),
    ("logró 8 de 10 intentos con apoyo", {"8 de 10"}),
    ("ocho de diez imágenes", {"ocho de diez"}),
    ("imitated 4 of 6 models", {"4 of 6"}),
    ("CPT 92507; ICD-10 F80.2; ICD: R48", {"92507", "F80.2", "R48"}),
    ("Age 4;2, CA: 3;10", {"4;2", "3;10"}),
    ("SS 78, 7th percentile, -1.5 SD", {"SS 78", "7th percentile", "-1.5 SD"}),
    ("PLS-5 Spanish total; CELF Preschool-3 given", {"PLS-5 Spanish", "CELF Preschool-3"}),
    ("[w] for /r/, [ˈpeɾo], [EW:goed]", {"[w]", "/r/", "[ˈpeɾo]", "[EW:goed]"}),
    ('said "wabbit" and “tat”', {"wabbit", "tat"}),
    ("Outcome 2 and Goal #3", {"Outcome 2", "Goal #3"}),
])
def test_protect_clinical_tokens(text, expected):
    assert expected <= protected_texts(text)


@pytest.mark.parametrize("text,not_expected", [
    ("Miss Rossetti reported progress", "Rossetti"),
    ("la maestra Bayley dijo", "Bayley"),
    ("next visit 8/10", "8/10"),  # no trial word: a date, not a score
    ("Lives at 92507 Old Main St", "92507"),  # a house number, not a CPT code
    ("Guðrún visited Hawaiʻi", "Guðrún"),
    ("on 3/4/2026", "3/4"),
])
def test_protect_does_not_shield_identifiers(text, not_expected):
    assert not_expected not in protected_texts(text)


def test_field_label_is_not_an_org():
    hits = rule_hits("Phone: (919) 555-0142\nDaycare: Little Acorns Learning Center")
    assert ("Daycare", "DAYCARE") not in hits
    assert ("Little Acorns Learning Center", "DAYCARE") in hits


def test_protect_target_words():
    assert "sol" in protected_texts("dijo sol y luna", target_words=["sol"])


# --------------------------------------------------------------------------- rules


@pytest.mark.parametrize("text,date", [
    ("seen on 03/14/2021.", "03/14/2021"),
    ("DOB 3-14-21", "3-14-21"),
    ("on 2026-03-14", "2026-03-14"),
    ("on March 3, 2026 we met", "March 3, 2026"),
    ("Monday, March 3rd", "Monday, March 3rd"),
    ("the 3rd of March, 2026", "3rd of March, 2026"),
    ("el 5 de marzo de 2026", "5 de marzo de 2026"),
    ("martes 3 de marzo", "martes 3 de marzo"),
    ("primero de enero", "primero de enero"),
    ("marzo 3, 2026", "marzo 3, 2026"),
    ("en marzo de 2026", "marzo de 2026"),
    ("el 14/3", "14/3"),
])
def test_dates(text, date):
    assert (date, "DATE") in rule_hits(text)


def test_dates_negative():
    hits = rule_hits("You may go. Mark it. 25/40 is not a date. hace dos semanas")
    assert not any(k == "DATE" for _, k in hits)


@pytest.mark.parametrize("text,value,kind", [
    ("from 9:30-10:15 AM", "9:30-10:15 AM", "TIME"),
    ("at 3pm", "3pm", "TIME"),
    ("call (919) 555-0142", "(919) 555-0142", "PHONE"),
    ("al 919.555.0142", "919.555.0142", "PHONE"),
    ("cel +52 55 1234 5678", "+52 55 1234 5678", "PHONE"),
    ("tel: 555-0142", "555-0142", "PHONE"),
    ("email ana.r@example.com.", "ana.r@example.com", "EMAIL"),
    ("see https://example.org/x?y=1.", "https://example.org/x?y=1", "URL"),
    ("lives at 1234 N. Oak Ridge Dr., Apt 5B", "1234 N. Oak Ridge Dr., Apt 5B", "PLACE"),
    ("Cary, NC 27513", "27513", "PLACE"),
    ("Calle Morelos #45", "Calle Morelos #45", "PLACE"),
    ("Medicaid #: 912345678K", "912345678K", "MEDICAID_ID"),
    ("MRN A1234567", "A1234567", "ID"),
    ("SSN 123-45-6789", "123-45-6789", "ID"),
])
def test_format_rules(text, value, kind):
    assert (value, kind) in rule_hits(text)


@pytest.mark.parametrize("text,name,kind", [
    ("Su mamá, Lucía Rodríguez, dijo", "Lucía Rodríguez", "PARENT"),
    ("his mother Ana reported", "Ana", "PARENT"),
    ("su hermana Sol vino", "Sol", "SIBLING"),
    ("tía Carmen", "Carmen", "FAMILY"),
    ("her dog Max", "Max", "PET"),
    ("su amigo Diego", "Diego", "PEER"),
    ("seen by Dr. Patel", "Patel", "PROVIDER"),
    ("la maestra Pérez", "Pérez", "TEACHER"),
    ("Ms. Lopez's class", "Lopez", "TEACHER"),
    ("doña Lupe", "Lupe", "PERSON"),
    ("Ms. Lopez reported", "Lopez", "PERSON"),
    ("Mom works nights at Food Lion.", "Food Lion", "ORG"),
    ("attends Little Acorns Learning Center", "Little Acorns Learning Center", "DAYCARE"),
    ("Briarwood Elementary School", "Briarwood Elementary School", "SCHOOL"),
    ("Escuela Primaria Benito Juárez", "Escuela Primaria Benito Juárez", "SCHOOL"),
    ("Guardería Los Pinitos", "Guardería Los Pinitos", "DAYCARE"),
    ("NICU at WakeMed", "WakeMed", "HOSPITAL"),
    ("born at Duke Regional Hospital", "Duke Regional Hospital", "HOSPITAL"),
    ("the Durham CDSA", "Durham CDSA", "AGENCY"),
    ("DSS is involved", "DSS", "AGENCY"),
    ("vive en Apex", "Apex", "PLACE"),
])
def test_triggers(text, name, kind):
    assert (name, kind) in rule_hits(text)


@pytest.mark.parametrize("text", [
    "Mom said Mateo likes trains.",  # no name right after the kin word
    "es de Oaxaca",  # a state: allowed
    "vive en México",  # a country: allowed
    "la maestra dijo que sí",
])
def test_triggers_negative(text):
    hits = rule_hits(text)
    assert not any(k in ("PARENT", "PLACE", "TEACHER") and v not in ("Mateo",) for v, k in hits), hits


# --------------------------------------------------------------------------- fields


def field_hits(text, **kw):
    return [(text[h.start:h.end], h.kind) for h in fields.parse(text, **kw)]


def test_fields_inline_and_multi():
    t = "Name: Mateo Ramírez        DOB: 03/14/2021\nParent/Guardian: Lucía Rodríguez | Phone: (919) 555-0142"
    assert field_hits(t) == [("Mateo Ramírez", "CHILD"), ("03/14/2021", "DATE"),
                             ("Lucía Rodríguez", "PARENT"), ("(919) 555-0142", "PHONE")]


def test_fields_spanish_salt_and_tables():
    t = ("+ Name: Mateo\nFecha de evaluación: 5 de marzo de 2026\n"
         "Name\tDOB\tSchool\nSofía Pérez\t2/2/2022\tBriarwood Elementary\n\n"
         "Examiner\tValeria López, M.S., CCC-SLP\n")
    assert field_hits(t) == [("Mateo", "CHILD"), ("5 de marzo de 2026", "DATE"),
                             ("Sofía Pérez", "CHILD"), ("2/2/2022", "DATE"),
                             ("Briarwood Elementary", "SCHOOL"), ("Valeria López", "PROVIDER")]


def test_fields_reject_prose_and_empty():
    t = "Mother: reports that Mateo is talking more.\nTeacher: N/A\nDOB: unknown\nSubtest\tRaw\tStandard"
    assert field_hits(t) == []


def test_fields_trim_honorific_relation_credentials():
    assert field_hits("Physician: Dr. Amit Patel, MD") == [("Amit Patel", "PROVIDER")]
    assert field_hits("Parent: Lucía Rodríguez (mamá)") == [("Lucía Rodríguez", "PARENT")]


def test_fields_custom_label():
    assert field_hits("Abuelita: Rosa Díaz", extra_labels={"Abuelita": "FAMILY"}) == [("Rosa Díaz", "FAMILY")]


# --------------------------------------------------------------------------- roster


KIDS = [
    Child("Mateo", ["Ramírez"], ["Teo"], [Related("Lucía", "mother"), Related("Sol", "sister")],
          ["Little Acorns Learning Center"], ["sol"]),
    Child("José María", ["de la Cruz", "Peña"]),
    Child("Guadalupe", ["Hernández López"]),
    Child("Valentina", ["Okafor"]),
]


def roster_hits(text):
    return {(text[c.start:c.end], c.kind, c.sure) for c in roster.find(text, KIDS)}


def test_roster_case_accent_nickname_diminutive():
    hits = roster_hits("MATEO, Teo y Mateito. Jose maria de la cruz. Lupita y Lupe.")
    for h in [("MATEO", "CHILD", True), ("Teo", "CHILD", True), ("Mateito", "CHILD", True),
              ("Jose maria", "CHILD", True), ("de la cruz", "SURNAME", True),
              ("Lupita", "CHILD", True), ("Lupe", "CHILD", True)]:
        assert h in hits


def test_roster_word_names_and_short_folds_are_unsure():
    hits = roster_hits("su hermana Sol dijo sol. la pena; Peña.")
    assert ("Sol", "SIBLING", False) in hits and ("sol", "SIBLING", False) in hits
    assert ("pena", "SURNAME", False) in hits and ("Peña", "SURNAME", True) in hits


def test_roster_fuzzy_long_names_only():
    hits = roster_hits("Valentena came; Matteo left.")
    assert ("Valentena", "CHILD", False) in hits  # 9 letters, one edit
    assert not any(h[0] == "Matteo" for h in hits)  # Mateo has 5 letters: no fuzzy


def test_roster_surname_parts_and_hyphen():
    hits = roster_hits("Hernandez-Lopez and López")
    assert ("Hernandez", "SURNAME", True) in hits and ("López", "SURNAME", True) in hits


def test_roster_empty():
    assert roster.find("anything", []) == []


def test_diminutives():
    assert {"juanito", "juanita"} <= roster.diminutives("juan")
    assert "carlitos" in roster.diminutives("carlos")
    assert "anita" in roster.diminutives("ana")
    assert "paquito" in roster.diminutives("paco")
    assert "marquitos" in roster.diminutives("marcos")
    assert "tomasito" in roster.diminutives("tomas")


# --------------------------------------------------------------------------- gazetteer


def gaz(text):
    return {(text[c.start:c.end], c.sure, c.note) for c in gazetteer.find(text)}


def test_gazetteer_context_pair_bare():
    hits = gaz("We met with Grace Park and her mom Lucia. Then Will arrived. su hermana Carmen vino.")
    assert ("Park", True, "gazetteer:name-pair") in hits
    assert ("Grace", False, "gazetteer:name-pair") in hits  # word-name: unsure
    assert ("Lucia", True, "gazetteer:context") in hits
    assert ("Carmen", True, "gazetteer:context") in hits
    assert ("Will", False, "gazetteer:bare") in hits


def test_gazetteer_skips_lowercase_initial_and_stoplist():
    hits = gaz("Mom said hello. English and Spanish. Monday came. lucia vino.")
    assert hits == set()


def test_female_share():
    assert gazetteer.female_share("Valentina") > 0.9
    assert gazetteer.female_share("Mateo") < 0.1
    assert gazetteer.female_share("Zzzzqx") is None
