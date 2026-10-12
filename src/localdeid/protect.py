"""Protect stage: mark clinical tokens that no detector may hide (SPEC 4.3).

A protect span is a hard mask for every detector. The one exception (applied
in merge) is a roster hit inside a protect span, which becomes a check item.
Protection is context-aware in one place: a clinical term right after an
honorific ("Miss Rossetti", "la maestra Bayley") is a person, not a test, so
it is not protected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Iterable


@dataclass(frozen=True)
class Protected:
    start: int
    end: int
    reason: str  # term | code | score | age | ipa | phoneme | salt | quoted | target | cue


# Known SLP procedure codes (slp_context.md Q2) and the 92xxx/97xxx families
# billed by therapists. 5-digit codes collide with ZIP codes, so they are
# protected before any rule runs.
CPT_CODES = frozenset(
    "92507 92508 92521 92522 92523 92524 92526 92597 92605 92606 92607 92608 92609 "
    "92610 92611 92612 92614 92616 92618 92626 92627 96105 96125 97110 97112 97129 "
    "97130 97530 97533 97535 97161 97162 97163 97164 97165 97166 97167 97168 "
    "97150 97153 97155 97158 G0515 S9152 T1024".split()
)

HONORIFIC = (
    r"(?:Mr|Mrs|Ms|Mx|Miss|Mister|Dr|Dra|Sr|Sra|Srta|Prof|Profa|Lic|Mtro|Mtra)\.?|"
    r"(?:teacher|maestra|maestro|señora|señor|señorita|doña|don|tía|tío|abuela|abuelo)"
)
_HONORIFIC_BEFORE = re.compile(rf"(?:\b(?:{HONORIFIC}))\s+$", re.IGNORECASE)
_LOOKBACK = 24  # longest honorific plus spaces

_WORD = r"[^\W_]"  # a letter or digit (Unicode)


def _load_terms() -> tuple[list[str], list[str]]:
    insensitive, sensitive = [], []
    text = resources.files("localdeid.data").joinpath("clinical_terms.txt").read_text("utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("="):
            sensitive.append(line[1:])
        else:
            insensitive.append(line)
    return insensitive, sensitive


_ACCENT_CLASS = {"a": "aá", "e": "eé", "i": "ií", "o": "oó", "u": "uúü", "n": "nñ",
                 "A": "AÁ", "E": "EÉ", "I": "IÍ", "O": "OÓ", "U": "UÚÜ", "N": "NÑ"}
_UNACCENT = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def _term_regex(term: str) -> str:
    """A term with or without its accents: "Comprensión" also matches "Comprension"
    (writers and EMR fields often drop accents)."""
    out = []
    for ch in term:
        base = ch.translate(_UNACCENT)
        if base in _ACCENT_CLASS:
            out.append(f"[{_ACCENT_CLASS[base]}]")
        elif ch == " ":
            out.append(r"\s+")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _alternation(terms: Iterable[str]) -> str:
    # Longest first, so "PLS-5 Spanish" wins over "PLS-5".
    parts = sorted({t for t in terms}, key=len, reverse=True)
    return "|".join(_term_regex(t) for t in parts)


@lru_cache(maxsize=1)
def _term_patterns() -> tuple[re.Pattern[str], re.Pattern[str]]:
    insensitive, sensitive = _load_terms()
    # A term inside a hyphenated record number ("NC-SLP-04521", "ITP-4471298") is part of the ID,
    # but a one- or two-digit edition ("Preschool-3", "Bayley-4") belongs to the test name.
    edition = r"(?:-\d{1,2}(?![\w-]))?"
    edge_l, edge_r = rf"(?<!{_WORD})(?<!{_WORD}-)", rf"(?!{_WORD})(?!-{_WORD})"
    pi = re.compile(rf"{edge_l}(?:{_alternation(insensitive)}){edition}{edge_r}", re.IGNORECASE)
    ps = re.compile(rf"{edge_l}(?:{_alternation(sensitive)}){edition}{edge_r}")
    return pi, ps


# Scores, ratios, and percentages that must never become dates or IDs.
_TRIAL_WORDS = (
    r"trials?|attempts?|opportunit(?:y|ies)|productions?|responses?|tries|items?|"
    r"targets?|words?|times|correct|accuracy|accurate(?:ly)?|oportunidades|ocasiones|"
    r"intentos?|oportunidades?|ensayos?|producciones?|respuestas?|veces|palabras?|"
    r"correct[oa]s?|precisión|aciertos?|utterances?|enunciados?|emisiones?"
)
_NUM_WORDS = (
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|"
    r"cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte"
)
_SCORE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "8/10 trials", "8/10 (80%)", "con 7/10 de precisión", "8/10 accuracy"
    ("score", re.compile(
        rf"(?<![\d/])\d{{1,3}}\s*/\s*\d{{1,3}}(?![\d/])(?=\s*(?:\(\s*\d{{1,3}}\s*%\s*\)|"
        rf"(?:\s*(?:de\s+|of\s+)?(?:(?:las|los|the|sus|his|her)\s+)?(?:\w+-\w+\s+)?(?:{_TRIAL_WORDS})\b)))",
        re.IGNORECASE)),
    # Apgar scores: "Apgar de 9/9", "Apgars 8 and 9", "APGAR 8/9"
    # "8 out of 10", "8 de 10 intentos", "ocho de diez", "4 of 6 models"
    ("score", re.compile(
        rf"\b(?:\d{{1,3}}|{_NUM_WORDS})\s+(?:out\s+of|of|de)\s+(?:\d{{1,3}}|{_NUM_WORDS})\b"
        rf"(?=\s*(?:\(\s*\d{{1,3}}\s*%\s*\)|[\w\s]{{0,25}}?\b(?:{_TRIAL_WORDS}|models?|modelos?|"
        rf"cues?|pistas?|images?|imágenes|pictures?|láminas?)\b))", re.IGNORECASE)),
    # "approximately 3/10 for unfamiliar listeners", "aproximadamente 6/10", "estimated at 4/5"
    ("score", re.compile(
        r"(?i)\b(?:approximately|approx\.?|about|around|roughly|nearly|aproximadamente|aprox\.?|"
        r"alrededor de|casi|estimated at|estimado en|intelligibility(?: of| was| is| at)?|"
        r"inteligibilidad(?: de| del| es| fue)?)\s+(?P<v>\d{1,3}\s*/\s*\d{1,3})(?![\d/])")),
    # "Accuracy: 8/10", "Precisión: 7/10", "Score = 8/10", "trials 8/10"
    ("score", re.compile(
        r"(?i)\b(?:accuracy|accurate|precisi[oó]n|score|scores|puntaje|puntuaci[oó]n|correct|correctas?|"
        r"trials?|intentos?|ensayos?|data|datos|responses?|respuestas?|opportunities|oportunidades)"
        r"\s*[:=]?\s*(?P<v>\d{1,3}\s*/\s*\d{1,3})(?![\d/])")),
    ("score", re.compile(r"(?<![\w.])\d{1,3}(?:\.\d+)?\s?%")),
    # "(7/10)" after a clinical phrase, "6/10 for familiar listeners"
    ("score", re.compile(r"(?<=\()(?P<v>\d{1,2}\s*/\s*\d{1,3})(?=\))")),
    ("score", re.compile(r"(?i)(?P<v>\d{1,2}\s*/\s*\d{1,3})(?=\s+(?:for|para|con|with)\s+(?:un)?(?:familiar|"
                         r"familiares|desconocidos|conocidos|listeners|oyentes))")),
    # "SS 78", "standard score of 78", "percentile 7", "7th percentile", "-1.5 SD"
    ("score", re.compile(
        r"\b(?:SS|SD|PR|SEM|CI|AE|Std\.?\s*Score|standard\s+scores?|scaled\s+scores?|"
        r"percentile(?:\s+rank)?|puntuaci[oó]n(?:\s+est[aá]ndar)?|percentil)\s*(?:of|de|=|:)?\s*"
        r"[-–]?\d{1,3}(?:\.\d+)?\b(?!\s*[./]\d)(?!-\d{1,2}[-./]\d)", re.IGNORECASE)),  # never a date
    # ("CI\n08/14/2026", "CI\n08.14.2026"); "CI 73-83" is a score
    ("score", re.compile(r"\b\d{1,2}(?:st|nd|rd|th)\s+percentile\b", re.IGNORECASE)),
    ("score", re.compile(r"(?<![\w.])[-–+]?\d(?:\.\d+)?\s?SDs?\b")),
    ("score", re.compile(r"\b(?:Outcome|Goal|Objetivo|Meta|Resultado)\s*#?\s*\d{1,2}\b", re.IGNORECASE)),
    # Ages in years;months, "CA: 3;10"
    ("age", re.compile(r"(?<![\w;])(?:0?\d|[1-8]\d);\d{1,2}(?![\w;])")),  # 0;0-89;11, "04;02" (over 89 is an identifier)
    # ICD-10 codes with a dot (F80.2, R48.2), or after an ICD label
    ("code", re.compile(r"\b[A-TV-Z]\d[0-9AB]\.[0-9A-Z]{1,4}\b")),
    ("code", re.compile(r"\bICD(?:-10)?(?:-CM)?\s*[:#]?\s*(?P<v>[A-TV-Z]\d[0-9AB])\b")),
    # SALT error and utterance codes: [EW], [EO:word], [FP], [WO]
    ("salt", re.compile(r"\[(?:EW|EO|EP|EU|EC|FP|WO|OM|ID|NM|SI|PRO|IMP|X+)(?::[^\]\s]{1,20})?\]")),
]

_STREET = (r"St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Boulevard|Way|Ct|Court|Pl|Place|"
           r"Pkwy|Parkway|Cir|Circle|Trl|Trail|Hwy|Highway")
# A known code is protected unless it reads like a house number ("92507 Main St").
# Test names not on the list: "Bayley Scales of Infant Development-4", "Receptive Language Test",
# "Escala de Desarrollo Battelle", "Preschool Language Inventory".
_TEST_SHAPE = re.compile(
    r"\b(?:[A-Z][\w'’-]*\s+){0,5}(?:Scales?|Test|Tests|Inventory|Battery|Profile|Screener|Screening|Checklist|"
    r"Questionnaire|Survey|Assessment of [A-Z][\w'’-]*)(?:\s+(?:of|for|de|del)\s+(?:[A-Z][\w'’-]*\s*){1,6})?"
    r"(?:-\d{1,2}(?![\w-]))?(?:,?\s+(?:(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh)\s+Edition|"
    r"(?:Primera|Segunda|Tercera|Cuarta|Quinta|Sexta)\s+Edición))?"
    r"(?:[,:]?\s+(?:Spanish|English|Bilingual|Spanish-English)(?:[\s-]+(?:Bilingual|English|Spanish))*\s+Edition)?"
    r"|\b(?:[A-Z][\w'’-]*\s+){1,4}Measures\b"
    r"|\b(?:Escala|Prueba|Inventario|Batería|Bateria|Evaluación)\s+(?:de|del)\s+"
    r"(?:[A-ZÁÉÍÓÚ][\w'’-]*\s*){1,6}(?:-\d{1,2}(?![\w-]))?")
_CREDENTIAL_RE = re.compile(r"(?<![\w.])(?:M\.S\.|M\.A\.|M\.Ed\.|Ph\.D\.|Ed\.D\.|OTR/L|CCC-SLP|CF-SLP|COTA/L|"
                            r"DPT|BCBA|LCSW|CCC|SLP-A|SLPA)(?![\w])")
_CPT_RE = re.compile(
    r"(?<![\w-])(?:" + "|".join(sorted(CPT_CODES)) + r")(?![\w-])"
    rf"(?!\s+(?:[A-Z][\w'.-]*\s+){{1,3}}(?:{_STREET})\b)"
)
_ZIP_BEFORE = re.compile(
    r"(?:\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|"
    r"NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC|North Carolina|South Carolina|"
    r"Virginia),?[ \t]+|(?i:\bzip(?:[ \t]*code)?|c\.[ \t]?p\.|"
    r"c[oó]digo postal)[ \t]*[:#]?[ \t]*)$")
_CPT_CONTEXT = re.compile(r"\bCPT(?:\s*(?:code)?s?)?\s*[:#]?\s*(\d{5})\b", re.IGNORECASE)

# IPA notation. A bare word is protected only if it has a character from the
# IPA Extensions block (U+0250-U+02AF) or a stress/length/aspiration mark;
# letters shared with real orthographies (ð, θ, ŋ, ʻ) count only inside
# /slashes/ or [brackets], so names like "Guðrún" or "Hawaiʻi" stay detectable.
_IPA_CHARS = "\u0250-\u02af\u02b0\u02b2\u02b7\u02c8\u02cc\u02d0"
_IPA_RUN = re.compile(rf"[\w{_IPA_CHARS}]*[{_IPA_CHARS}][\w{_IPA_CHARS}]*")
# /k/, /ɾ/, /st/ blends: no digits, no spaces, short, between slashes not part of a path or date
_PHONEME = re.compile(rf"(?<![\w/])/[^\s/\d<>]{{1,6}}/(?![\w/])")
# [w], [ˈpeɾo], [tæt]: lowercase or IPA inside square brackets (tags are uppercase)
_BRACKET_PHONETIC = re.compile(rf"\[[a-zˈˌː{_IPA_CHARS}.\-]{{1,20}}\]")
# Quoted child productions and target words: 1-3 lowercase words in quotes
_QUOTED = re.compile(  # lowercase stimulus words; a comma or period may sit inside the quotes ("sol,")
    r"([\"“”'‘’«])((?:[a-záéíóúüñç]+(?:[-'][a-záéíóúüñç]+)?)(?:\s+[a-záéíóúüñç]+){0,2})[,.;:!?]?([\"“”'‘’»])"
)
# A lowercase word right after "target (word)" or "palabra meta/objetivo" is a stimulus:
# "the target sol (sun)", "target words including sol", "la palabra meta pan".
_TARGET_WORD = re.compile(
    r"(?i:\btargets?(?:\s+words?)?|\bpalabras?\s+(?:meta|objetivo))\s+(?:(?:including|such\s+as|like|"
    r"incluyendo|como)\s+)?(?P<w>[a-záéíóúüñ]+)\b")
# Cue levels: lowercase "max cues", "w/ máx cues táctiles", "maximum (max) cues". A pet named Max is capitalized.
_CUE_LEVEL = re.compile(
    r"(?<![\w])(?:max|máx|mín)\.?(?=\)?\s+(?:[a-záéíóúñ]+\s+)?(?:cues?|cueing|claves|pistas|apoyos?|ayudas?|"
    r"assist(?:ance)?|support|prompts?|prompting|modelos?|models?)\b)"
)
_QUOTE_PAIRS = {'"': '"', "“": "”", "”": "”", "'": "'", "‘": "’", "’": "’", "«": "»"}


_SLASH_PAIR = re.compile(r"(?<![\w/.-])(\d{1,3})\s*/\s*(\d{1,3})(?![\w/]|\.\d)")
_DATE_CUE = re.compile(
    r"(?i)(?:\b(?:on|el|la|del|al|date|dated|fecha|dob|d\.o\.b\.?|born|nacimiento|nació|nacio|seen|visit|visita|"
    r"session on|sesión del|since|desde|until|hasta|before|after|antes del|después del|next|próxima|proxima|"
    r"scheduled|programada|programado|due|review|revisión|revision|meeting|reunión|reunion|week of|semana del|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|lunes|martes|miércoles|miercoles|jueves|viernes|"
    r"sábado|sabado|domingo|mon|tue|wed|thu|fri|sat|sun)\.?|[:#])\s*$")
_TRIAL_DENOMS = set(range(2, 21)) | {24, 25, 30, 40, 50, 100}


# Apgar scores: "Apgar 8/9", "Apgar al nacer: 8/9", "Apgar del recién nacido: 9/8". Privacy
# first: after removing known birth-history descriptors from the gap, any birth or date cue
# left anywhere in it ("DOB was 9/8", "nació 8/9", "born 8/9") makes the numbers a date
# (/cr rounds 28-32 reversed each other; DECISIONS 24 closes the area with this rule).
# No sentence end in the gap, and never part of a longer number ("08/09/2022").
_APGAR = re.compile(r"(?i)\bapgars?\b(?P<gap>[^\d\n.;]{0,30}?)"
                    r"(?P<v>\d{1,2}\s*(?:/|,|and|y)\s*\d{1,2})(?!\d)(?!\s*/\s*\d)")
_APGAR_DESCRIPTOR = re.compile(
    r"(?i)\b(?:al\s+nacer|al\s+nacimiento|at\s+birth|del\s+reci[eé]n\s+nacid[oa]|reci[eé]n\s+nacid[oa]|"
    r"newborn|born\s+(?:at|full)\s+term|born\s+at\s+\d+\s+weeks|full\s+term|at\s+term|"
    r"naci[oó]\s+a\s+t[eé]rmino|nacid[oa]\s+a\s+t[eé]rmino|a\s+t[eé]rmino)\b")
_APGAR_DATE_CUE = re.compile(r"(?i)\b(?:born|dob|d\.o\.b|birth\s*date|birthdate|date\s+of\s+birth|date|fecha|"
                             r"naci[oó]|nacid[oa]|nacimiento)\b|\b(?:el|on)\W*$")


def _apgar_scores(text: str) -> list[Protected]:
    out = []
    for m in _APGAR.finditer(text):
        if _APGAR_DATE_CUE.search(_APGAR_DESCRIPTOR.sub(" ", m.group("gap"))):
            continue
        out.append(Protected(m.start("v"), m.end("v"), "score"))
    return out


# Score ranges after a score cue: "90% CI 73-83", "CI (66–78)", "AE 2-3", "percentile 12-18",
# "intervalo de confianza 70–82". A bare "3-10", or a "range" of dates, is left to the detectors.
_SCORE_RANGE = re.compile(
    r"(?i:\b(?:CI|C\.I\.|IC|confidence\s+interval|intervalo\s+de\s+confianza|SS|standard\s+scores?|"
    r"puntuaci[oó]n(?:\s+est[aá]ndar)?|percentiles?|percentil|AE|age\s+equivalents?|edad\s+equivalente)\b)[^\n\d]{0,12}?(?P<v>\d{1,3}(?:[.,]\d)?\s*[-–—]\s*\d{1,3}(?:[.,]\d)?)(?![\d/-])")


def _ambiguous_scores(text: str) -> list[Protected]:
    """'7/10', '2/3' with no date cue: a clinical score. SLP notes are full of uncued
    accuracy ratios; a real month/day almost always follows a cue ('on 3/10', 'visit 3/10')."""
    out = []
    for m in _SLASH_PAIR.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        if b not in _TRIAL_DENOMS or a > b:
            continue
        before = text[max(0, m.start() - 24):m.start()]
        after = text[m.end():m.end() + 7]
        if _DATE_CUE.search(before) or re.match(r"\s*/\s*(?:19|20)?\d{2}\b|\s*,\s*(?:19|20)\d{2}\b", after):
            continue
        out.append(Protected(m.start(), m.end(), "score"))
    return out


def protect(text: str, target_words: Iterable[str] = ()) -> list[Protected]:
    """Return protected spans (may overlap; merge handles the union)."""
    out: list[Protected] = []
    pi, ps = _term_patterns()
    for pat in (pi, ps):
        for m in pat.finditer(text):
            before = text[max(0, m.start() - _LOOKBACK) : m.start()]
            if _HONORIFIC_BEFORE.search(before) and text[m.start()].isupper():
                continue  # "Miss Rossetti" is a person
            out.append(Protected(m.start(), m.end(), "term"))
    for m in _TEST_SHAPE.finditer(text):
        before = text[max(0, m.start() - _LOOKBACK) : m.start()]
        if not _HONORIFIC_BEFORE.search(before) and len(m.group().split()) >= 2:
            out.append(Protected(m.start(), m.end(), "term"))
    for m in _CREDENTIAL_RE.finditer(text):
        out.append(Protected(m.start(), m.end(), "term"))
    for m in _CPT_RE.finditer(text):
        if _ZIP_BEFORE.search(text[max(0, m.start() - 30):m.start()]):
            continue  # "NC 92507" or "ZIP: 92507": a ZIP code, not a procedure code
        out.append(Protected(m.start(), m.end(), "code"))
    for m in _CPT_CONTEXT.finditer(text):
        out.append(Protected(m.start(1), m.end(1), "code"))
    for reason, pat in _SCORE_PATTERNS:
        for m in pat.finditer(text):
            if "\n" in m.group():
                line = text[text.rfind("\n", 0, m.start()) + 1:m.start() + m.group().index("\n")].strip()
                if "|" in line or "\t" in line:  # cells after the indentation: a header row
                    continue  # a table header's last cell: the next row is data, not this score
            s, e = m.span("v") if "v" in pat.groupindex else m.span()
            while s < e and text[s].isspace():
                s += 1
            if e > s:
                out.append(Protected(s, e, reason))
    out.extend(_ambiguous_scores(text))
    out.extend(_apgar_scores(text))
    for m in _SCORE_RANGE.finditer(text):
        out.append(Protected(m.start("v"), m.end("v"), "score"))
    for m in _PHONEME.finditer(text):
        out.append(Protected(m.start(), m.end(), "phoneme"))
    for m in _BRACKET_PHONETIC.finditer(text):
        out.append(Protected(m.start(), m.end(), "ipa"))
    for m in _IPA_RUN.finditer(text):
        out.append(Protected(m.start(), m.end(), "ipa"))
    for m in _TARGET_WORD.finditer(text):
        out.append(Protected(m.start("w"), m.end("w"), "quoted"))  # a stimulus by position
    for m in _CUE_LEVEL.finditer(text):
        out.append(Protected(m.start(), m.end(), "cue"))
    for m in _QUOTED.finditer(text):
        if _QUOTE_PAIRS.get(m.group(1)) == m.group(3):
            out.append(Protected(m.start(2), m.end(2), "quoted"))
    words = sorted({w.strip() for w in target_words if w and w.strip()}, key=len, reverse=True)
    if words:
        pat = re.compile(
            rf"(?<!{_WORD})(?:{'|'.join(re.escape(w) for w in words)})(?!{_WORD})", re.IGNORECASE
        )
        for m in pat.finditer(text):
            out.append(Protected(m.start(), m.end(), "target"))
    return sorted(out, key=lambda p: (p.start, p.end))

