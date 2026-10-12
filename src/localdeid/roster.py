"""Roster stage: caseload names (SPEC 4.3 `roster`, 4.6 caseload).

Matching is on folded text (no case, no accents), so "Jose", "JOSÉ", and
"josé" all match "José", and offsets map back to the original text. Each
roster name also matches:
- its nicknames (table plus the caseload's own), and Spanish diminutives
  (-ito/-ita, -cito/-cita: Juanito, Anita, Carlitos);
- one-letter misspellings when the name has 6+ letters (as unsure items);
- surname particles inside the name ("de la Cruz").
A hit on a name that is also a word (Ana, Leo, Sol, Rosa, Luz) is decided
per mention: it becomes an unsure check item, hidden by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Iterable

from .spans import Candidate, is_word_char
from .textnorm import fold

RELATION_KIND = {
    "child": "CHILD", "client": "CHILD", "mother": "PARENT", "father": "PARENT", "parent": "PARENT",
    "guardian": "PARENT", "stepmother": "PARENT", "stepfather": "PARENT", "mom": "PARENT",
    "dad": "PARENT", "sister": "SIBLING", "brother": "SIBLING", "sibling": "SIBLING",
    "grandmother": "FAMILY", "grandfather": "FAMILY", "aunt": "FAMILY", "uncle": "FAMILY",
    "cousin": "FAMILY", "family": "FAMILY", "caregiver": "FAMILY", "nanny": "FAMILY",
    "teacher": "TEACHER", "provider": "PROVIDER", "coordinator": "PROVIDER", "therapist": "PROVIDER",
    "doctor": "PROVIDER", "physician": "PROVIDER", "interpreter": "PROVIDER", "peer": "PEER",
    "classmate": "PEER", "friend": "PEER", "pet": "PET",
    "madre": "PARENT", "padre": "PARENT", "mamá": "PARENT", "papá": "PARENT", "hermana": "SIBLING",
    "hermano": "SIBLING", "abuela": "FAMILY", "abuelo": "FAMILY", "tía": "FAMILY", "tío": "FAMILY",
    "prima": "FAMILY", "primo": "FAMILY", "maestra": "TEACHER", "maestro": "TEACHER",
}


@dataclass
class Related:
    name: str  # given name(s), e.g. "Lucía"
    relation: str  # key of RELATION_KIND
    surnames: list[str] = field(default_factory=list)


@dataclass
class Child:
    """One caseload entry (SPEC 4.6: minimal; no addresses, phones, IDs, or DOBs)."""

    first: str
    surnames: list[str] = field(default_factory=list)
    nicknames: list[str] = field(default_factory=list)
    related: list[Related] = field(default_factory=list)
    schools: list[str] = field(default_factory=list)
    target_words: list[str] = field(default_factory=list)


_PARTICLES = frozenset({"de", "del", "la", "las", "los", "y", "da", "di", "van", "von", "der", "le"})


@lru_cache(maxsize=1)
def word_names() -> frozenset[str]:
    text = resources.files("localdeid.data").joinpath("names_that_are_words.txt").read_text("utf-8")
    return frozenset(w for line in text.splitlines() if not line.startswith("#") for w in line.split())


@lru_cache(maxsize=1)
def nickname_table() -> dict[str, frozenset[str]]:
    text = resources.files("localdeid.data").joinpath("nicknames.txt").read_text("utf-8")
    table: dict[str, set[str]] = {}
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        name, nicks = line.split("=", 1)
        table.setdefault(fold(name.strip()).text, set()).update(
            fold(n.strip()).text for n in nicks.split(",") if n.strip()
        )
    return {k: frozenset(v) for k, v in table.items()}


def diminutives(name: str) -> set[str]:
    """Spanish diminutives of a folded given name: juan -> juanito; ana -> anita; carlos -> carlitos."""
    n = name
    out: set[str] = set()
    if len(n) < 3 or " " in n:
        return out
    if n.endswith(("o", "a")):
        stem, g = n[:-1], n[-1]
        out |= {stem + "it" + g, stem + "ill" + g}
        if stem.endswith("c"):
            out.add(stem[:-1] + "quit" + g)  # paco -> paquito
        if stem.endswith("g"):
            out.add(stem + "uit" + g)  # diego -> dieguito
    elif n.endswith("e"):
        out |= {n + "cito", n + "cita"}
    elif n.endswith("os") and len(n) > 4:
        stem = n[:-2]
        out |= {stem + "itos", stem + "ito"}  # carlos -> carlitos
        if stem.endswith("c"):
            out.add(stem[:-1] + "quitos")  # marcos -> marquitos
    elif n.endswith("s"):
        out |= {n + "ito", n + "ita"}  # tomas -> tomasito
    else:
        out |= {n + "ito", n + "ita", n + "cito", n + "cita"}
    return out


def _within_one_edit(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        return len(diff) == 1 or (
            len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
        )
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


@dataclass(frozen=True)
class _Form:
    folded: str  # folded surface form, may hold spaces ("de la cruz", "maria jose")
    kind: str
    exact: bool  # False: one-edit fuzzy form (unsure)
    note: str
    orig: str | None = None  # case-folded roster spelling with accents, for short names


def _forms(children: Iterable[Child]) -> list[_Form]:
    nick = nickname_table()
    forms: dict[tuple[str, str], _Form] = {}

    def add(surface: str, kind: str, note: str, given: bool) -> None:
        from .generic import all_generic

        f = re.sub(r"\s+", " ", fold(surface.strip()).text)
        from .generic import NON_NAME_STRICT

        if sum(ch.isalnum() for ch in f) < 2 or all_generic(surface, NON_NAME_STRICT):
            return  # an entry like "la", "Abuela", "Care", or the initial "W" would hide every mention
        variants = {f}
        if given:
            variants |= set(nick.get(f, ()))
            for canon, nicks in nick.items():  # roster holds a nickname: match the full name too
                if f in nicks:
                    variants.add(canon)
            for v in list(variants):
                variants |= diminutives(v)
        orig = re.sub(r"\s+", " ", surface.strip().casefold())
        for v in variants:
            forms.setdefault((v, kind), _Form(v, kind, True, note, orig if v == f else None))

    for c in children:
        add(c.first, "CHILD", "roster:child", True)
        parts = c.first.split()
        if len(parts) > 1:  # "María José": each given name alone too, never "de" or "los"
            for p in parts:
                if fold(p).text not in _PARTICLES:
                    add(p, "CHILD", "roster:child", True)
        for n in c.nicknames:
            add(n, "CHILD", "roster:nickname", True)
        for s in c.surnames:
            add(s, "SURNAME", "roster:surname", False)
            for p in s.replace("-", " ").split():
                if fold(p).text not in _PARTICLES:
                    add(p, "SURNAME", "roster:surname", False)
        for r in c.related:
            kind = RELATION_KIND.get(r.relation.lower(), "FAMILY")
            add(r.name, kind, f"roster:{r.relation.lower()}", kind != "PET")
            for s in r.surnames:  # "Wumbloo Zorblax": each surname alone too, as for the child
                add(s, "SURNAME", "roster:surname", False)
                for p in s.replace("-", " ").split():
                    if fold(p).text not in _PARTICLES:
                        add(p, "SURNAME", "roster:surname", False)
        for school in c.schools:
            low = school.lower()
            kind = "DAYCARE" if any(w in low for w in ("daycare", "day care", "child care", "learning center",
                                                       "guardería", "guarderia", "preschool")) else "SCHOOL"
            add(school, kind, "roster:school", False)
    out = list(forms.values())
    # One-letter misspellings for single-token names of 6+ letters.
    fuzzy = [_Form(f.folded, f.kind, False, f.note + ":fuzzy") for f in out
             if f.kind != "SCHOOL" and " " not in f.folded and len(f.folded) >= 6]
    return out + fuzzy


def _word_bounds(text: str, s: int, e: int) -> bool:
    return (s == 0 or not is_word_char(text[s - 1])) and (e == len(text) or not is_word_char(text[e]))


def find(text: str, children: Iterable[Child]) -> list[Candidate]:
    children = list(children)
    if not children:
        return []
    folded = fold(text)
    ft = folded.text
    forms = _forms(children)
    words = word_names()
    out: list[Candidate] = []
    inside_state = _state_spans(text)
    exact = [f for f in forms if f.exact]
    fuzzy = {f.folded: f for f in forms if not f.exact}
    # Exact forms: longest first so "maria jose" beats "maria" (merge keeps both anyway).
    for f in sorted(exact, key=lambda f: -len(f.folded)):
        pat = re.compile(r"(?<!\w)" + re.escape(f.folded).replace(r"\ ", r"\s+") + r"(?!\w)")
        for m in pat.finditer(ft):
            s, e = folded.to_source(m.start(), m.end())
            if not _word_bounds(text, s, e) or any(a <= s and e <= b for a, b in inside_state):
                continue  # a child named Carolina is not "North Carolina"
            ambiguous = " " not in f.folded and f.folded in words
            note = f.note + (":word-name" if ambiguous else "")
            # Short names matched only by folding ñ to n ("pena" for "Peña")
            # are often ordinary words: review them (spanish_deid.md section 3).
            if len(f.folded) < 5 and f.orig and "ñ" in f.orig and "ñ" not in text[s:e].casefold():
                ambiguous, note = True, note + ":accent-fold"
            out.append(Candidate(s, e, f.kind, "roster", not ambiguous, 3, note))
    if fuzzy:
        exact_set = {f.folded for f in exact}
        for m in re.finditer(r"\w{5,}", ft):
            tok = m.group()
            if tok in exact_set:
                continue
            s0, _ = folded.to_source(m.start(), m.end())
            if not text[s0].isupper():
                continue  # misspelled names are still capitalized; "media" is not "Medina"
            for name, f in fuzzy.items():
                if abs(len(name) - len(tok)) <= 1 and _within_one_edit(tok, name):
                    s, e = folded.to_source(m.start(), m.end())
                    if _word_bounds(text, s, e):
                        out.append(Candidate(s, e, f.kind, "roster", False, 3, f.note))
                    break
    return out


_STATES_MULTI = re.compile(
    r"(?i)\b(?:north|south|west)\s+(?:carolina|dakota|virginia)\b|\bcarolina\s+del\s+(?:norte|sur)\b|"
    r"\b(?:new\s+(?:york|jersey|mexico|hampshire)|rhode\s+island|el\s+salvador|costa\s+rica|puerto\s+rico|"
    r"santo\s+domingo|ciudad\s+de\s+m[eé]xico|estado\s+de\s+m[eé]xico|nuevo\s+le[oó]n|baja\s+california)\b")


def _state_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _STATES_MULTI.finditer(text)]


def target_words(children: Iterable[Child]) -> list[str]:
    return [w for c in children for w in c.target_words]
