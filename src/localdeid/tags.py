"""Tag vocabulary and the tolerant tag grammar shared by render and restore.

Canonical tags look like `[CHILD_1]`; `[EMPLOYER]` has no number (SPEC 6).
The tolerant parser (SPEC 4.5) also reads what LLMs write back: any case,
spaces or hyphens for underscores, a missing underscore, Spanish labels
(`[NIÑO_1]`, `[MADRE 1]`), and possessives (`[CHILD_1]'s`, which leaves the
`'s` as plain text).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Canonical tag types (SPEC 6 plus PERSON and SURNAME, docs/DECISIONS.md 8-9).
PERSON_KINDS = ("CHILD", "PEER", "PARENT", "SIBLING", "FAMILY", "TEACHER", "PROVIDER", "PERSON")
TAG_TYPES = PERSON_KINDS + (
    "SURNAME", "PET", "EMPLOYER", "ORG", "SCHOOL", "DAYCARE", "HOSPITAL", "AGENCY", "PLACE",
    "DATE", "TIME", "PHONE", "EMAIL", "URL", "ID", "MEDICAID_ID", "AGE",
)
UNNUMBERED = frozenset({"EMPLOYER"})

# Spanish (and a few English) labels an AI may write instead of ours. Keys are
# folded (no accents, uppercase, underscores for spaces).
ALIASES = {
    "NINO": "CHILD", "NINA": "CHILD", "NINO_A": "CHILD", "KID": "CHILD", "CLIENT": "CHILD",
    "STUDENT": "CHILD", "ESTUDIANTE": "CHILD", "PACIENTE": "CHILD",
    "COMPANERO": "PEER", "COMPANERA": "PEER", "CLASSMATE": "PEER",
    "MADRE": "PARENT", "PADRE": "PARENT", "MAMA": "PARENT", "PAPA": "PARENT", "MOTHER": "PARENT",
    "FATHER": "PARENT", "GUARDIAN": "PARENT", "TUTOR": "PARENT",
    "HERMANO": "SIBLING", "HERMANA": "SIBLING", "SISTER": "SIBLING", "BROTHER": "SIBLING",
    "FAMILIA": "FAMILY", "FAMILIAR": "FAMILY", "RELATIVE": "FAMILY",
    "MAESTRA": "TEACHER", "MAESTRO": "TEACHER", "PROFESORA": "TEACHER", "PROFESOR": "TEACHER",
    "PROVEEDOR": "PROVIDER", "PROVEEDORA": "PROVIDER", "DOCTOR": "PROVIDER", "MEDICO": "PROVIDER",
    "PERSONA": "PERSON", "NAME": "PERSON", "NOMBRE": "PERSON",
    "APELLIDO": "SURNAME", "LAST_NAME": "SURNAME", "LASTNAME": "SURNAME",
    "MASCOTA": "PET", "EMPLEADOR": "EMPLOYER", "ORGANIZACION": "ORG", "ORGANIZATION": "ORG",
    "ESCUELA": "SCHOOL", "GUARDERIA": "DAYCARE", "CLINICA": "HOSPITAL", "AGENCIA": "AGENCY",
    "LUGAR": "PLACE", "DIRECCION": "PLACE", "ADDRESS": "PLACE", "LOCATION": "PLACE",
    "FECHA": "DATE", "HORA": "TIME", "TELEFONO": "PHONE", "CORREO": "EMAIL", "ENLACE": "URL",
    "IDENTIFICADOR": "ID", "NUMERO": "ID", "MEDICAID": "MEDICAID_ID",
}


def fold_label(label: str) -> str:
    d = unicodedata.normalize("NFD", label)
    d = "".join(c for c in d if not unicodedata.combining(c)).upper()
    return re.sub(r"[\s\-]+", "_", d.strip()).strip("_")


def canonical_type(label: str) -> str | None:
    k = fold_label(label)
    if k in TAG_TYPES:
        return k
    return ALIASES.get(k)


def render_tag(tag_type: str, number: int) -> str:
    return f"[{tag_type}]" if tag_type in UNNUMBERED else f"[{tag_type}_{number}]"


_LABEL_CHARS = r"A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
# [ CHILD_1 ], [child 1], [Child-1], [CHILD1], [NIÑO_1], [EMPLOYER], [MEDICAID_ID_2], (CHILD_1)
TOLERANT = re.compile(
    rf"(?P<open>[\[({{])\s*(?P<label>[{_LABEL_CHARS}]+(?:[ _-][{_LABEL_CHARS}]+)*?)"
    rf"(?:(?P<sep>[ _-]?)(?P<num>\d+))?\s*(?P<close>[\])}}])"
)
# Bare tags with no brackets: CHILD_1 or NIÑO_1 (underscore and number required).
BARE = re.compile(rf"(?<![\w\[({{])(?P<label>[{_LABEL_CHARS}]+(?:_[{_LABEL_CHARS}]+)*)_(?P<num>\d+)(?![\w\])}}])")
ESCAPE = re.compile(r"⟦(\d+)⟧")
# A numbered tag with an unknown label, written like ours: an underscore
# right before the number ("[XYZ_1]", "[xyz_1]", "(FOO_BAR_2)").
UNKNOWN_NUMBERED = re.compile(rf"[\[({{]\s*[{_LABEL_CHARS}][{_LABEL_CHARS}_]*_\d+\s*[\])}}]")
# An all-caps placeholder with no number ("[XYZ]", "[FOO_BAR]"), except SALT codes.
SALT_CODES = frozenset("EW EO EP EU EC FP WO OM ID NM SI PRO IMP X XX XXX".split())
CAPS_PLACEHOLDER = re.compile(r"\[\s*(?P<label>[A-ZÁÉÍÓÚÜÑ]{3,}(?:_[A-ZÁÉÍÓÚÜÑ]+)*)\s*\]")
# A line in the source that looks like our ref line: escaped, so restore never
# mistakes it for session metadata.
REF_LIKE = re.compile(r"(?im)^[ \t]*ref[ \t]*[:：][ \t]*[A-Z0-9]{4}(?:[ \t]*[—–-][^\n]*)?[ \t]*$")
# An unclosed all-caps fragment ("[CHILD ok", "[XYZ"); "[EW:goed]" (SALT) and "[NOTE: x]" are not.
_UP = "A-ZÁÉÍÓÚÜÑ"
UNCLOSED_CAPS = re.compile(
    rf"\[\s*[{_UP}]{{2,}}[{_UP}_]*(?:_\d+)?(?![{_UP}_\d]*[\])}}:])"
    # "(CHILD_1" or "{CHILD": only when nothing closes it on the line ("(PLS-5)" is text)
    rf"|[({{]\s*[{_UP}]{{2,}}[{_UP}_]*(?:_\d+)?(?![^(){{}}\n]*[)}}])")
PLACEHOLDER = re.compile(r"(?i)\[\s*(?:REDACTED|XXX+|\*+|_+|NAME|NOMBRE|BLANK|TBD)\s*\]")


def _label_alternation() -> str:
    labels = sorted({*TAG_TYPES, *ALIASES}, key=len, reverse=True)
    return "|".join(l.lower().replace("_", "[ _-]?") for l in labels)


# Broken known tags, matched on folded (lowercase, no accents) text:
# "[child did well." (no closing bracket on the line) and "child_1]" (no opening).
_KNOWN = _label_alternation()
BROKEN_UNCLOSED = re.compile(rf"[\[({{]\s*(?:{_KNOWN})(?:[ _-]?\d+|_)\b(?![^\[\](){{}}\n]*[\])}}])"
                             rf"|\[\s*(?:{_KNOWN})\b(?![^\[\]\n]*\])")
BROKEN_UNOPENED = re.compile(rf"(?<![\[\w(])(?:{_KNOWN})[ _-]?\d+\s*[\])]")
# A damaged bare tag: "CHILD_1A", "child_12x", or a trailing underscore "CHILD_ did".
BROKEN_BARE = re.compile(rf"(?<![\w\[(])(?:{_KNOWN})_(?:\d+[a-z0-9_]+|(?=\s|$))")
# A known label with an underscore or number but extra text inside closed
# brackets ("[CHILD_1A]", "[child_1 extra]"); "[child's drawing]" is text.
BROKEN_CLOSED = re.compile(rf"[\[({{]\s*(?:{_KNOWN})(?:_|[ -]?\d)[^\[\](){{}}\n]*[\])}}]")


def _known_tag(label: str, num: str | None, sep: str) -> tuple[bool, str | None]:
    """Is this bracketed label one of our tags? Returns (is_tag, canonical tag or None)."""
    t = canonical_type(label)
    if t is None:
        return False, None
    canonical_label = fold_label(label) in TAG_TYPES
    if num is None:
        # Only all-caps forms without a number count ("[CHILD]", "[EMPLOYER]"):
        # "(mother)" or "[school]" is ordinary text.
        if t == "EMPLOYER":
            return True, "EMPLOYER"  # our only unnumbered tag: any case
        if not label.isupper():
            return False, None
        return True, None
    if not canonical_label and not (label.isupper() or sep == "_"):
        return False, None  # "(número 3)" is text; "[NÚMERO_3]" or "[NIÑO_1]" is a tag
    return True, render_tag(t, int(num))[1:-1]


@dataclass(frozen=True)
class TagLike:
    start: int
    end: int
    kind: str  # "tag" | "escape" | "residue"
    tag: str | None = None  # canonical tag for kind == "tag" (None: unusable tag)
    escape: int = 0  # escape number for kind == "escape"


def scan(text: str) -> list[TagLike]:
    """Find everything in `text` that a reader could take for a tag, an escape,
    or tag residue. Non-overlapping, sorted. Render escapes all of these in the
    source; restore resolves tags and escapes and blocks on the rest."""
    from .textnorm import fold

    found: list[TagLike] = []
    taken = bytearray(len(text) + 1)

    def add(s: int, e: int, item: TagLike) -> None:
        if any(taken[s:e]):
            return
        taken[s:e] = b"\x01" * (e - s)
        found.append(item)

    for m in ESCAPE.finditer(text):
        add(m.start(), m.end(), TagLike(m.start(), m.end(), "escape", escape=int(m.group(1))))
    for m in TOLERANT.finditer(text):
        ok, tag = _known_tag(m.group("label"), m.group("num"), m.group("sep") or "")
        if ok:
            add(m.start(), m.end(), TagLike(m.start(), m.end(), "tag", tag))
        elif m.group("num") and canonical_type(m.group("label")):
            # A known label with a number in a form we do not accept ("[niño 1]"):
            # tag-like, so it must never pass silently.
            add(m.start(), m.end(), TagLike(m.start(), m.end(), "residue"))
    for m in UNKNOWN_NUMBERED.finditer(text):
        add(m.start(), m.end(), TagLike(m.start(), m.end(), "residue"))
    for m in BARE.finditer(text):
        t = canonical_type(m.group("label"))
        if t is not None:
            add(m.start(), m.end(), TagLike(m.start(), m.end(), "tag", render_tag(t, int(m.group("num")))[1:-1]))
    for m in PLACEHOLDER.finditer(text):
        add(m.start(), m.end(), TagLike(m.start(), m.end(), "residue"))
    for m in CAPS_PLACEHOLDER.finditer(text):
        if m.group("label") not in SALT_CODES:
            add(m.start(), m.end(), TagLike(m.start(), m.end(), "residue"))
    for m in REF_LIKE.finditer(text):
        s0 = m.start() + (len(m.group()) - len(m.group().lstrip(" \t")))
        add(s0, m.end(), TagLike(s0, m.end(), "residue"))
    folded = fold(text)
    for pat in (BROKEN_CLOSED, BROKEN_UNCLOSED, BROKEN_UNOPENED, BROKEN_BARE):
        for m in pat.finditer(folded.text):
            s, e = folded.to_source(m.start(), m.end())
            add(s, e, TagLike(s, e, "residue"))
    for m in UNCLOSED_CAPS.finditer(text):
        add(m.start(), m.end(), TagLike(m.start(), m.end(), "residue"))
    for m in re.finditer("[⟦⟧]", text):
        add(m.start(), m.end(), TagLike(m.start(), m.end(), "residue"))
    return sorted(found, key=lambda t: t.start)
