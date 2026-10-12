"""Words that are never an identifier on their own (SPEC 6: relationship words stay).

Detectors, Privacy Filter above all, sometimes label a kin word ("Mamá"), an
article, or a title as part of a name. These words are trimmed from the edges
of name and organization spans, a span made only of them is dropped, and they
are never propagated. Matching is on folded text (no case, no accents).
"""

from __future__ import annotations

import re

from .textnorm import fold  # noqa: F401  (used for LOWERCASE_ONLY variants)

# Lists keep their accents and match case-folded tokens without accent folding:
# "niña" is generic, but "Nina" is a name.
KIN = frozenset("""
mom mommy mother mama mamá mami madre dad daddy father papa papá papi padre parent parents guardian guardians
stepmom stepmother stepdad stepfather madrastra padrastro tutor tutora caregiver caregivers cuidador cuidadora
sister brother hermana hermano hermanita hermanito hermanas hermanos siblings sibling twin gemela gemelo
grandma grandmother grandpa grandfather granny abuela abuelita abuelo abuelito abuelos grandparents
aunt auntie uncle tía tío tías tíos cousin prima primo primos niece nephew sobrina sobrino
godmother godfather madrina padrino comadre compadre nanny babysitter niñera
baby bebé son daughter hijo hija hijos husband wife esposo esposa family familia
""".split())

REFERENTS = frozenset("""
child children kid kids client patient student boy girl toddler infant
niño niña niños niñas cliente paciente estudiante alumno alumna
i me my we you he she him her his they them their yo mi tú él ella ellos ellas usted nosotros
teacher maestra maestro therapist terapeuta clinician examiner evaluator provider doctor
interpreter intérprete interprete coordinator coordinadora coordinador pediatrician pediatra
""".split())

# Words of SLP section headings, never a name ("EVALUACIÓN DEL HABLA Y DEL LENGUAJE").
HEADINGS = frozenset("""
speech language therapy evaluation assessment report plan summary background history results
recommendations goals goal objectives services session note notes progress daily initial annual review
receptive expressive articulation phonology fluency voice pragmatics feeding hearing development
developmental family concerns priorities routines outcomes outcome strategies impressions diagnosis
habla lenguaje terapia evaluación evaluacion informe reporte plan resumen antecedentes historia
resultados resultado recomendaciones metas objetivos servicios sesión sesion nota notas progreso
receptivo expresivo articulación articulacion fonología fonologia fluidez voz desarrollo familia
preocupaciones prioridades rutinas resultados estrategias impresiones diagnóstico diagnostico
""".split())

TITLES = frozenset("""
mr mrs ms mx miss mister dr dra doctora sr sra srta señor señora señorita doña lic ing prof profa
""".split())

FUNCTION = frozenset("""
the a an at in on of to for from by with and or but as la el las los un una unos unas de del en con por para
y o this that these those este esta ese esa sus
""".split())

# Words that are also given names: generic only when written in lowercase
# ("ella" is "she"; "Ella" is a child).
LOWERCASE_ONLY = frozenset("ella nana don al su nina tia tio bebe dona mama papa".split())

# Common words that are never a person or place name on their own, found hidden on dev2
# (often spread by propagation from one wrong entity): languages, chart shorthand,
# form labels, and Spanish verbs and nouns of clinical prose (DECISIONS 25).
# NEVER: not a name even in a form field or a longer span ("Language: Spanish",
# "primary daytime CG", "Location: Home", "Caregiver Coaching Session").
NEVER_NAME = frozenset("""
spanish español espanol inglés ingles bilingual bilingüe bilingue
cg cgs pt pts caregiver care familiar coaching home casa hogar
date fecha baseline approx aprox tb mgr dear estimado estimada
código codigo hora pudo puede está esta usa lingüística linguistica
su sus reunión reunion intervention intervención intervencion switching correo electrónico electronico
""".split())
# SOFT: real surnames or given names too (English, Early, Start, Per); not a name only
# when a model or gazetteer guesses one alone (/cr round 27).
SOFT_NAME = frozenset("english primary secondary start per peer peers within tubes early".split())
COMMON = NEVER_NAME | SOFT_NAME

NON_ID = KIN | REFERENTS | TITLES | FUNCTION | HEADINGS
# For person and place candidates only: these words can be part of organization
# names ("Bilingual Pediatric Therapy Partners", "Pediatric Primary Care").
NON_NAME = NON_ID | COMMON
NON_NAME_STRICT = NON_ID | NEVER_NAME

_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?|\S")


def _is_generic(tok: str, words: frozenset[str] = NON_ID) -> bool:
    raw = tok.strip(".,;:()")
    if len(raw) == 1 and raw.isupper():
        return False  # an initial ("R.A."), not the article "a"
    t = raw.casefold()
    if not t or not any(ch.isalnum() for ch in t):
        return True
    if t in LOWERCASE_ONLY:
        return raw.islower() and (t in words or fold(t).text in words)
    return t in words


def all_generic(value: str, words: frozenset[str] = NON_ID) -> bool:
    """True if every token of `value` is a non-identifier word or punctuation."""
    toks = _TOKEN.findall(value)
    return bool(toks) and all(_is_generic(t, words) for t in toks)


def trim(text: str, start: int, end: int, *, trailing: bool = True,
         words: frozenset[str] = NON_ID) -> tuple[int, int]:
    """Shrink [start, end) past leading (and trailing) non-identifier words."""
    s, e = start, end
    while s < e:
        m = _TOKEN.match(text, s, e) if not text[s].isspace() else None
        if text[s].isspace():
            s += 1
            continue
        if m and _is_generic(m.group(), words) and m.end() < e:
            s = m.end()
            continue
        break
    if trailing:
        while e > s:
            if text[e - 1].isspace():
                e -= 1
                continue
            m = None
            for mm in _TOKEN.finditer(text, s, e):
                m = mm
            if m and m.end() == e and m.start() > s and _is_generic(m.group(), words):
                e = m.start()
                continue
            break
    return s, e
