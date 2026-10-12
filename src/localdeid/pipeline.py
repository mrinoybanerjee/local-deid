"""The de-identification engine (SPEC 4.3):

paste -> protect -> {fields, rules, roster, gazetteer, Privacy Filter} -> merge
-> propagate -> link -> decide -> render -> checks

The engine never rewrites text. It replaces exact character spans with tags
and proves it: `checks` rebuilds the source from the output and the mapping,
and blocks the output if a single character differs.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from dataclasses import replace as _replace
from typing import Callable, Iterable

from . import fields, gazetteer, generic, roster, rules
from .protect import Protected, protect
from .roster import Child
from .spans import SOURCE_PRIORITY, Candidate, Entity, merge
from .tags import PERSON_KINDS, render_tag, scan
from .textnorm import fold, normalize_paste

PIPELINE_VERSION = "0.1.0"

# Privacy Filter label -> tag kind.
PF_KIND = {
    "private_person": "PERSON", "private_date": "DATE", "private_phone": "PHONE",
    "private_email": "EMAIL", "private_url": "URL", "private_address": "PLACE",
    "account_number": "ID", "secret": "ID",
    "private_organization": "ORG",  # the fine-tuned model's added class (DECISIONS 26)
}
PF_UNSURE_BELOW = 0.5  # model spans under this score become check items
ORG_MODEL_MIN = 0.6  # GLiNER spans under this score are dropped (dev2 experiment, DECISIONS 22)
# GLiNER label -> tag kind (gliner_onnx.LABEL_KIND; repeated so the engine needs no model import).
ORG_MODEL_KIND = {"organization": "ORG", "company": "ORG", "daycare": "DAYCARE", "school": "SCHOOL",
                  "government agency": "AGENCY", "hospital or clinic": "HOSPITAL", "church": "ORG",
                  "library": "ORG"}

ROLE_RANK = {"roster": 3, "fields": 2, "rules": 1}
_YEAR_TAIL = re.compile(r"(?:,?\s+(?:de\s+|del\s+)?)((?:19|20)\d{2})$")


@dataclass
class Settings:
    children: list[Child] = field(default_factory=list)
    extra_labels: dict[str, str] = field(default_factory=dict)
    keep_names: list[str] = field(default_factory=list)  # The clinician's own name: kept (SPEC 6)
    employer_names: list[str] = field(default_factory=list)  # The clinician's employer -> [EMPLOYER]


@dataclass
class Item:
    """One replaced (or reviewable) span after linking."""

    start: int
    end: int
    tag: str  # e.g. "CHILD_1" (no brackets)
    kind: str
    key: str  # identity key: same key -> same tag
    sure: bool
    hidden: bool
    sources: tuple[str, ...]
    notes: tuple[str, ...]
    protected_overlap: bool = False
    decision_key: str = ""  # what the clinician's keep/hide decision applies to (SPEC 4.3 decide)

    @property
    def check_item(self) -> bool:
        # Per-mention decisions (word-names, ñ-folds) are always reviewable (SPEC 4.3).
        return (not self.sure or self.protected_overlap or "role-conflict" in self.notes
                or "@" in self.decision_key)


@dataclass
class Instance:
    tag: str  # "" for an escape
    start: int
    end: int
    value: str
    esc: int = 0  # escape number (rendered as ⟦esc⟧) when tag == ""

    @property
    def token(self) -> str:
        return f"[{self.tag}]" if self.tag else f"{ESC_OPEN}{self.esc}{ESC_CLOSE}"


@dataclass
class CheckFailure:
    code: str  # integrity | protected | leak
    word: str
    start: int
    end: int
    detail: str = ""


@dataclass
class Result:
    source: str  # the NFC text all offsets refer to
    nfc_changed: bool
    items: list[Item]
    protected: list[Protected]
    output: str  # tagged text (body only, no ref/context lines)
    instances: list[Instance]  # every tag and escape occurrence in output order
    canonical: dict[str, str]  # tag -> canonical restore value
    failures: list[CheckFailure]
    ref: str
    version: str = PIPELINE_VERSION
    copied_contexts: list[str] = field(default_factory=list)  # context lines of issued copy blocks

    @property
    def blocked(self) -> bool:
        return bool(self.failures)


# --------------------------------------------------------------------------- detect


# Protect reasons that mark a token by its position (quotes, slashes, brackets,
# a cue level), not by matching a word list anywhere in the prose.
_STIMULUS_REASONS = {"quoted", "phoneme", "ipa", "cue"}


def _drop_roster_in_stimulus(text: str, cands: list[Candidate], prot: list[Protected]) -> list[Candidate]:
    """A roster hit written all in lowercase and lying wholly inside a stimulus
    token (a quoted target word, /phonemes/, [IPA], "max cues") is the word, not
    the name: "sol" for a sister named Sol. Clinical writing capitalizes names,
    so capitalized hits, and lowercase hits in running prose, stay check items
    (SPEC 4.3 protect row, narrowed by DECISIONS 21)."""
    out = []
    for c in cands:
        if c.source == "roster" and text[c.start:c.end] == text[c.start:c.end].lower():
            cover = [p for p in prot if p.reason in _STIMULUS_REASONS and p.start < c.end and c.start < p.end]
            if cover and all(any(p.start <= i < p.end for p in cover) for i in range(c.start, c.end)
                             if not text[i].isspace()):
                continue
        out.append(c)
    return out


def _drop_orgs_over_clinical(text: str, cands: list[Candidate], prot: list[Protected]) -> list[Candidate]:
    """An organization match that overlaps a protected token keeps only its
    unprotected words after merge. If those are all generic ("ENT clinic" ->
    "clinic", "NICU at Duke" keeps "Duke"), the match was a clinical phrase."""
    out = []
    for c in cands:
        if c.kind in _ORG_KINDS and c.source not in ("roster", "manual", "fields"):
            over = [p for p in prot if p.start < c.end and c.start < p.end]
            if over:
                rest = "".join(" " if any(p.start <= i < p.end for p in over) else text[i]
                               for i in range(c.start, c.end))
                words = re.findall(r"[^\W\d_]+", rest)
                if all(w.lower() in rules._GENERIC_ORG_WORDS or w.lower() in rules._CLINICAL_PHRASE_WORDS
                       or w.lower() in _ORG_TYPE_WORDS or w.lower() in _HOSPITAL_TYPE_WORDS
                       or generic.all_generic(w) for w in words):
                    continue
        out.append(c)
    return out


_HOSPITAL_TYPE_WORDS = frozenset("clinic clinics clínica clinica hospital hospitals".split())


# Words that never make a GLiNER span a name on their own: place types, roles,
# units, diagnoses, and form labels (dev2 false positives, DECISIONS 22).
# "Carolina", WIC, CDSA, and Smart Start are not here: they name employers and agencies.
_MODEL_ORG_GENERIC = frozenset("""
daycare daycares childcare preschool pre kindergarten kinder school schools escuela escuelas guardería
guarderia preescolar classroom salón salon room aula library biblioteca church iglesia home casa house
store tienda restaurant restaurante farm granja plant planta office oficina agency agencia campus
pathologist patóloga patologa patólogo patologo therapist terapeuta clinician clínico clinico interpreter
intérprete interprete coordinator coordinadora coordinador teacher maestra maestro specialist
especialista consultant consultation consulta evaluator evaluadora evaluador provider proveedor
msw pcp cg pt slp ot
medicaid program programa part parte system sistema nc cna rn lpn np md cma
neonatal intensive care unit cuidados intensivos neonatales trastorno trastornos disorder delay retraso
speech sound relative funded
cell celular phone teléfono telefono address dirección direccion form formulario tabla table
standardized testing sample interview history educational individual
""".split())


_NUMBERED_ORG_HEADS = frozenset("""
school schools escuela escuelas daycare guardería guarderia preschool elementary academy academia center centro
clinic clínica clinica hospital church iglesia district distrito
""".split())  # "School 23", "Guardería 12": a number after one of these names the place


def _model_org_ok(text: str, s: int, e: int, prot: list[Protected], need_capital: bool = True) -> bool:
    """Model organizations (GLiNER, the fine-tuned Privacy Filter) are the least
    trusted. A span is void if it touches a protected (clinical) token, crosses a
    tab, line break, or checkbox, names a test, or holds only generic, clinical,
    role, program, or place-type words. A GLiNER or unsure span is also void if it
    has no capitalized word, or is a capitalized first word at a line or sentence
    start followed only by lowercase words ("Language sample"). A confident span
    from the tuned model (`need_capital=False`) skips those two capitalization
    tests, and an acronym or a digit counts as name evidence ("3M", "AT&T", "H&M")."""
    span = text[s:e]
    if any(p.start < e and s < p.end for p in prot) or re.search(r"[\t\n\[\]]", span):
        return False  # a span across table cells, lines, or checkboxes is not one name
    if rules._TEST_WORDS.search(span):
        return False
    words = [w for w in re.findall(r"[^\W\d_]+", span) if len(w) > 1]
    amp = re.search(r"([A-Z]+)&([A-Z]+)", span) if not need_capital else None
    if amp and (any(len(p) == 1 for p in amp.groups()) or not all(
            p.lower() in rules._GENERIC_ORG_WORDS or p.lower() in rules._CLINICAL_PHRASE_WORDS for p in amp.groups())):
        return True  # "AT&T", "H&M", "P&G": an ampersand name; "ENT&GI" (specialties) falls to the veto
    if not need_capital and re.search(r"\d", span) and not any(
            w.lower() in rules._CLINICAL_PHRASE_WORDS for w in words) and (
            not words or any(w.lower() in _NUMBERED_ORG_HEADS for w in words)):
        # "3M" (no other words), "School 23", "Guardería 12" (an organization head): a number
        # names this one. "Table 2", "sample 2", "Part 2" have no head and fall to the veto
        # below (DECISIONS 24 closure, /cr phase 3 rounds 5-7).
        return True
    if words and all(w.lower() in rules._CLINICAL_PHRASE_WORDS or w.lower() in rules._GENERIC_ORG_WORDS
                     or w.lower() in _MODEL_ORG_GENERIC or w.lower() in _LANGUAGE_WORDS or generic.all_generic(w)
                     for w in words):
        return False  # "NICU", "PCP", "SPEECH SOUND DISORDER": generic or clinical words only
    if not need_capital and re.search(r"\b[A-Z]{2,}\b", span):
        return True  # "UNC Health": an acronym is name evidence
    if not words or (need_capital and not any(w[0].isupper() for w in words)):
        return False
    if not need_capital:
        return True  # confident tuned-model span: no sentence-start veto ("Sunnybrook elementary sent ...")
    i = s
    while i > 0 and text[i - 1] in " \t":
        i -= 1
    at_start = i == 0 or text[i - 1] in "\n.!?:"
    if at_start and words[0][0].isupper() and len(words) > 1 and all(w.islower() for w in words[1:]):
        return False
    return True


def detect(text: str, settings: Settings, pf_detect: Callable[[str], list] | None,
           prot: list[Protected], org_detect: Callable[[str], list] | None = None) -> list[Candidate]:
    cands: list[Candidate] = []
    cands += fields.find(text, settings.extra_labels)
    cands += rules.find(text)
    cands += roster.find(text, settings.children)
    cands += gazetteer.find(text)
    cands += gazetteer.find_places(text)
    if settings.employer_names:
        ft = fold(text)
        for name in settings.employer_names + _employer_acronyms(settings.employer_names):
            f = fold(name).text.strip()
            if not f:
                continue
            for m in re.finditer(r"(?<!\w)" + re.escape(f).replace(r"\ ", r"\s+") + r"(?!\w)", ft.text):
                s, e = ft.to_source(m.start(), m.end())
                cands.append(Candidate(s, e, "EMPLOYER", "roster", True, 3, "setting:employer"))
    if pf_detect is not None:
        for sp in pf_detect(text):
            kind = PF_KIND.get(sp.label)
            if kind == "ORG":
                sure = sp.score >= PF_UNSURE_BELOW
                cands += _model_orgs(text, sp.start, sp.end, prot, sure, f"model:{sp.label}", need_capital=not sure)
            elif kind:
                cands.append(Candidate(sp.start, sp.end, kind, "model", sp.score >= PF_UNSURE_BELOW, 0,
                                       f"model:{sp.label}"))
    # "Alamance Partnership for Children (APC)": an all-caps acronym in brackets right after
    # (or at the end of) an organization is the same organization, and so is every other
    # whole-word mention of it. Rule candidates, so protection still applies ("(ASD)").
    best: dict[str, Candidate] = {}  # acronym -> strongest source, then the nearest organization
    for c in cands:
        if c.kind not in _ORG_KINDS:
            continue
        # after the candidate, possibly past more of the same capitalized run
        # ("Maplecrest Clinic, Audiology Department (MCC)")
        m = (re.match(r"[\w.]*((?:[ \t]*,?[ \t]*(?:[A-ZÁÉÍÓÚÑ][\w'’&.-]*|de|del|of|the|and|y|&)){0,8})"
                      r"[ \t]*\(([A-Z][A-Z&]{1,7})\)", text[c.end:c.end + 120])
             or re.search(r"()\(([A-Z][A-Z&]{1,7})\)$", text[c.start:c.end]))
        if not m or m.group(2).lower() in rules._GENERIC_ORG_WORDS:  # "(ENT)" is a specialty
            continue
        between = [sg for sg in m.group(1).split(",")[1:]]  # comma segments between name and acronym
        if not all(rules.org_segment_ok(sg) for sg in between):
            continue  # "Maplecrest Clinic, Hearing Loss (HL)": the acronym is the finding's
        acr = m.group(2)
        if acr not in best or (SOURCE_PRIORITY[c.source], -c.end) < (SOURCE_PRIORITY[best[acr].source],
                                                                     -best[acr].end):
            best[acr] = c
    for acr, c in best.items():
        for m in re.finditer(rf"(?<![\w&]){re.escape(acr)}(?![\w&])", text):
            cands.append(Candidate(m.start(), m.end(), c.kind, "rules", c.sure, 0, "rule:org-acronym"))
    if org_detect is not None:
        for sp in org_detect(text):
            kind = ORG_MODEL_KIND.get(sp.label)
            if not kind or sp.score < ORG_MODEL_MIN:
                continue
            cands += [c if c.kind == kind else _replace(c, kind=kind)
                      for c in _model_orgs(text, sp.start, sp.end, prot, True, f"model:gliner:{sp.label}")]
    return cands


def _model_orgs(text: str, start: int, end: int, prot: list[Protected], sure: bool, note: str,
                need_capital: bool = True) -> list[Candidate]:
    """A model organization span, judged cell by cell ("Lopez\tLittle Sprouts Daycare")
    and kept only where `_model_org_ok` accepts it. A confident span from the fine-tuned
    Privacy Filter needs no capital letter (dictated "sunnybrook elementary")."""
    out = []
    for m in re.finditer(r"[^\t\n]+", text[start:end]):
        a, b = start + m.start(), start + m.end()
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a and _model_org_ok(text, a, b, prot, need_capital):
            out.append(Candidate(a, b, "ORG", "model", sure, 0, note))
    return out


_NAME_FIELD_KINDS = {"CHILD", "PARENT", "SIBLING", "FAMILY", "TEACHER", "PROVIDER", "PEER", "PERSON",
                     "SURNAME", "PET"}


_ORG_KINDS = {"SCHOOL", "DAYCARE", "HOSPITAL", "AGENCY", "ORG", "EMPLOYER"}


_ORG_TYPE_WORDS = frozenset("""
distrito district escolar school schools escuela escuelas county condado public pública publica unified
unificado
""".split())

def _non_name(source: str | frozenset, value: str) -> frozenset[str]:
    """Words that cannot be a person or place on their own. A caseload entry or a
    manual hide is explicit. generic.NEVER_NAME words ("Spanish", "CG", "Home")
    are never names otherwise; generic.SOFT_NAME words ("Early", "English") are
    names in a form field ("Child: Early") or a longer name ("Dr. Sam English"),
    and not when guessed alone (/cr round 27)."""
    sources = {source} if isinstance(source, str) else set(source)
    if sources & {"roster", "manual"}:
        return generic.NON_ID
    if sources & {"fields", "named"}:
        return generic.NON_NAME_STRICT
    # a guess (one word or several): "Early Intervention" is generic; "Sam English" is not
    return generic.NON_NAME


_LANGUAGE_WORDS = frozenset("""
english spanish español espanol inglés ingles bilingual bilingüe bilingue french francés frances
portuguese portugués portugues primary secondary dominant dominante secundario secundaria
""".split())


def _clean_candidates(text: str, cands: list[Candidate]) -> list[Candidate]:
    """Trim kin words, titles, and articles off name spans ("Mamá Rosa" -> "Rosa"),
    articles and prepositions off organization spans ("At Little Sprouts ..."),
    and drop spans made only of such words ("la niña", "yo", "de")."""
    from dataclasses import replace as _replace

    out = []
    for c in cands:
        if c.source == "manual":
            out.append(c)
            continue
        if c.kind in PERSON_KINDS or c.kind == "SURNAME":
            src = "named" if c.note == "propagate:named" else c.source
            if generic.all_generic(text[c.start:c.end], _non_name(src, text[c.start:c.end])):
                continue
            # descriptors at the edges go ("Ana bilingual"); a caseload or manual entry stays whole
            s, e = generic.trim(text, c.start, c.end, words=(generic.NON_ID if c.source in ("roster", "manual")
                                                              else generic.NON_NAME_STRICT))
            if c.source == "model" and len(text[s:e].strip(" .")) < 2:
                continue  # a stray letter from the model ("M", "S", "Y" after "FAMILIAR")
        elif c.kind in _ORG_KINDS:
            s, e = c.start, c.end
            while True:  # lowercase articles and prepositions only: "El Bajío" keeps its "El"
                m = re.match(r"([a-záéíóúñü]+)[ \t]+", text[s:e])
                if not m or m.group(1) not in generic.FUNCTION:
                    break
                s += m.end()
            m = re.match(r"([^\W\d_]+),[ \t]+", text[s:e])
            if m and m.group(1).lower() in _LANGUAGE_WORDS:
                s += m.end()  # "(in English, Hernández Construction)"
            words = re.findall(r"[^\W\d_]+", text[s:e])
            if c.source != "roster" and words and all(w.lower() in _LANGUAGE_WORDS or w.lower() in generic.FUNCTION
                                                     for w in words):
                continue  # "(in English, ...)": a language name alone is not an organization
        elif c.kind == "PLACE" and generic.all_generic(text[c.start:c.end], _non_name(c.source, text[c.start:c.end])):
            continue  # "Location: Home", "home" are not places
        elif c.kind == "DATE" and c.source == "model":
            core = _date_core(text, c.start, c.end)
            if core is None:
                continue  # "next Tuesday", "aprox. 3 semanas": relative time stays (SPEC 6)
            s, e = core
        else:
            s, e = c.start, c.end
        if e > s:
            out.append(_replace(c, start=s, end=e) if (s, e) != (c.start, c.end) else c)
    return out


_DATE_TOKEN = re.compile(
    r"\d+|(?i:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|abr|ago|dic)[a-záéíóú]*\.?")


_DURATION_AFTER = re.compile(r"(?i)\s*(?:-\s*)?(?:weeks?|wks?|months?|mos?|years?|yrs?|days?|hours?|minutes?|"
                             r"semanas?|meses|mes|años?|anos?|días?|dias?|horas?|minutos?)\b")


def _date_core(text: str, start: int, end: int) -> tuple[int, int] | None:
    """The part of a model date span from its first to its last digit or month word.
    A duration ("2 weeks", "aprox. 3 semanas") is clinical content, not a date."""
    for m in re.finditer(r"\d+", text[start:end]):
        if _DURATION_AFTER.match(text, start + m.end()):
            return None
    toks = list(_DATE_TOKEN.finditer(text, start, end))
    if not toks:
        return None
    return toks[0].start(), toks[-1].end()


_STATE_WORDS = frozenset("nc n.c".split())  # not "Carolina": "servicio de limpieza Carolina" is a name


def _drop_generic_entities(text: str, ents: list[Entity]) -> list[Entity]:
    """Last check after merge: an entity left with only generic words is not an
    identifier. Merging can cut a span down to such a piece ("Piedmont Bilingual"
    -> "Bilingual" when "Piedmont" is a place; "NC" from "NC's Infant-Toddler").
    Entities from the caseload, a manual hide, or a form field are kept."""
    out = []
    for i, e in enumerate(ents):
        if e.sources & {"roster", "manual", "fields"}:
            out.append(e)
            continue
        # a piece touching another entity is part of a longer name ("de la" + "Cruz")
        prev = ents[i - 1] if i > 0 else None
        nxt = ents[i + 1] if i + 1 < len(ents) else None
        if ((prev is not None and re.fullmatch(r"[ \t'’-]*", text[prev.end:e.start]))
                or (nxt is not None and re.fullmatch(r"[ \t'’-]*", text[e.end:nxt.start]))):
            out.append(e)
            continue
        v = text[e.start:e.end]
        if e.kind in PERSON_KINDS or e.kind in ("SURNAME", "PLACE"):
            src = e.sources | ({"named"} if "propagate:named" in e.notes else set())
            if generic.all_generic(v, _non_name(src, v)):
                continue
        elif e.kind in _ORG_KINDS:
            words = [w.lower() for w in re.findall(r"[^\W\d_]+", v)]
            if words and all(w in _LANGUAGE_WORDS or w in generic.FUNCTION or w in _STATE_WORDS
                             or w in generic.NEVER_NAME for w in words):
                continue
        out.append(e)
    return out


def _fuse_orgs(text: str, ents: list[Entity]) -> list[Entity]:
    """'Alamance Smart Start Partnership' + 'for Children' -> one organization: two
    detectors' spans of one name must not leave a generic-looking tail visible."""
    from dataclasses import replace as _replace

    out: list[Entity] = []
    for e in ents:
        prev = out[-1] if out else None
        if (prev is not None and prev.kind in _ORG_KINDS and e.kind in _ORG_KINDS
                and re.fullmatch(r" *", text[prev.end:e.start])):  # a tab separates table cells
            out[-1] = _replace(prev, end=e.end, sources=prev.sources | e.sources, sure=prev.sure or e.sure,
                               notes=tuple(sorted(set(prev.notes) | set(e.notes))))
            continue
        out.append(e)
    return out


def _drop_protect_in_fields(text: str, prot: list[Protected], cands: list[Candidate]) -> list[Protected]:
    """A field label outranks clinical protection only for a confirmed identifier:
    the protected token is the whole field value ("ZIP: 92507", "Mother: Bayley"),
    or a test-name term that is one word of a name value ("Physician: Dr. Ana
    Rossetti"). Codes, scores, and phonemes elsewhere in a field stay protected."""
    fields_c = [c for c in cands if c.source == "fields"]
    keep = []
    for p in prot:
        override = False
        for c in fields_c:
            if not (c.start <= p.start and p.end <= c.end):
                continue
            quotes = " \t\"“”'‘’«»"
            whole = text[c.start:c.end].strip(quotes) == text[p.start:p.end].strip(quotes)
            name_term = (c.kind in _NAME_FIELD_KINDS and p.reason == "term"
                         and len(text[p.start:p.end].split()) == 1)
            if whole or name_term:
                override = True
                break
        if not override:
            keep.append(p)
    return keep


def _employer_acronyms(names: list[str]) -> list[str]:
    """'Carolina Pediatric Speech & Language Therapy, PLLC' -> 'CPSLT', 'CPSL&LT' style forms."""
    out = set()
    for n in names:
        core = re.sub(r",?\s*(?:PLLC|LLC|Inc\.?|P\.C\.|PA)$", "", n.strip())
        words = [w for w in re.findall(r"[A-ZÁÉÍÓÚÑ][\w'’-]*", core)]
        if len(words) >= 2:
            ac = "".join(w[0] for w in words)
            if len(ac) >= 3:
                out.add(ac)
    return sorted(out)


def propagate(text: str, entities: list[Entity], settings: Settings | None = None) -> list[Candidate]:
    """A caught string becomes a span at every whole-word mention (SPEC 4.3).

    A multi-word person name also spreads each part: "Noah Salazar" from a
    field makes every later "Noah" (and "Salazar") a span too.
    """
    ft = fold(text)
    words = roster.word_names()
    surnames = _roster_surnames(settings or Settings())
    seen: set[tuple[str, str]] = set()
    out: list[Candidate] = []
    states = roster._state_spans(text)
    spread_done: set[tuple[str, str]] = set()
    strings: list[tuple[str, str, Entity, int, int]] = []  # (folded, kind, entity, start, end)
    for e in entities:
        if e.kind in ("DATE", "TIME"):
            continue
        if not e.sure:
            # Unsure items spread as unsure, exact case only, so every mention is
            # decided together and "Will" never spreads to the verb "will". Each
            # distinct value spreads once (no quadratic growth on repeated names).
            if (text[e.start:e.end], e.kind) not in spread_done:
                spread_done.add((text[e.start:e.end], e.kind))
                _spread_exact(text, e, out)
            if e.kind in PERSON_KINDS or e.kind == "SURNAME":
                # each capitalized name word of an unsure name, exact case ('Jose "Pepe"' -> "Pepe")
                for m in re.finditer(r"[A-ZÁÉÍÓÚÑÜ][\w'’-]{2,}", text[e.start:e.end]):
                    w = m.group()
                    if (w, e.kind) in spread_done or generic.all_generic(w, generic.NON_NAME):
                        continue
                    spread_done.add((w, e.kind))
                    part = _replace(e, start=e.start + m.start(), end=e.start + m.end())
                    _spread_exact(text, part, out)
            continue
        strings.append((re.sub(r"\s+", " ", fold(text[e.start:e.end]).text).strip(), e.kind, e, e.start, e.end))
        if e.kind in PERSON_KINDS:
            parts = _split_person(text, e, surnames, _roster_given(settings or Settings()))
            if len(parts) > 1:
                for p in parts:
                    for m in re.finditer(r"[^\W\d_][\w'’-]*", text[p.start:p.end]):
                        tok = fold(m.group()).text
                        if len(tok) >= 2 and tok not in _PARTICLES:
                            strings.append((tok, p.kind, e, p.start + m.start(), p.start + m.end()))
    for f, kind, e, es, ee in strings:
        # Generic-word check on the original casing: "Ella" is a name, "ella" is "she".
        if len(f) < 2 or (f, kind) in seen or generic.all_generic(text[es:ee]):
            continue
        seen.add((f, kind))
        ambiguous = " " not in f and f in words  # names that are words: per mention
        pat = re.compile(r"(?<!\w)" + re.escape(f).replace(r"\ ", r"\s+") + r"(?!\w)")
        for m in pat.finditer(ft.text):
            s, t = ft.to_source(m.start(), m.end())
            if (s, t) == (es, ee) or any(a <= s and t <= b for a, b in states):
                continue
            named = e.sources & {"fields", "roster", "manual"} or " " in text[e.start:e.end].strip()
            out.append(Candidate(s, t, kind, "propagate", not ambiguous, 0,
                                 "propagate" + (":word-name" if ambiguous else ":named" if named else "")))
    return out


_PARTICLES = {"de", "del", "la", "las", "los", "y", "da", "van", "von", "der", "di", "le"}


def _spread_exact(text: str, e: Entity, out: list[Candidate]) -> None:
    value = text[e.start:e.end]
    if len(value.strip()) < 2 or generic.all_generic(value):
        return
    pat = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
    for m in pat.finditer(text):
        if m.span() != (e.start, e.end):
            out.append(Candidate(m.start(), m.end(), e.kind, "propagate", False, 0, "propagate:unsure"))


def _roster_given(settings: Settings) -> set[str]:
    names = set()
    for c in settings.children:
        names.add(c.first)
        names.update(c.nicknames)
        names.update(r.name for r in c.related)
    return {re.sub(r"\s+", " ", fold(n).text).strip() for n in names if n.strip()}


def _keep_forms(settings: Settings) -> tuple[set[str], set[str]]:
    """The clinician's own name (SPEC 6): full names, and first given names alone."""
    full = {re.sub(r"\s+", " ", fold(k).text).strip() for k in settings.keep_names if k.strip()}
    first = {f.split()[0] for f in full if f}
    return full, first


def _keep_spans(text: str, settings: Settings) -> list[tuple[int, int]]:
    """Where the clinician's full name appears in `text` (folded, whole words)."""
    full, _ = _keep_forms(settings)
    if not full:
        return []
    ft = fold(text)
    spans = []
    for f in full:
        pat = re.compile(r"(?<!\w)" + re.escape(f).replace(r"\ ", r"\s+") + r"(?!\w)")
        spans += [ft.to_source(m.start(), m.end()) for m in pat.finditer(ft.text)]
    return spans


def _is_exempt(text: str, start: int, end: int, settings: Settings,
               keep_spans: list[tuple[int, int]]) -> bool:
    """Values that stay by policy (SPEC 6): the clinician's name, only inside an
    occurrence of her full name or as her first name alone; and a bare year."""
    value = text[start:end]
    if any(a <= start and end <= b for a, b in keep_spans):
        return True
    _, first = _keep_forms(settings)
    v = re.sub(r"\s+", " ", fold(value).text).strip()
    if v in first and len(value.split()) == 1:
        return True
    return bool(re.fullmatch(r"(?:19|20)\d{2}", value.strip()))


def _roster_surnames(settings: Settings) -> set[str]:
    out = {fold(p).text for c in settings.children for s in c.surnames for p in s.replace("-", " ").split()}
    out |= {fold(s).text for c in settings.children for r in c.related for s in r.surnames}
    return out - _PARTICLES


# --------------------------------------------------------------------------- link


@dataclass
class _Part:
    start: int
    end: int
    kind: str
    entity: Entity


def _split_person(text: str, e: Entity, roster_surnames: set[str],
                  roster_given: frozenset[str] | set[str] = frozenset()) -> list[_Part]:
    """'Lucía Rodríguez' (PARENT) -> 'Lucía' PARENT + 'Rodríguez' SURNAME.

    Given names known from the roster ("María José") stay together. Otherwise a
    trailing token counts as a surname only on surname-specific evidence: a
    roster surname, or a listed surname that is not also a listed first name.
    With no evidence, the last token of a multi-word name is the surname.
    """
    toks = [(m.start() + e.start, m.end() + e.start) for m in re.finditer(r"[^\W\d_][\w'’-]*", text[e.start:e.end])]
    if len(toks) < 2:
        return [_Part(e.start, e.end, e.kind, e)]
    folded = [fold(text[a:b]).text for a, b in toks]
    if all(f in _PARTICLES for f in folded):
        return [_Part(e.start, e.end, e.kind, e)]  # "de la": attached to the surname later
    if " ".join(folded) in roster_given:
        return [_Part(e.start, e.end, e.kind, e)]  # the whole span is a known given name
    given_n = 0
    for n in range(len(toks) - 1, 0, -1):  # longest roster given-name run at the start
        if " ".join(folded[:n]) in roster_given:
            given_n = n
            break
    if given_n:
        surname_start = given_n
    else:
        surname_start = None
        for i in range(len(toks) - 1, 0, -1):
            tok = folded[i]
            if tok in _PARTICLES:
                continue
            if tok in roster_surnames or (gazetteer.is_surname(tok) and not gazetteer.is_first_name(tok)):
                surname_start = i
            else:
                break
        if surname_start is None:
            surname_start = len(toks) - 1  # "First Last" with an unknown last name
    while surname_start > 1 and folded[surname_start - 1] in _PARTICLES:
        surname_start -= 1
    given_end = toks[surname_start - 1][1]
    return [_Part(e.start, given_end, e.kind, e), _Part(toks[surname_start][0], e.end, "SURNAME", e)]


ROSTER_RANK = 3
_ROLE_ORDER = ["CHILD", "PARENT", "SIBLING", "FAMILY", "PEER", "TEACHER", "PROVIDER", "PET", "PERSON"]


def _norm(s: str) -> str:
    """Identity form of a value: case- and accent-folded, whitespace collapsed."""
    return re.sub(r"\s+", " ", fold(s).text).strip()


_CONJ = re.compile(r"[ \t]*(?:,[ \t]*)?(?:\band\b|\by\b|&|/)[ \t]*|[ \t]*,[ \t]*")


def _split_coordinated(text: str, e: Entity) -> list[Entity]:
    """'John Smith and Jane Doe' -> two people; the conjunction stays plain text."""
    from dataclasses import replace as _replace

    value = text[e.start:e.end]
    pieces, pos = [], 0
    for m in _CONJ.finditer(value):
        if m.start() > pos:
            pieces.append((pos, m.start()))
        pos = m.end()
    if pos < len(value):
        pieces.append((pos, len(value)))
    if len(pieces) < 2:
        return [e]
    return [_replace(e, start=e.start + a, end=e.start + b) for a, b in pieces if value[a:b].strip()]


def _attach_particles(text: str, parts: list[_Part]) -> list[_Part]:
    """'Mateo de la Cruz': a particle-only part between two name parts joins the
    next part, so 'de la' is hidden with the surname instead of left visible."""
    from dataclasses import replace as _replace

    parts = sorted(parts, key=lambda p: p.start)
    out: list[_Part] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        words = [fold(w).text for w in text[p.start:p.end].split()]
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        prev = out[-1] if out else None
        name_kinds = PERSON_KINDS + ("SURNAME",)
        if (words and all(w in _PARTICLES for w in words) and nxt is not None and prev is not None
                and prev.kind in name_kinds and nxt.kind in name_kinds
                and text[prev.end:p.start].strip() == "" and text[p.end:nxt.start].strip() == ""):
            out.append(_replace(nxt, start=p.start))
            i += 2
            continue
        out.append(p)
        i += 1
    return out


def _identity_group(kind: str) -> str:
    return "NAME" if kind in PERSON_KINDS else kind


_NUM_DATE_YEAR_LAST = re.compile(r"(\d{1,2}[/.-]\d{1,2})([/.-])(\d{4}|\d{2})")
_NUM_DATE_YEAR_FIRST = re.compile(r"((?:19|20)\d{2})([/.-])(\d{1,2}[/.-]\d{1,2})")


def link(text: str, entities: list[Entity], settings: Settings) -> list[Item]:
    roster_surnames = _roster_surnames(settings)
    roster_given = _roster_given(settings)
    keep_spans = _keep_spans(text, settings)

    parts: list[_Part] = []
    for e in entities:
        value = text[e.start:e.end]
        if (e.kind in PERSON_KINDS + ("SURNAME",) and "roster" not in e.sources
                and _is_exempt(text, e.start, e.end, settings, keep_spans)):
            continue  # The clinician's own name stays (SPEC 6), unless the roster says it is a client
        if "manual" in e.sources:
            parts.append(_Part(e.start, e.end, e.kind, e))  # The clinician's own selection: kept whole
        elif e.kind in PERSON_KINDS:
            for sub in _split_coordinated(text, e):
                parts.extend(_split_person(text, sub, roster_surnames, roster_given))
        elif e.kind == "DATE":
            m = _YEAR_TAIL.search(text, e.start, e.end)
            n_last = _NUM_DATE_YEAR_LAST.fullmatch(value)
            n_first = _NUM_DATE_YEAR_FIRST.fullmatch(value)
            # The year stays (SPEC 6): "March 3, 2026", "3 de marzo de 2026",
            # "03/14/2021" -> "[DATE_1]/2021", "2021-03-14" -> "2021-[DATE_1]".
            if m and m.end() == e.end and m.start() > e.start:
                parts.append(_Part(e.start, m.start(), e.kind, e))
            elif n_last:
                parts.append(_Part(e.start, e.start + n_last.end(1), e.kind, e))
            elif n_first:
                parts.append(_Part(e.start + n_first.start(3), e.end, e.kind, e))
            elif re.fullmatch(r"(?:19|20)\d{2}", value):
                continue  # a bare year is allowed
            else:
                parts.append(_Part(e.start, e.end, e.kind, e))
        else:
            parts.append(_Part(e.start, e.end, e.kind, e))

    parts = _attach_particles(text, parts)
    parts = [p for p in parts if "manual" in p.entity.sources or not generic.all_generic(text[p.start:p.end])]
    # Resolve one role per identity: roster > field > trigger > none. Each
    # detector's role keeps its own evidence rank (a field's TEACHER never
    # borrows the roster's rank for PARENT).
    best: dict[str, tuple[int, int, str]] = {}  # key -> (rank, first_pos, kind)
    conflicts: set[str] = set()
    for p in parts:
        key = f"{_identity_group(p.kind)}:{_norm(text[p.start:p.end])}"
        if p.kind in PERSON_KINDS:
            evidence = [(k, r) for k, r in p.entity.roles if k != "PERSON"] or [("PERSON", 0)]
        else:
            evidence = [(p.kind, 0)]
        for kind, rank in evidence:
            cur = best.get(key)
            if cur is None or rank > cur[0]:
                best[key] = (rank, p.start, kind)
            elif rank == cur[0] and kind != cur[2] and rank > 0:
                if rank == ROSTER_RANK:
                    # Two caseload entries share a name (two children called Mateo):
                    # take the closer relation, without a check item.
                    if _ROLE_ORDER.index(kind) < _ROLE_ORDER.index(cur[2]):
                        best[key] = (rank, cur[1], kind)
                else:
                    conflicts.add(key)

    parts = [p for p in parts if "manual" in p.entity.sources or not generic.all_generic(text[p.start:p.end])]
    numbers: dict[str, dict[str, int]] = {}
    words = roster.word_names()
    items: list[Item] = []
    for p in sorted(parts, key=lambda p: p.start):
        key = f"{_identity_group(p.kind)}:{_norm(text[p.start:p.end])}"
        kind = best[key][2] if p.kind != "SURNAME" else "SURNAME"
        per_type = numbers.setdefault(kind, {})
        if key not in per_type:
            per_type[key] = len(per_type) + 1
        tag = render_tag(kind, per_type[key])[1:-1]
        notes = p.entity.notes + (("role-conflict",) if key in conflicts else ())
        value = re.sub(r"\s+", " ", fold(text[p.start:p.end]).text).strip()
        # Names that are words, ñ-folds, and roster hits over clinical tokens are
        # decided per mention (SPEC 4.3); everything else per value.
        per_mention = p.entity.protected_overlap or value in words or (
            " " not in value and any("accent-fold" in n for n in notes))
        decision = f"{key}@{p.start}" if per_mention else key
        items.append(Item(p.start, p.end, tag, kind, key, p.entity.sure, True,
                          tuple(sorted(p.entity.sources)), notes, p.entity.protected_overlap, decision))
    return items


# --------------------------------------------------------------------------- render


ESC_OPEN, ESC_CLOSE = "⟦", "⟧"


def _tag_like(text: str) -> list[tuple[int, int]]:
    """Source text a reader could take for a tag, an escape, or residue: it is
    escaped, so restore can tell the original text from AI output."""
    return [(t.start, t.end) for t in scan(text)]


def render(text: str, items: list[Item]) -> tuple[str, list[Instance], dict[str, str]]:
    """Replace hidden items with tags. Tag-like source text becomes an escape
    ⟦n⟧ that restores to the exact original characters."""
    repl: list[tuple[int, int, str]] = []  # (start, end, tag or "")
    covered = [False] * (len(text) + 1)
    for it in items:
        if it.hidden:
            for i in range(it.start, it.end):
                covered[i] = True
            repl.append((it.start, it.end, it.tag))
    for s, e in _tag_like(text):
        if not any(covered[s:e]):
            repl.append((s, e, ""))
    repl.sort()
    for a, b in zip(repl, repl[1:]):
        if a[1] > b[0]:
            raise AssertionError("overlapping replacements")
    out: list[str] = []
    instances: list[Instance] = []
    canonical: dict[str, str] = {}
    pos = 0
    n_esc = 0
    for s, e, tag in repl:
        if not tag:
            n_esc += 1
        inst = Instance(tag, s, e, text[s:e], 0 if tag else n_esc)
        out.append(text[pos:s])
        out.append(inst.token)
        instances.append(inst)
        if tag:
            canonical.setdefault(tag, text[s:e])
        pos = e
    out.append(text[pos:])
    return "".join(out), instances, canonical


def rebuild(output: str, text: str, instances: list[Instance]) -> str:
    """Byte-exact inverse of render for an unedited output (integrity proof)."""
    res: list[str] = []
    pos_out = 0
    pos_src = 0
    for inst in instances:
        literal = text[pos_src:inst.start]
        if output[pos_out:pos_out + len(literal)] != literal:
            raise ValueError("output differs from source outside a replaced span")
        res.append(literal)
        pos_out += len(literal)
        if output[pos_out:pos_out + len(inst.token)] != inst.token:
            raise ValueError("output differs from the recorded replacement")
        res.append(inst.value)
        pos_out += len(inst.token)
        pos_src = inst.end
    tail = text[pos_src:]
    if output[pos_out:] != tail:
        raise ValueError("output tail differs from source")
    res.append(tail)
    return "".join(res)


# --------------------------------------------------------------------------- checks


def run_checks(text: str, items: list[Item], prot: list[Protected], output: str,
               instances: list[Instance], settings: Settings) -> list[CheckFailure]:
    failures: list[CheckFailure] = []
    # 1. Integrity: the source must rebuild exactly from output + mapping.
    try:
        if rebuild(output, text, instances) != text:
            failures.append(CheckFailure("integrity", "", 0, 0, "rebuild mismatch"))
    except ValueError as exc:
        failures.append(CheckFailure("integrity", "", 0, 0, str(exc)))
    # 2. Protected tokens: no hidden span may contain a protected token, except
    #    a roster hit over one, which is surfaced as a check item.
    for it in items:
        if not it.hidden or it.protected_overlap or "manual" in it.sources:
            continue
        for p in prot:
            if p.start < it.end and it.start < p.end:
                failures.append(CheckFailure("protected", text[p.start:p.end], p.start, p.end,
                                             f"inside {it.tag}"))
    # 3. Leaks: a hidden value must not remain anywhere in the output, and the
    #    format rules and roster must find nothing new in the output.
    masked = _mask_tags(output, instances)
    fm = fold(masked)
    out_prot = protect(masked, roster.target_words(settings.children))
    out_states = roster._state_spans(masked)
    prot_mask = [False] * (len(masked) + 1)
    for pp in out_prot:
        for i in range(pp.start, pp.end):
            prot_mask[i] = True
    kept = {fold(output[0:0]).text}
    for it in items:
        if it.hidden:
            continue
        kept.add(re.sub(r"\s+", " ", fold(text[it.start:it.end]).text).strip())
    for it in items:
        if not it.hidden or it.kind in ("DATE", "TIME"):
            continue  # a month word ("May") recurring as a verb is not a leak
        v = re.sub(r"\s+", " ", fold(text[it.start:it.end]).text).strip()
        words = (_non_name(frozenset(it.sources), text[it.start:it.end])
                 if (it.kind in PERSON_KINDS or it.kind in ("SURNAME", "PLACE")) else generic.NON_ID)
        if it.kind in _ORG_KINDS and all(w in _LANGUAGE_WORDS or w in generic.FUNCTION or w in generic.NEVER_NAME
                                         for w in re.findall(r"[^\W\d_]+", v)):
            continue  # "Bilingual" left as an organization piece: the word is not a leak
        if len(v) < 2 or v in kept or generic.all_generic(text[it.start:it.end], words):
            continue
        # "Mama" or "Care" hidden in a name does not make "mama" or "care" a leak: exact case only
        if it.sure and v not in generic.LOWERCASE_ONLY and v not in generic.NEVER_NAME:
            pat = re.compile(r"(?<!\w)" + re.escape(v).replace(r"\ ", r"\s+") + r"(?!\w)")
            hits = [fm.to_source(m.start(), m.end()) for m in pat.finditer(fm.text)]
        else:  # unsure items are matched with exact case (see propagate)
            raw = text[it.start:it.end]
            hits = [m.span() for m in re.finditer(r"(?<!\w)" + re.escape(raw) + r"(?!\w)", masked)]
        for s, e in hits:
            if all(prot_mask[s:e]) or any(a <= s and e <= b for a, b in out_states):
                continue  # a protected clinical token, or part of "North Carolina", is not a leak
            failures.append(CheckFailure("leak", masked[s:e], s, e, f"value of {it.tag} remains"))
    # Field values are read with tags as spaces: "Fecha: [DATE_1] de 2024" holds no new value.
    spaced = masked.replace(_MASK_FILL, " ")
    second = rules.find(masked) + roster.find(masked, settings.children) + fields.find(spaced, settings.extra_labels)
    if second:
        leftover = merge(masked, second, out_prot)
        out_keep = _keep_spans(masked, settings)
        for e in leftover:
            raw = masked[e.start:e.end]
            v = re.sub(r"\s+", " ", fold(raw).text).strip()
            words = (_non_name(e.sources, raw) if (e.kind in PERSON_KINDS or e.kind in ("SURNAME", "PLACE"))
                     else generic.NON_ID)
            if (not e.sure or v in kept or not v or generic.all_generic(raw, words)
                    or _is_exempt(masked, e.start, e.end, settings, out_keep)):
                continue
            if _HONORIFIC_ONLY.fullmatch(raw.strip()):
                continue  # a title left next to a hidden name ("Dr. [PROVIDER_1]")
            words = re.findall(r"[^\W\d_]+", raw)
            if e.kind in _ORG_KINDS and all(w.lower() in rules._GENERIC_ORG_WORDS or w.lower() in _ORG_TYPE_WORDS
                                            or w.lower() in _LANGUAGE_WORDS or w.lower() in generic.FUNCTION
                                            or generic.all_generic(w) for w in words):
                continue  # "Distrito [SCHOOL_1]", "Spanish [PROVIDER_8], English": words left beside tags
            failures.append(CheckFailure("leak", masked[e.start:e.end], e.start, e.end,
                                         f"{e.kind} found in output"))
    return failures


_HONORIFIC_ONLY = re.compile(r"(?i)(?:dr|dra|mr|mrs|ms|mx|miss|sr|sra|srta|prof|profa|doña|don|lic)\.?")


_MASK_FILL = "\u2063"  # INVISIBLE SEPARATOR: not a space, letter, or digit, so no rule joins words across it


def _mask_tags(output: str, instances: list[Instance]) -> str:
    """Replace the rendered tags and escapes (and only those: a checkbox "[X]"
    is text) with filler of the same length. The filler is not whitespace, so
    "[X] Home [CHILD_1] Daycare" cannot become one "Home Daycare" run."""
    out, pos, delta = [], 0, 0
    for inst in instances:
        tok = inst.token
        at = inst.start + delta
        if output[at:at + len(tok)] != tok:
            raise ValueError("instance does not match the output")
        out.append(output[pos:at])
        out.append(_MASK_FILL * len(tok))
        pos = at + len(tok)
        delta += len(tok) - (inst.end - inst.start)
    out.append(output[pos:])
    return "".join(out)


# --------------------------------------------------------------------------- run


_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I


def new_ref() -> str:
    return "".join(secrets.choice(_REF_ALPHABET) for _ in range(4))


def deidentify(raw: str, settings: Settings | None = None, *,
               pf_detect: Callable[[str], list] | None = None,
               org_detect: Callable[[str], list] | None = None,
               keep_keys: Iterable[str] = (), manual_hides: Iterable[tuple[int, int]] = (),
               ref: str | None = None) -> Result:
    settings = settings or Settings()
    text, changed = normalize_paste(raw)
    prot = protect(text, roster.target_words(settings.children))
    cands = _drop_roster_in_stimulus(text, detect(text, settings, pf_detect, prot, org_detect), prot)
    prot = _drop_protect_in_fields(text, prot, cands)
    cands = _drop_orgs_over_clinical(text, _clean_candidates(text, cands), prot)
    for s, e in manual_hides:
        cands.append(Candidate(s, e, "PERSON", "manual", True, 0, "manual"))
    ents = _drop_generic_entities(text, _fuse_orgs(text, merge(text, cands, prot))) if cands else []
    extra = propagate(text, ents, settings)
    if extra:
        ents = _drop_generic_entities(text, _fuse_orgs(text, merge(text, cands + extra, prot)))
    items = link(text, ents, settings)
    keep = set(keep_keys)
    for it in items:
        if it.decision_key in keep:
            it.hidden = False  # The clinician un-hid it (SPEC 4.3 decide)
    output, instances, canonical = render(text, items)
    failures = run_checks(text, items, prot, output, instances, settings)
    return Result(text, changed, items, prot, output, instances, canonical, failures, ref or new_ref())
