"""Form-field parser (SPEC 4.3 `fields`): "Label: value" lines and table cells.

Evaluations and IFSPs put most identifiers in labelled fields. The value of a
known label becomes a span of the label's type. Three layouts are read:

1. "Label: value" (also "Label - value" and "Label # value"), several per
   line when separated by a tab, " | ", or 3+ spaces;
2. a two-cell table row "Label<TAB>value";
3. a header row of labels followed by rows of values (tab-separated).

SALT headers ("+ Name: ...") count as fields. Labels match without regard to
case or accents. Users can add labels (SPEC 4.3: "she can add a label from
the Check view").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from .spans import Candidate
from .textnorm import fold

# Label -> kind. Keys are folded (lowercase, no accents); spaces normalized.
_LABELS: dict[str, str] = {}


def _add(kind: str, *labels: str) -> None:
    for lab in labels:
        _LABELS[_key(lab)] = kind


def _key(label: str) -> str:
    k = fold(label).text
    k = re.sub(r"[#:().*]", " ", k)
    k = re.sub(r"\s*/\s*", "/", k)
    return re.sub(r"\s+", " ", k).strip()


_add("CHILD", "name", "child", "child name", "child's name", "childs name", "client", "client name",
     "patient", "patient name", "student", "student name", "full name", "legal name", "preferred name",
     "nickname", "nombre", "nombre del niño", "nombre del nino", "nombre de la niña", "nombre del niño/a",
     "nombre del niño(a)", "nombre completo", "niño", "niña", "niño/a", "paciente", "estudiante",
     "alumno", "alumna", "apodo", "first name", "primer nombre", "cliente", "nombre del cliente",
     "nombre del paciente", "client/cliente", "cliente/client", "name/nombre", "nombre/name")
_add("SURNAME", "last name", "surname", "family name", "apellido", "apellidos", "child's last name",
     "apellido paterno", "apellido materno")
_add("DATE", "dob", "d.o.b", "d.o.b.", "date of birth", "birth date", "birthdate", "birthday",
     "fecha de nacimiento", "f. de nacimiento", "fecha nac", "f. nac", "nacimiento", "date of evaluation",
     "evaluation date", "eval date", "date of assessment", "assessment date", "date of service", "dos",
     "service date", "session date", "date of session", "date of report", "report date", "ifsp date",
     "ifsp meeting date", "date of ifsp", "initial ifsp date", "annual review date", "review date",
     "start date", "end date", "referral date", "date of referral", "test date", "date tested",
     "date of visit", "visit date", "fecha de evaluación", "fecha de evaluacion", "fecha de servicio",
     "fecha de la sesión", "fecha de la sesion", "fecha del informe", "fecha de referido",
     "fecha de revisión", "fecha de revision", "fecha", "date", "next visit", "próxima visita",
     "proxima visita", "date signed", "signature date")
_add("PARENT", "parent", "parents", "guardian", "parent/guardian", "parent/caregiver", "legal guardian",
     "mother", "father", "mom", "dad", "caregiver", "primary caregiver", "foster parent", "padre", "madre",
     "padres", "tutor", "tutora", "tutor legal", "padre/madre", "padre/tutor", "madre/padre",
     "encargado", "encargada", "cuidador", "cuidadora", "responsable", "contact", "emergency contact",
     "contacto", "contacto de emergencia", "family", "familia", "parent name", "nombre del padre",
     "nombre de la madre")
_add("PLACE", "address", "home address", "street address", "mailing address", "residence", "city",
     "county", "zip", "zip code", "location", "session location", "service location", "place of service",
     "dirección", "direccion", "domicilio", "ciudad", "condado", "código postal", "codigo postal",
     "lugar", "lugar de servicio", "location of service")
_add("PHONE", "phone", "phone number", "telephone", "tel", "cell", "cell phone", "mobile", "home phone",
     "work phone", "fax", "teléfono", "telefono", "celular", "tel.", "número de teléfono",
     "numero de telefono", "whatsapp")
_add("EMAIL", "email", "e-mail", "email address", "correo", "correo electrónico", "correo electronico")
_add("MEDICAID_ID", "medicaid", "medicaid id", "medicaid number", "medicaid no", "ma", "ma id",
     "número de medicaid", "numero de medicaid")
_add("ID", "insurance", "insurance id", "insurance number", "member id", "member number", "policy",
     "policy number", "subscriber id", "group number", "mrn", "medical record", "medical record number",
     "record", "record number", "chart", "chart number", "account", "account number", "claim",
     "claim number", "authorization", "authorization number", "auth", "prior auth", "case number",
     "child id", "nc itp child id", "itp child id", "itp id", "ifsp number", "ifsp", "student id",
     "npi", "ssn", "seguro", "número de seguro", "numero de seguro", "póliza", "poliza",
     "número de expediente", "numero de expediente", "expediente", "número de caso", "numero de caso")
_add("PROVIDER", "physician", "pcp", "primary care physician", "primary care provider", "pediatrician",
     "doctor", "referring physician", "referring provider", "referral source", "referred by",
     "service coordinator", "case manager", "care coordinator", "coordinator", "examiner", "evaluator",
     "clinician", "therapist", "provider", "treating therapist", "interpreter", "translator",
     "médico", "medico", "pediatra", "doctor/a", "referido por", "fuente de referido",
     "coordinador de servicios", "coordinadora de servicios", "coordinador(a) de servicios",
     "coordinador/a de servicios", "evaluador", "evaluadora", "terapeuta", "intérprete", "interprete",
     "audiologist", "neurologist", "ot", "pt", "occupational therapist", "physical therapist",
     "supervisor")
_add("SCHOOL", "school", "school name", "elementary", "escuela", "colegio", "nombre de la escuela",
     "campus", "district", "school district", "distrito", "distrito escolar")
_add("DAYCARE", "daycare", "day care", "child care", "childcare", "preschool", "center",
     "child care center", "guardería", "guarderia", "centro de cuidado", "preescolar")
_add("TEACHER", "teacher", "classroom teacher", "lead teacher", "maestra", "maestro", "maestro/a",
     "profesora", "profesor", "classroom")
_add("AGENCY", "cdsa", "agency", "agencia", "cdsa office", "oficina cdsa")
_add("HOSPITAL", "hospital", "birth hospital", "place of birth", "hospital de nacimiento",
     "lugar de nacimiento", "nicu", "clinic", "clínica", "clinica")
_add("ORG", "employer", "parent employer", "workplace", "occupation/employer", "empleador", "trabajo",
     "lugar de trabajo", "parent workplace", "mother's workplace", "father's workplace", "mother's employer",
     "father's employer", "caregiver workplace", "caregiver employer", "trabajo de la madre", "trabajo del padre",
     "lugar de trabajo de la madre", "lugar de trabajo del padre")
_add("PET", "pet", "pets", "mascota", "mascotas")
_add("SIBLING", "sibling", "siblings", "brother", "sister", "hermano", "hermana", "hermanos")

# Values that are not identifiers.
_EMPTY = re.compile(r"^(?:n/?a|none|unknown|tbd|pending|—|-+|_+|\?+|no|sí|si|yes|ninguno|ninguna|"
                    r"desconocido|desconocida|no aplica|same|igual|see above|ver arriba)\.?$", re.I)
_SEP_INLINE = re.compile(r"\t+|\s\|\s|\s{3,}")
_LINE_LABEL = re.compile(
    r"^(?P<lead>[ \t]*(?:[-*•+]\s*)?)(?P<label>[^\W\d_][\w'’ ./()#-]{0,40}?)\s*(?:[:：]|\s[-–—]\s|#\s*:?)\s*(?P<value>.*)$"
)


# Labels that are not identifiers but end a value when they follow it inline
# ("DOB: 01/02/2022  Age: 4;2").
_OTHER_LABELS = {_key(x) for x in (
    "age", "chronological age", "ca", "grade", "sex", "gender", "language", "primary language",
    "home language", "diagnosis", "dx", "icd-10", "icd", "cpt", "duration", "time in", "time out",
    "minutes", "units", "session type", "service", "frequency", "edad", "grado", "sexo", "género",
    "genero", "idioma", "diagnóstico", "diagnostico", "duración", "duracion", "frecuencia")}
_NEXT_LABEL = re.compile(r"(?:(?P<wide>[ \t]{2,})|[ \t]+)(?P<label>[^\W\d_][\w'’ ./()#-]{0,40}?)\s*[:：]")


def _cut_at_next_label(value: str, extra: Mapping[str, str]) -> int:
    """Length of `value` before the next inline label. After 2+ spaces any
    "Label:" ends the value; after one space only a recognized label does."""
    for m in _NEXT_LABEL.finditer(value):
        if m.start() == 0:
            continue
        label = m.group("label").strip()
        known = _lookup(label, extra) is not None or _key(label) in _OTHER_LABELS
        if m.group("wide") or known:
            return m.start()
    return len(value)


@dataclass(frozen=True)
class FieldHit:
    start: int
    end: int
    kind: str
    label: str


def _lookup(label: str, extra: Mapping[str, str]) -> str | None:
    k = _key(label)
    if k in extra:
        return extra[k]
    if k in _LABELS:
        return _LABELS[k]
    # "Child's Name (first, last)" -> try without trailing qualifiers
    k2 = re.sub(r"\s*\(.*\)$", "", k).strip()
    return extra.get(k2) or _LABELS.get(k2)


_PERSONISH = {"CHILD", "PARENT", "PROVIDER", "TEACHER", "SIBLING", "PET", "SURNAME", "SCHOOL",
              "DAYCARE", "AGENCY", "HOSPITAL", "ORG"}
_ORGS = {"SCHOOL", "DAYCARE", "AGENCY", "HOSPITAL", "ORG"}
_NAME_JOINERS = {"de", "del", "la", "las", "los", "y", "and", "&", "da", "di", "van", "von", "der", "le"}
# A verb or clause word means the "value" is a sentence ("Reports that Mateo ...").
_SENTENCE = re.compile(r"(?i)\b(?:that|reports?|reported|states?|stated|says?|said|is|are|was|were|has|have|"
                       r"will|would|can|could|should|did|does|do|not|no|que|dijo|dice|reporta|reportó|es|está|"
                       r"son|fue|tiene|tienen|no)\b|[.!?]\s+\S")
_MONTH = re.compile(r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|abr|ago|dic)")
_BLANK = " \t\u00a0"


def _name_like(value: str) -> bool:
    """1-6 name tokens: capitalized words, initials, or surname particles. All-
    lowercase values pass only when every token is a listed name."""
    from . import gazetteer

    toks = re.findall(r"[^\W\d_][\w'’.-]*|&", value)
    # A one- or two-word value is a name even if a word is also a verb ("Will").
    # Initials ("Hannah R. Soto") are not sentence breaks.
    if len(toks) > 2 and _SENTENCE.search(re.sub(r"\b[A-Z]\.", "X", value)):
        return False
    core = [t for t in toks if t.lower() not in _NAME_JOINERS]
    if not core or len(core) > 6 or re.search(r"\d", value):
        return False
    if all(t[0].isupper() for t in core):
        return True
    return value == value.lower() and all(gazetteer.is_listed(t) for t in core)


def _name_prefix(value: str) -> int:
    """Length of the name at the start of a longer value: 'Ruiz, Ana' (last, first)
    stays whole; otherwise the part before the first comma, if it is a name."""
    segs = value.split(",")
    if len(segs) >= 2:
        a, b = segs[0].strip(), segs[1].strip()
        if len(a.split()) == 1 and len(b.split()) == 1 and _name_like(a) and _name_like(b) and not _credential_only(b):
            return len(segs[0]) + 1 + len(segs[1])
        if _name_like(a):
            return len(segs[0])
    return 0


def _plausible(kind: str, value: str) -> bool:
    """Reject prose after a label ("Mother: reports that ..."): it is not a field value."""
    words = value.split()
    digits = sum(ch.isdigit() for ch in value)
    if kind in _PERSONISH - _ORGS:
        return _name_like(value)  # see _name_prefix for "Name, M.S., description" values
    if kind in _ORGS:
        return len(words) <= 8 and value[0].isupper() and not _SENTENCE.search(value)
    if kind == "DATE":
        return digits > 0 or bool(_MONTH.search(value))
    if kind == "PHONE":
        return digits >= 7
    if kind in ("ID", "MEDICAID_ID"):
        # "IFSP #1 (continuado, modificado):" numbers an outcome; an ID has 2+ digits and no prose.
        # Brackets around a whole value are punctuation: "IFSP #: (2024-0042)".
        core = value.strip().strip("()[]{}").strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]+", core):
            return digits > 0  # one compact token: "G7", "12345"
        return digits >= 2 and len(core.split()) <= 4 and not re.search(r"[():]", core)
    if kind == "EMAIL":
        return "@" in value
    if kind == "PLACE":
        if re.search(r"\[[ xX✓✔]?\]", value):
            return False  # "[X] Home  [ ] Daycare": a checkbox choice, not an address
        # An address or a place name: a capitalized word or a number ("home" is not a place).
        alpha = [w for w in words if any(ch.isalpha() for ch in w)]
        has_cap = any(w[0].isupper() for w in alpha)
        return len(words) <= 12 and (has_cap or digits > 0) and not _SENTENCE.search(value)
    return True


# ", M.S., CCC-SLP" / ", MD" / ", Ph.D." after a person's name: credentials, not identity.
_CREDENTIALS = re.compile(r",\s*(?:(?:[A-Z][A-Za-z]{0,3}\.?){1,4}(?:[-/][A-Za-z]{1,4})*\s*,?\s*)+$")


_KNOWN_CREDENTIALS = frozenset("""
ms ma med mat mat bs ba phd edd psyd md do rn np pa dpt pt ot otr otr/l cota slp slpa ccc ccc-slp cf cf-slp
bcba bcaba lcsw lpc lmft rbt cdp aud au.d cfy ibclc cleft
""".split())


def _tail_is_name(tail: str) -> bool:
    """', lucia' (last, first in lowercase) is a name, not a description."""
    from . import gazetteer

    words = re.findall(r"[^\W\d_]+", tail)
    return 1 <= len(words) <= 2 and all(gazetteer.is_first_name(w) for w in words)


def _strip_credentials(value: str) -> int:
    """Length of `value` without trailing comma-separated known credentials
    ("Ana Ruiz, M.S., CCC-SLP" -> "Ana Ruiz"; "Ruiz, Ana" is unchanged)."""
    segs = value.split(",")
    keep = len(segs)
    while keep > 1 and _credential_only(segs[keep - 1]):
        keep -= 1
    return len(",".join(segs[:keep])) if keep < len(segs) else len(value)


def _credential_only(value: str) -> bool:
    toks = [t.strip(".").lower().replace(".", "") for t in re.split(r"[,\s]+", value) if t.strip(" ,.")]
    return bool(toks) and all(t in _KNOWN_CREDENTIALS for t in toks)


_RELATION_TAIL = re.compile(r"\s*(?:,\s*|\(\s*)[a-záéíóúñ][^,()]*\)?\s*$")
_HONORIFIC_HEAD = re.compile(r"(?:Dr|Dra|Mr|Mrs|Ms|Mx|Miss|Sr|Sra|Srta|Prof|Profa)\.?\s+")


def _value_span(text: str, s: int, e: int, kind: str) -> tuple[int, int] | None:
    while s < e and text[s] in _BLANK:
        s += 1
    semi = text.find(";", s, e)  # a value ends at a semicolon: prose may follow
    if semi != -1:
        e = semi
    if kind in _PERSONISH and kind not in ("SCHOOL", "DAYCARE", "AGENCY", "HOSPITAL", "ORG"):
        for _ in range(3):  # "Name, M.S., CCC-SLP, bilingual provider": strip both kinds of tail
            m = _RELATION_TAIL.search(text, s, e)  # "Lucía Rodríguez, mother" / "(mamá)"
            if m and m.start() > s and not _tail_is_name(m.group()):
                e = m.start()
            e = s + _strip_credentials(text[s:e].rstrip(" ,."))
        m = _HONORIFIC_HEAD.match(text, s, e)  # keep "Dr." / "Sra." visible
        if m and m.end() < e:
            s = m.end()
    while e > s and text[e - 1] in _BLANK + ".;,":
        e -= 1
    while s < e and text[s] in ",;:-–— \t":
        s += 1
    if kind in _PERSONISH - _ORGS and e > s:
        # "Carla Jiménez (Spanish, on-site)": a parenthetical of common words describes the person
        par = re.search(r"\s*\(([^()]*)\)?\s*$", text[s:e])  # trailing only: "Ana (Early) Salazar" keeps all
        if par and par.start() > 0:
            from .generic import NON_NAME, all_generic

            inner = par.group(1)
            if all_generic(re.sub(r"[-/]", " ", inner), NON_NAME | {"on", "site", "certified", "medical",
                                                                    "interpreter", "intérprete", "phone",
                                                                    "telephone", "video", "remote"}):
                e = s + par.start()
    if kind in _PERSONISH - _ORGS and e > s:
        segs = text[s:e].split(",")
        if len(segs) >= 2 and any(_credential_only(sg.strip()) for sg in segs[1:] if sg.strip()):
            # "Mara Jennings, M.S., CCC-SLP, Bilingual": the name ends before the credentials
            e = s + (_name_prefix(text[s:e]) or len(segs[0]))
    if kind in _PERSONISH - _ORGS and e > s and not _name_like(text[s:e]):
        e = s + _name_prefix(text[s:e])  # "Ashley Monteiro-Byrd, M.S., CCC-SLP, bilingual provider"
    if e <= s or _EMPTY.match(text[s:e]) or not _plausible(kind, text[s:e]):
        return None
    if _credential_only(text[s:e]):
        return None  # "OTR/L" or "M.S., CCC-SLP" alone
    return s, e


def parse(text: str, extra_labels: Mapping[str, str] | None = None) -> list[FieldHit]:
    extra = {_key(k): v for k, v in (extra_labels or {}).items()}
    hits: list[FieldHit] = []
    pos = 0
    lines = text.split("\n")
    offsets = []
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    header: list[tuple[int, str | None]] | None = None  # (column, kind) for a header row
    for li, line in enumerate(lines):
        base = offsets[li]
        body = line[:-1] if line.endswith("\r") else line
        cells = body.split("\t")
        if not body.strip():
            header = None
            continue
        # A row whose cells are labels (a new header, or "Label<TAB>value") ends
        # the previous header's table.
        if header is not None and any(
            _lookup(c.strip().rstrip(":#"), extra) for c in cells if c.strip()
        ):
            header = None
        # Layout 3: values under a header row of labels.
        if header is not None and len(cells) == len(header):
            col_start = base
            for ci, cell in enumerate(cells):
                kind = header[ci][1]
                if kind:
                    vs = _value_span(text, col_start, col_start + len(cell), kind)
                    if vs:
                        hits.append(FieldHit(vs[0], vs[1], kind, f"column {ci + 1}"))
                col_start += len(cell) + 1
            continue
        header = None
        if len(cells) >= 2:
            kinds = [_lookup(c.strip().rstrip(":#"), extra) if c.strip() else None for c in cells]
            if sum(k is not None for k in kinds) >= 2 and not any(":" in c for c in cells):
                header = [(i, k) for i, k in enumerate(kinds)]
                continue
            # Layout 2: "Label<TAB>value" pairs across the row.
            col_start = base
            pairs = False
            for ci in range(0, len(cells) - 1, 1):
                label = cells[ci].strip().rstrip(":#").strip()
                kind = _lookup(label, extra) if label else None
                if kind and ":" not in cells[ci + 1]:
                    vstart = col_start + len(cells[ci]) + 1
                    vs = _value_span(text, vstart, vstart + len(cells[ci + 1]), kind)
                    if vs:
                        hits.append(FieldHit(vs[0], vs[1], kind, label))
                        pairs = True
                col_start += len(cells[ci]) + 1
            if pairs:
                continue
        # Layout 1: "Label: value", possibly several per line.
        seg_start = 0
        for m in list(_SEP_INLINE.finditer(body)) + [None]:
            seg_end = m.start() if m else len(body)
            seg = body[seg_start:seg_end]
            lm = _LINE_LABEL.match(seg)
            if lm:
                label = lm.group("label").strip()
                kind = _lookup(label, extra)
                if kind:
                    v0 = base + seg_start + lm.start("value")
                    v1 = v0 + _cut_at_next_label(lm.group("value"), extra)
                    vs = _value_span(text, v0, v1, kind)
                    if vs:
                        hits.append(FieldHit(vs[0], vs[1], kind, label))
            if m:
                seg_start = m.end()
    return hits


def find(text: str, extra_labels: Mapping[str, str] | None = None) -> list[Candidate]:
    return [Candidate(h.start, h.end, h.kind, "fields", True, 2, f"field:{h.label}")
            for h in parse(text, extra_labels)]


def known_labels() -> Iterable[str]:
    return _LABELS.keys()
