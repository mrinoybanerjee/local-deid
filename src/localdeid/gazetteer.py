"""Gazetteer stage: bilingual first-name and surname lists with context (SPEC 4.3).

Lists: SSA first names and Census 2010 surnames (CC0; tools/build_gazetteer.py).
Matching is case- and accent-insensitive. A hit becomes:
- a span (sure) when context marks it as a name: next to a kin word or an
  honorific, or next to another listed name ("Lucía Ramírez");
- a check item (unsure, hidden by default) when it is a bare capitalized word
  that is not at the start of a sentence or line;
- nothing when it is lowercase, a bare sentence-initial word, or a
  capitalized non-name word from the stoplist (months, languages, headings).
Typed notes capitalize names; lowercase names belong to transcripts (v1.1).
Names that are also words (Hope, Rosa, Sol) are always unsure.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

from .roster import word_names
from .spans import Candidate
from .textnorm import fold

# Capitalized words common in SLP notes that are listed as names or surnames
# but are almost never people here. Folded forms.
STOPLIST = frozenset("""
the and a an of in on at to for with by from or but not no yes is was are be this that it he she
they we you his her their our my your its as if so than then there here when where what who how
mom dad mommy daddy mother father parent parents child children baby sister brother family
teacher therapist clinician speech language therapy session sessions goal goals note notes plan
english spanish bilingual french chinese mandarin arabic portuguese hispanic latino latina
american mexican guatemalan honduran salvadoran african asian white black
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday
enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre
lunes martes miercoles jueves viernes sabado domingo
christmas easter thanksgiving halloween navidad pascua
mr mrs ms miss dr sr sra srta don dona
early intervention program county state school daycare center hospital clinic
assessment evaluation report summary background history results recommendations
standard score scores raw percentile age test total subtest receptive expressive
articulation phonology fluency voice feeding play social pragmatics vocabulary
subjective objective analysis service services provider outcome outcomes
good great well very also during after before while because however overall
el la los las un una y o de del en con por para su sus es son fue muy
nombre fecha padre madre familia escuela maestra maestro terapia lenguaje habla
apoyo juego casa palabras sesion objetivo resultados
case record page price major general court early oral core below above sample manager worker number
""".split())

_TOKEN = re.compile(r"[^\W\d_](?:[^\W\d_]|['’-](?=[^\W\d_]))*")
_KIN_BEFORE = re.compile(
    r"(?i:\b(?:mom|mother|dad|father|sister|brother|aunt|uncle|grandma|grandmother|grandpa|"
    r"grandfather|cousin|friend|classmate|teacher|son|daughter|baby|"
    r"mamá|mama|mami|madre|papá|papa|papi|padre|hermana|hermano|hermanita|hermanito|tía|tia|"
    r"tío|tio|abuela|abuelita|abuelo|prima|primo|amigo|amiga|maestra|maestro|hijo|hija|"
    r"compañero|compañera|niño|niña|bebé|"
    r"mr|mrs|ms|miss|dr|dra|sr|sra|srta|doña|don|señora|señor)\.?,?\s+)$"
)


@lru_cache(maxsize=1)
def _counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for line in resources.files("localdeid.data").joinpath("first_names.tsv").read_text("utf-8").splitlines():
        if not line.startswith("#"):
            name, count, _ = line.split("\t")
            k = fold(name).text
            out[k] = out.get(k, 0) + int(count)
    return out


BARE_MIN_BIRTHS = 1000  # a bare capitalized word is a check item only for a common first name


@lru_cache(maxsize=1)
def _lists() -> tuple[dict[str, float], frozenset[str]]:
    first: dict[str, float] = {}
    pkg = resources.files("localdeid.data")
    for line in pkg.joinpath("first_names.tsv").read_text("utf-8").splitlines():
        if line.startswith("#"):
            continue
        name, _count, female = line.split("\t")
        key = fold(name).text
        first.setdefault(key, float(female))
    surnames = set()
    for line in pkg.joinpath("surnames.tsv").read_text("utf-8").splitlines():
        if line.startswith("#"):
            continue
        surnames.add(fold(line.split("\t", 1)[0]).text)
    return first, frozenset(surnames)


# Large cities families often name as a hometown (below state level: places).
MAJOR_CITIES = frozenset("""
Mexico City|Ciudad de México|Ciudad de Mexico|Guadalajara|Monterrey|Tijuana|Ciudad Juárez|León|Acapulco|Toluca|
Morelia|Cuernavaca|Veracruz|Puebla de Zaragoza|Guatemala City|Ciudad de Guatemala|Quetzaltenango|Xela|
Huehuetenango|Escuintla|San Salvador|Santa Ana|San Miguel|Tegucigalpa|San Pedro Sula|La Ceiba|Choluteca|
Juticalpa|Siguatepeque|Comayagua|Managua|León Viejo|Granada|San José|Panama City|Ciudad de Panamá|Bogotá|
Medellín|Cali|Barranquilla|Quito|Guayaquil|Cuenca|Riobamba|Lima|Arequipa|Caracas|Maracaibo|Santo Domingo|
Santiago de los Caballeros|San Juan|Ponce|Havana|La Habana|Miami|Houston|Los Angeles|New York City|Atlanta
""".replace("\n", "").split("|"))


@lru_cache(maxsize=1)
def _places() -> frozenset[str]:
    text = resources.files("localdeid.data").joinpath("nc_places.txt").read_text("utf-8")
    nc = {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}
    return frozenset(nc | {c.strip() for c in MAJOR_CITIES if c.strip()})


# the engine's own street vocabulary (rules.ADDRESS_PATTERNS), English and Spanish
from .rules import STREET_TYPES, STREET_TYPES_ES  # noqa: E402

_STREETS = f"{STREET_TYPES}|{STREET_TYPES_ES}"
# after a location word, or after a numbered street (with a street word) and a comma
# ("204 Avenida Roxboro, Durham"; not "10 minutes, Bath time")
_PLACE_CONTEXT = re.compile(r"(?i:\b(?:in|at|near|from|to|of|en|de|cerca de|desde|hasta|a)\s+)$|"
                            rf"\b\d{{1,6}}[ \t]+(?=[^\n,]*[A-ZÁÉÍÓÚÑ])"  # a named street, not "10 minutes in place"
                            rf"(?:[^\n,]{{0,60}}?\b(?i:{_STREETS})\b[^\n,]{{0,30}}),[ \t]*$")
_PLACE_AFTER = re.compile(r"^(?:,\s*(?:NC|N\.C\.|North Carolina|Carolina del Norte)\b|\s+(?:County|area|área))")


def find_places(text: str) -> list[Candidate]:
    """North Carolina city and town names (Census places). Sure after a location
    word ("in Apex") or before ", NC"; otherwise a check item, unless the name is
    also a common word or a listed name, then nothing."""
    places = _places()
    if not places:
        return []
    out: list[Candidate] = []
    pat = _place_pattern()
    words = word_names()
    first, surnames = _lists()
    for m in pat.finditer(text):
        name = m.group()
        before, after = text[max(0, m.start() - 60):m.start()], text[m.end():m.end() + 25]
        context = bool(_PLACE_CONTEXT.search(before) or _PLACE_AFTER.match(after))
        k = fold(name).text
        ambiguous = k in STOPLIST or k in words or k in first or k in surnames
        if context or " " in name.strip():
            out.append(Candidate(m.start(), m.end(), "PLACE", "gazetteer", True, 0, "gazetteer:place"))
        elif not ambiguous and not _sentence_initial(text, m.start()):
            out.append(Candidate(m.start(), m.end(), "PLACE", "gazetteer", False, 0, "gazetteer:place-bare"))
    return out


@lru_cache(maxsize=1)
def _place_pattern() -> "re.Pattern[str]":
    names = sorted(_places(), key=len, reverse=True)
    alt = "|".join(re.escape(n).replace(r"\ ", r"[ \t]+") for n in names)
    return re.compile(rf"(?<![\w-])(?:{alt})(?![\w-])")


def is_place(token: str) -> bool:
    return token.strip() in _places()


def female_share(given: str) -> float | None:
    """SSA female share of a given name, or None if unknown (for the context line)."""
    return _lists()[0].get(fold(given).text)


def is_first_name(token: str) -> bool:
    return fold(token).text in _lists()[0]


def is_surname(token: str) -> bool:
    return fold(token).text in _lists()[1]


def is_listed(token: str) -> bool:
    first, surnames = _lists()
    k = fold(token).text
    return k in first or k in surnames


def _name_count(k: str) -> int:
    """Births for a first name, or for the name a diminutive comes from ('danielito' -> 'daniel')."""
    counts = _counts()
    if k in counts:
        return counts[k]
    for suf, repl in _DIM_RULES:
        if k.endswith(suf) and len(k) > len(suf) + 2 and (k[: -len(suf)] + repl) in counts:
            return counts[k[: -len(suf)] + repl]
    return 0


_DIM_RULES = (("cito", ""), ("cita", ""), ("ito", "o"), ("ita", "a"), ("ito", ""), ("ita", ""),
              ("guito", "go"), ("quito", "co"), ("illo", "o"), ("illa", "a"))


def _diminutive_of_listed(k: str, first: dict[str, float]) -> bool:
    """'danielito' -> 'daniel', 'dieguito' -> 'diego', 'anita' -> 'ana'."""
    for suf, repl in _DIM_RULES:
        if k.endswith(suf) and len(k) > len(suf) + 2 and (k[: -len(suf)] + repl) in first:
            return True
    return False


def _sentence_initial(text: str, s: int) -> bool:
    i = s - 1
    while i >= 0 and text[i] in " \t\u00a0\"“'‘(¿¡":
        i -= 1
    return i < 0 or text[i] in ".!?\n:;•*-–—|\t"


_GAP = re.compile(r"[ \t\u00a0]+(?:(?:de|del|de la|de los|de las|y|van|von|da)[ \t\u00a0]+)?")


def _neighbour(tokens: list, listed: list, i: int, step: int) -> int:
    """Index of the next token, skipping particle tokens between name parts."""
    j = i + step
    while 0 <= j < len(tokens) and tokens[j][2] in ("de", "del", "la", "los", "las", "y", "van", "von", "da"):
        j += step
    return j


def find(text: str) -> list[Candidate]:
    first, surnames = _lists()
    words = word_names()
    from .roster import _state_spans

    states = _state_spans(text)
    tokens = [(m.start(), m.end(), fold(m.group()).text) for m in _TOKEN.finditer(text)]
    listed = [k not in STOPLIST and (k in first or k in surnames or _diminutive_of_listed(k, first))
              and len(k) >= 2 for _, _, k in tokens]
    out: list[Candidate] = []
    for i, (s, e, k) in enumerate(tokens):
        if not listed[i] or any(a <= s and e <= b for a, b in states):
            continue
        if not text[s].isupper():
            continue
        # neighbour is another listed, capitalized token separated only by a space
        def adjacent(j: int) -> bool:
            if not 0 <= j < len(tokens) or not listed[j] or not text[tokens[j][0]].isupper():
                return False
            a, b = (tokens[j][1], s) if j < i else (e, tokens[j][0])
            gap = text[a:b]
            # A space, or surname particles: "Mateo de la Cruz", "Juan del Río"
            return 0 < b - a <= 12 and _GAP.fullmatch(gap) is not None
        kin = bool(_KIN_BEFORE.search(text[max(0, s - 24):s]))
        pair = adjacent(_neighbour(tokens, listed, i, -1)) or adjacent(_neighbour(tokens, listed, i, 1))
        ambiguous = k in words
        if kin:
            out.append(Candidate(s, e, "PERSON", "gazetteer", not ambiguous, 0, "gazetteer:context"))
        elif pair:
            out.append(Candidate(s, e, "PERSON", "gazetteer", not ambiguous, 0, "gazetteer:name-pair"))
            j = _neighbour(tokens, listed, i, 1)
            if j > i + 1 and adjacent(j):  # bridged by particles: one span for the whole name
                out.append(Candidate(s, tokens[j][1], "PERSON", "gazetteer", True, 0, "gazetteer:name-pair"))
        elif (not _sentence_initial(text, s) and _name_count(k) >= BARE_MIN_BIRTHS and text[s:e].istitle()
              and not any(a <= s and e <= b for a, b in states)):
            # A bare title-case first name ("Diego") is a check item; surname-only
            # words ("Case", "Record") and ALL-CAPS headings are not.
            out.append(Candidate(s, e, "PERSON", "gazetteer", False, 0, "gazetteer:bare"))
    return out
