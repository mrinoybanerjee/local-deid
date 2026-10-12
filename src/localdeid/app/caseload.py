"""Caseload text format (SPEC 4.6, 5.2: "Type your kids' names, one per line").

One child per line: the name first (given name, then surnames), then optional
parts after semicolons. Lines starting with # are comments.

    Mateo Ruiz López; nickname Teo; mom Lucía; sister Sol; school Little Sprouts; targets sol, pan
    Juan Pablo Ruiz; first Juan Pablo

The first word is the given name unless a "first" (or "nombre") part names a
longer given name, which must start the line.

Parts (English or Spanish keys): nickname/apodo, mom/mamá/mother/madre,
dad/papá/father/padre, sister/hermana, brother/hermano, grandma/abuela,
grandpa/abuelo, aunt/tía, uncle/tío, family/familia, caregiver/cuidadora,
teacher/maestra/maestro, pet/mascota, school/escuela/daycare/guardería,
targets/palabras. A relative may have a surname: "mom Lucía Ruiz".
No addresses, phone numbers, IDs, or dates of birth: rules find those by format.
"""

from __future__ import annotations

import re

from ..roster import Child, Related

_REL = {
    "mom": "mother", "mamá": "mother", "mama": "mother", "mother": "mother", "madre": "mother",
    "dad": "father", "papá": "father", "papa": "father", "father": "father", "padre": "father",
    "sister": "sister", "hermana": "sister", "brother": "brother", "hermano": "brother",
    "grandma": "grandmother", "abuela": "grandmother", "grandpa": "grandfather", "abuelo": "grandfather",
    "aunt": "aunt", "tía": "aunt", "tia": "aunt", "uncle": "uncle", "tío": "uncle", "tio": "uncle",
    "family": "family", "familia": "family", "caregiver": "family", "cuidadora": "family", "cuidador": "family",
    "teacher": "teacher", "maestra": "teacher", "maestro": "teacher", "pet": "pet", "mascota": "pet",
}
_NICK = {"nickname", "nick", "apodo"}
_FIRST = {"first", "given", "nombre"}
_SCHOOL = {"school", "escuela", "daycare", "guardería", "guarderia", "preschool"}
_TARGETS = {"targets", "target", "palabras", "words"}
_DIGITS = re.compile(r"\d{3,}")


class CaseloadError(ValueError):
    def __init__(self, line: int, message: str):
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


def parse(text: str) -> list[Child]:
    kids: list[Child] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _DIGITS.search(line):
            raise CaseloadError(n, "numbers are not needed here (no phone numbers, IDs, or dates of birth)")
        parts = [p.strip() for p in line.split(";")]
        name = parts[0].split()
        if not name:
            raise CaseloadError(n, "start the line with the child's name")
        given = 1
        for p in parts[1:]:
            words = p.split(None, 1)
            if len(words) == 2 and words[0].lower().rstrip(":") in _FIRST:
                g = words[1].split()
                if [w.casefold() for w in name[:len(g)]] != [w.casefold() for w in g]:
                    raise CaseloadError(n, f"'{words[1].strip()}' must be the start of the name")
                given = len(g)
        child = Child(" ".join(name[:given]), [" ".join(name[given:])] if len(name) > given else [])
        for p in parts[1:]:
            if not p:
                continue
            words = p.split(None, 1)
            key = words[0].lower().rstrip(":")
            value = words[1].strip() if len(words) > 1 else ""
            if not value:
                raise CaseloadError(n, f"'{words[0]}' needs a name after it")
            if key in _FIRST:
                continue  # read above
            if key in _NICK:
                child.nicknames += [v.strip() for v in value.split(",") if v.strip()]
            elif key in _SCHOOL:
                child.schools.append(value)
            elif key in _TARGETS:
                child.target_words += [v.strip() for v in value.split(",") if v.strip()]
            elif key in _REL:
                for person in value.split(","):
                    pw = person.split()
                    if pw:
                        child.related.append(Related(pw[0], _REL[key], [" ".join(pw[1:])] if len(pw) > 1 else []))
            else:
                raise CaseloadError(n, f"unknown part '{words[0]}' (try mom, sister, school, nickname, targets)")
        kids.append(child)
    return kids
