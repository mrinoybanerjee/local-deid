"""Generate the synthetic harness inputs (deterministic; all names are fake).

These texts load the model with realistic length, language mix, and layout
(header fields, tab-separated score tables, prose). Every planted identifier
is recorded with exact code-point offsets, so the harness can report recall
per type for each runtime. They are not the dev or gate sets (SPEC section 7).

    .venv/bin/python harness/gen_inputs.py   # writes harness/inputs/*.txt and *.json
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path

OUT = Path(__file__).resolve().parent / "inputs"

FIRST = ["Mateo", "Sofía", "Liam", "Valentina", "Noah", "Camila", "Elijah", "Lucía",
         "Santiago", "Ava", "Diego", "Isabella", "Leo", "Ximena", "Owen", "Renata"]
LAST = ["Ramírez", "Okafor", "Delgado", "Whitfield", "Castillo", "Brennan", "Herrera",
        "Nakamura", "Villanueva", "Pruitt", "Salazar", "Lindqvist"]
PLACES = ["Little Acorns Learning Center", "Briarwood Elementary", "Guardería Los Pinitos",
          "Maple Hollow Daycare", "Escuela Primaria Juárez"]
TESTS = ["PLS-5", "CELF Preschool-3", "GFTA-3", "REEL-4", "PLS-5 Spanish", "CASL-2"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]

# Template fields: c child first name, p parent full name, s school, t teacher
# surname, x test name (clinical, not an identifier), n score, d date, q phone.
FIELD_TYPES = {"c": "PERSON", "p": "PERSON", "s": "SCHOOL", "t": "PERSON", "d": "DATE",
               "q": "PHONE", "x": None, "n": None}

EN = [
    "{c} engaged in a play-based session targeting two-word combinations during snack routines.",
    "Given moderate verbal cues, {c} produced initial /k/ in CV words with 7/10 accuracy.",
    "{p} reported that {c} has started using more words at home, especially at bath time.",
    "The clinician modeled expansions; {c} imitated 4 of 6 models following a 5-second pause.",
    "{c} attended {s} three days a week, where Ms. {t} reported limited peer interaction.",
    "Results of the {x} indicated a standard score of {n}, which is below the average range.",
    "Session time 9:30-10:15 AM; CPT 92507; ICD-10 F80.2. Next visit {d}.",
    "{c} labeled 12 of 20 pictured vocabulary items and followed one-step directions with gestures.",
    "Parent coaching focused on wait time and offering choices during play with {c}.",
    "Phone contact with {p} at {q} confirmed the schedule for {d}.",
]
ES = [
    "{c} participó en una sesión basada en el juego enfocada en combinaciones de dos palabras.",
    "Con apoyo verbal moderado, {c} produjo /k/ inicial en palabras CV en 7 de 10 intentos.",
    "Su mamá, {p}, comentó que {c} ya usa más palabras en casa, sobre todo a la hora del baño.",
    "La terapeuta modeló expansiones; {c} imitó 4 de 6 modelos después de una pausa de 5 segundos.",
    "{c} asiste a {s} tres días por semana, donde la maestra {t} reportó poca interacción.",
    "Los resultados del {x} indicaron una puntuación estándar de {n}, por debajo del promedio.",
    "Hora de la sesión 9:30-10:15; CPT 92507. La próxima visita es el {d}.",
    "{c} nombró 12 de 20 imágenes de vocabulario y siguió instrucciones de un paso con gestos.",
    "El entrenamiento a los padres se enfocó en el tiempo de espera y en ofrecer opciones.",
    "Se habló por teléfono con {p} al {q} para confirmar la cita del {d}.",
]


class Doc:
    """Text builder that records the offsets of every planted identifier."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.spans: list[dict] = []

    def lit(self, s: str) -> None:
        self.parts.append(s)
        self.length += len(s)

    def ent(self, kind: str | None, s: str) -> None:
        if kind is not None:
            self.spans.append({"start": self.length, "end": self.length + len(s), "type": kind})
        self.lit(s)

    def text(self) -> str:
        return "".join(self.parts)

    def words(self) -> int:
        return len(self.text().split())


def _phone(rng: random.Random, spanish: bool) -> str:
    n = f"{rng.randint(0, 9999):04d}"
    return f"919-555-{n}" if spanish else f"(919) 555-{n}"


def _date(rng: random.Random, spanish: bool) -> str:
    if spanish:
        return f"{rng.randint(1, 28)} de {rng.choice(MONTHS_ES)} de 2026"
    return f"{rng.randint(1, 12)}/{rng.randint(1, 28)}/2026"


def _sentence(doc: Doc, rng: random.Random, tpl: str, child: str, spanish: bool) -> None:
    values = {
        "c": child, "p": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "s": rng.choice(PLACES),
        "t": rng.choice(LAST), "x": rng.choice(TESTS), "n": str(rng.randint(62, 84)),
        "d": _date(rng, spanish), "q": _phone(rng, spanish),
    }
    for literal, field, _, _ in string.Formatter().parse(tpl):
        doc.lit(literal)
        if field is not None:
            doc.ent(FIELD_TYPES[field], values[field])


def _header(doc: Doc, rng: random.Random, child: str, spanish: bool) -> None:
    labels = (["Nombre", "Fecha de nacimiento", "Padre/Madre", "Teléfono", "Escuela", "Medicaid #"]
              if spanish else ["Name", "DOB", "Parent/Guardian", "Phone", "Daycare", "Medicaid #"])
    dob = (f"{rng.randint(1, 28)}/{rng.randint(1, 12)}/2022" if spanish
           else f"{rng.randint(1, 12)}/{rng.randint(1, 28)}/2022")
    values = [("PERSON", child), ("DATE", dob),
              ("PERSON", f"{rng.choice(FIRST)} {rng.choice(LAST)}"), ("PHONE", _phone(rng, spanish)),
              ("SCHOOL", rng.choice(PLACES)), ("ID", f"9{rng.randint(10**8, 10**9 - 1)}")]
    for label, (kind, value) in zip(labels, values):
        doc.lit(f"{label}: ")
        doc.ent(kind, value)
        doc.lit("\n")
    doc.lit("\n")


def _table(doc: Doc, rng: random.Random) -> None:
    doc.lit("Subtest\tRaw\tStandard\tPercentile\n")
    for name in ("Auditory Comprehension", "Expressive Communication", "Total Language"):
        doc.lit(f"{name}\t{rng.randint(20, 50)}\t{rng.randint(60, 90)}\t{rng.randint(1, 30)}\n")
    doc.lit("\n")


def make_doc(seed: int, words: int, spanish_ratio: float) -> Doc:
    rng = random.Random(seed)
    child = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    doc = Doc()
    _header(doc, rng, child, spanish_ratio > 0.5)
    _table(doc, rng)
    in_para = 0
    target_len = rng.randint(4, 7)
    while doc.words() < words:
        spanish = rng.random() < spanish_ratio
        if in_para:
            doc.lit(" ")
        _sentence(doc, rng, rng.choice(ES if spanish else EN), child.split()[0], spanish)
        in_para += 1
        if in_para >= target_len:
            doc.lit("\n\n")
            in_para, target_len = 0, rng.randint(4, 7)
            if rng.random() < 0.08:
                _table(doc, rng)
    if in_para:
        doc.lit("\n")
    return doc


DOCS = {
    "note_en_500": (101, 500, 0.0),
    "note_es_500": (102, 500, 1.0),
    "eval_mixed_5000": (201, 5000, 0.5),
    "eval_es_5000": (202, 5000, 1.0),
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, (seed, words, ratio) in DOCS.items():
        doc = make_doc(seed, words, ratio)
        text = doc.text()
        for s in doc.spans:  # self-check: offsets point at the planted values
            assert text[s["start"]:s["end"]].strip() == text[s["start"]:s["end"]], s
        (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
        (OUT / f"{name}.json").write_text(json.dumps({"spans": doc.spans}, indent=0) + "\n")
        print(f"{name}: {doc.words()} words, {len(text)} chars, {len(doc.spans)} identifiers")


if __name__ == "__main__":
    main()
