"""Spans, word snapping, and the union-then-split merge (SPEC 4.2, 4.3).

All offsets are code points into the NFC pasted text. A detector emits
`Candidate`s; `merge` turns them into non-overlapping `Entity`s:

1. Snap each candidate outward to whole words, never into a protected token.
2. Apply protect as a hard mask: protected characters are removed from every
   candidate (which may split it). The one exception is a roster candidate,
   which survives as an unsure check item.
3. Take the union of the remaining character coverage, so no tail of an
   identifier is lost when detectors disagree on boundaries.
4. Split the coverage into typed entities: each character takes the
   highest-priority candidate covering it (ties: the longest candidate from
   the same source, then the earliest). Pieces that hold no letter or digit
   (a space or a dash between two names) stay as plain text.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace
from typing import Iterable, Sequence

from .protect import Protected
from .tags import PERSON_KINDS

# Merge priority, highest first (docs/DECISIONS.md item 10).
SOURCE_PRIORITY = {
    "manual": 0, "fields": 1, "roster": 2, "rules": 3, "propagate": 4, "gazetteer": 5,
    "model": 6,
}


@dataclass(frozen=True)
class Candidate:
    start: int
    end: int
    kind: str  # tag type: PERSON, CHILD, PARENT, SURNAME, DATE, PHONE, SCHOOL, ...
    source: str  # key of SOURCE_PRIORITY
    sure: bool = True  # False -> check item (hidden by default)
    role_rank: int = 0  # person role evidence: 3 roster, 2 field, 1 trigger, 0 none
    note: str = ""  # reason code for the check list, e.g. "trigger:su mamá"


@dataclass(frozen=True)
class Entity:
    start: int
    end: int
    kind: str
    sure: bool
    sources: frozenset[str]
    role_rank: int = 0
    notes: tuple[str, ...] = ()
    protected_overlap: bool = False  # a roster hit over a protected token
    roles: tuple[tuple[str, int], ...] = ()  # (person kind, evidence rank) from every covering detector


def is_word_char(ch: str) -> bool:
    """Letters, digits, and combining marks. Apostrophes and hyphens are boundaries."""
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def snap(text: str, start: int, end: int, protected_mask: Sequence[bool]) -> tuple[int, int]:
    """Extend [start, end) outward to whole words without entering a protected char."""
    s, e = start, end
    while s > 0 and is_word_char(text[s - 1]) and is_word_char(text[s]) and not protected_mask[s - 1]:
        s -= 1
    while e < len(text) and e > 0 and is_word_char(text[e]) and is_word_char(text[e - 1]) and (
        not protected_mask[e]
    ):
        e += 1
    return s, e


def protected_mask(n: int, protected: Iterable[Protected]) -> list[bool]:
    mask = [False] * n
    for p in protected:
        for i in range(max(0, p.start), min(n, p.end)):
            mask[i] = True
    return mask


def _strip(text: str, s: int, e: int) -> tuple[int, int]:
    """Trim punctuation at the edges, but keep a parenthesis whose partner is inside:
    "(919) 555-0142" stays whole, not "([PHONE_1]"."""
    s0, e0 = s, e
    while s < e and not is_word_char(text[s]):
        s += 1
    while e > s and not is_word_char(text[e - 1]):
        e -= 1
    if e > s:
        seg = text[s:e]
        if s > s0 and text[s - 1] == "(" and seg.count(")") > seg.count("("):
            s -= 1
        if e < e0 and text[e] == ")" and seg.count("(") > seg.count(")"):
            e += 1
    return s, e


def _pieces(mask: Sequence[bool], s: int, e: int) -> list[tuple[int, int]]:
    """Split [s, e) into maximal runs of unprotected characters."""
    out, i = [], s
    while i < e:
        while i < e and mask[i]:
            i += 1
        j = i
        while j < e and not mask[j]:
            j += 1
        if j > i:
            out.append((i, j))
        i = j
    return out


def merge(text: str, candidates: Iterable[Candidate], protected: Iterable[Protected]) -> list[Entity]:
    n = len(text)
    mask = protected_mask(n, protected)
    prepared: list[tuple[Candidate, bool]] = []  # (candidate, overlaps_protect)
    for c in candidates:
        if not (0 <= c.start < c.end <= n):
            raise ValueError(f"candidate outside text: {c}")
        if c.source not in SOURCE_PRIORITY:
            raise ValueError(f"unknown source {c.source!r}")
        s, e = snap(text, c.start, c.end, mask)
        if c.source in ("roster", "manual") and any(mask[s:e]):
            # Roster hit over a clinical token (a sister named Sol, target word
            # "sol"): keep it whole as a check item. Manual hides are kept as is.
            s2, e2 = _strip(text, s, e)
            if e2 > s2:
                sure = c.sure and c.source == "manual"
                prepared.append((replace(c, start=s2, end=e2, sure=sure), c.source == "roster"))
            continue
        for ps, pe in _pieces(mask, s, e):
            ps, pe = _strip(text, ps, pe)
            if pe > ps:
                prepared.append((replace(c, start=ps, end=pe), False))
    if not prepared:
        return []

    # Each character takes its best candidate.
    def rank(item: tuple[Candidate, bool]) -> tuple[int, int, int]:
        c = item[0]
        return (SOURCE_PRIORITY[c.source], -(c.end - c.start), c.start)

    order = sorted(range(len(prepared)), key=lambda k: rank(prepared[k]))
    owner = [-1] * n
    cover: list[list[int]] = [[] for _ in range(n)]
    for k in order:
        c = prepared[k][0]
        for i in range(c.start, c.end):
            if owner[i] == -1:
                owner[i] = k
            cover[i].append(k)

    entities: list[Entity] = []
    i = 0
    while i < n:
        if owner[i] == -1:
            i += 1
            continue
        k = owner[i]
        j = i
        while j < n and owner[j] == k:
            j += 1
        s, e = _strip(text, i, j)
        if e > s:
            covering = {kk for t in range(s, e) for kk in cover[t]}
            cands = [prepared[kk][0] for kk in covering]
            best = prepared[k][0]
            entities.append(Entity(
                start=s, end=e, kind=best.kind,
                # Unsure only if every covering detector is unsure (fail-closed:
                # unsure items are hidden by default anyway).
                sure=any(c.sure for c in cands) and not any(prepared[kk][1] for kk in covering),
                sources=frozenset(c.source for c in cands),
                role_rank=max(c.role_rank for c in cands),
                roles=tuple(sorted({(c.kind, c.role_rank) for c in cands if c.kind in PERSON_KINDS})),
                notes=tuple(sorted({c.note for c in cands if c.note})),
                protected_overlap=any(prepared[kk][1] for kk in covering),
            ))
        i = j
    return entities


@dataclass
class Coverage:
    """Helper for tests and checks: which characters a set of spans covers."""

    n: int
    bits: list[bool] = field(default_factory=list)

    @classmethod
    def of(cls, n: int, spans: Iterable[tuple[int, int]]) -> "Coverage":
        bits = [False] * n
        for s, e in spans:
            for i in range(s, e):
                bits[i] = True
        return cls(n, bits)
