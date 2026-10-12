"""Score the engine against labelled documents (SPEC 3 gates, 4.8).

Per document:
- an identifier is caught only if every letter and digit is inside a hidden
  item (type-agnostic: any tag hides it); a leading title and a date's year
  are optional, because SPEC 6 lets them stay;
- the document is fully clean if every identifier is caught;
- a clinical hide is any hidden item that overlaps a KEEP span;
- the review load is the number of distinct check items (one row per key).
Recall per type gets a Wilson 95% lower bound.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .evaldata import Doc
from . import generic
from .evaldata import GOLD_TYPES as GOLD_COARSE
from .pipeline import Result


@dataclass
class DocScore:
    doc_id: str
    stratum: str
    caught: dict[str, list[int]]  # coarse type -> [caught, total]
    caught_fine: dict[str, list[int]]  # fine type (PHONE, EMAIL, MEDICAID_ID, ...) -> [caught, total]
    missed: list[tuple[str, str]]  # (type, surface)
    clinical_hides: list[str]
    check_items: int
    blocked: bool
    decisions: int = 0
    clinical_sources: dict = field(default_factory=dict)  # detector:kind -> clinical hides
    extra_hides: list[str] = field(default_factory=list)  # hidden values overlapping no gold span

    @property
    def clean(self) -> bool:
        return not self.missed


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


_LEADING_TITLE = re.compile(r"(?i)(?:dr|dra|mr|mrs|ms|mx|miss|sr|sra|srta|prof|profa|doña|don|lic|ing)\.?\s+")
_CREDENTIALS_TAIL = re.compile(r",\s*(?:(?:[A-Z][A-Za-z]{0,3}\.?){1,4}(?:[-/][A-Za-z]{1,4})*\s*,?\s*)+$")
_YEAR_CONNECTOR = re.compile(r"\b(de|del|of)\s+$", re.IGNORECASE)
_YEAR4 = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_YY = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-](\d{2})")  # m/d/yy


# Lowercase descriptors inside or after an organization name carry no identity:
# "Guilford County CDSA office", "Duke Health, campus Durham" (DECISIONS 25).
_ORG_DESCRIPTORS = frozenset("campus office oficina program programa agency agencia branch sucursal center "
                             "centro clinic clínica department departamento services servicios".split())


def required_chars(text: str, start: int, end: int, gold_type: str) -> list[int]:
    """Characters of a gold identifier that must be hidden. Allowed by SPEC 6 and
    so optional: a leading title ("Sra. Ortiz") and, in dates, the year."""
    # Leading titles, kin words, and articles may stay ("la maestra Carmen", "tía Marisol").
    s, _ = generic.trim(text, start, end, trailing=False)
    if GOLD_COARSE.get(gold_type) == "ORG":
        # "la planta empacadora de carne Smithfield": lowercase descriptors are not the name
        m0 = re.match(r"(?:[a-záéíóúñü]+[ \t]+)+(?=[A-ZÁÉÍÓÚÑ0-9])", text[s:end])
        if m0:
            s += m0.end()
    m = _LEADING_TITLE.match(text, s, end)
    s = m.end() if m and m.end() < end else s
    from .fields import _strip_credentials

    end = s + _strip_credentials(text[s:end])  # "Carla Fuentes, M.S., CCC-SLP": credentials are not identity
    optional: set[int] = set()
    if GOLD_COARSE.get(gold_type) == "ORG":
        # "X ENT at Novant Health", "Duke Health, campus Durham": a lowercase connector inside
        # the name carries no identity; the capitalized parts around it stay required.
        for w in re.finditer(r"(?<=\s)[a-záéíóúñü]+(?=[\s,.;:)]|$)", text[s:end]):
            if w.group() in generic.FUNCTION or w.group() in _ORG_DESCRIPTORS:
                optional.update(range(s + w.start(), s + w.end()))
    if gold_type in ("ID", "MEDICAID_ID"):
        # a leading label is not the number: "ID #SLP-48217-C", "No. 4471", "MRN: 12345"
        lab = re.match(r"(?i)(?:(?:N[uú]mero|MRN|ID|No|Num)\.?(?![^\W\d_])[ \t]*[#:]?|#)[ \t]*", text[s:end])
        if lab and lab.end() < end - s:
            optional.update(range(s, s + lab.end()))
    if gold_type == "PLACE":
        # a state or country is not an identifier (SPEC 6): "Greensboro, Carolina del Norte"
        st = re.search(r"(?i)(?:,\s*|\s+)(?:NC|N\.C\.|North\s+Carolina|Carolina\s+del\s+Norte|USA|"
                       r"EE\.?\s?UU\.?)[\s.]*$", text[s:end])
        if st and st.start() > 0:
            optional.update(range(s + st.start(), end))
    if gold_type == "DATE":
        span = text[s:end]
        years = list(_YEAR4.finditer(span))
        for y in years:
            optional.update(range(s + y.start(), s + y.end()))
            con = _YEAR_CONNECTOR.search(span, 0, y.start())  # "de 2026", "of 2026"
            if con:
                optional.update(range(s + con.start(1), s + con.end(1)))
        yy = _YY.fullmatch(span.strip())
        if not years and yy:
            lead = len(span) - len(span.lstrip())
            optional.update(range(s + lead + yy.start(1), s + lead + yy.end(1)))
    # Letters and digits carry the identity; punctuation beside a kept year
    # ("March 3, 2026" -> "[DATE_1], 2026") does not.
    return [i for i in range(s, end) if text[i].isalnum() and i not in optional]


def score_doc(doc: Doc, result: Result) -> DocScore:
    if result.source != doc.text:
        raise ValueError(f"{doc.doc_id}: engine text differs from gold text (NFC change?)")
    hidden = bytearray(len(doc.text))
    for it in result.items:
        if it.hidden:
            hidden[it.start:it.end] = b"\x01" * (it.end - it.start)
    caught: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    fine: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    missed = []
    for g in doc.identifiers:
        chars = required_chars(doc.text, g.start, g.end, g.type)
        ok = all(hidden[i] for i in chars)
        caught[g.coarse][1] += 1
        caught[g.coarse][0] += int(ok)
        fine[g.type][1] += 1
        fine[g.type][0] += int(ok)
        if not ok:
            missed.append((g.type, doc.text[g.start:g.end]))
    clinical = []
    clinical_sources: dict[str, int] = defaultdict(int)
    for k in doc.keeps:
        hits = [it for it in result.items if it.hidden and it.start < k.end and k.start < it.end]
        if hits:
            clinical.append(doc.text[k.start:k.end])
            for src in sorted({f"{s}:{it.kind}" for it in hits for s in it.sources}):
                clinical_sources[src] += 1
    # SPEC 5.2: the check list has one row per value ("Each row shows the number of
    # mentions"); word-names get per-mention toggles inside their row. The gate
    # counts rows; decisions (rows plus extra per-mention toggles) are reported too.
    checks = len({it.key for it in result.items if it.check_item})
    decisions = len({it.decision_key or it.key for it in result.items if it.check_item})
    # Over-hiding outside the KEEP labels: a hidden item touching no gold span at all
    # ("Duke Hospital. He" hid "He"). Also counts identifiers the generator did not label,
    # so it is a trend to watch, not a gate.
    gold = [(g.start, g.end) for g in doc.spans]
    extra = [doc.text[it.start:it.end] for it in result.items
             if it.hidden and not any(a < it.end and it.start < b for a, b in gold)]
    stratum = f"{doc.meta.get('language', '?')}/{doc.meta.get('doc_type', '?')}"
    return DocScore(doc.doc_id, stratum, dict(caught), dict(fine), missed, clinical, checks, result.blocked,
                    decisions, dict(clinical_sources), extra)


@dataclass
class Summary:
    docs: int = 0
    clean: int = 0
    blocked: int = 0
    clinical_hides: int = 0
    caught: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    caught_fine: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    check_items: list[int] = field(default_factory=list)
    decisions: list[int] = field(default_factory=list)
    clinical_sources: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    extra_hides: int = 0

    def add(self, s: DocScore) -> None:
        self.docs += 1
        self.clean += int(s.clean)
        self.blocked += int(s.blocked)
        self.clinical_hides += len(s.clinical_hides)
        for t, (k, n) in s.caught.items():
            self.caught[t][0] += k
            self.caught[t][1] += n
        for t, (k, n) in s.caught_fine.items():
            self.caught_fine[t][0] += k
            self.caught_fine[t][1] += n
        self.check_items.append(s.check_items)
        for k, v in s.clinical_sources.items():
            self.clinical_sources[k] += v
        self.decisions.append(s.decisions)
        self.extra_hides += len(s.extra_hides)

    def as_dict(self) -> dict:
        median = statistics.median(self.check_items) if self.check_items else 0
        return {
            "docs": self.docs,
            "fully_clean": self.clean,
            "fully_clean_rate": self.clean / self.docs if self.docs else float("nan"),
            "fully_clean_lb95": wilson_lower(self.clean, self.docs),
            "blocked": self.blocked,
            "clinical_hides": self.clinical_hides,
            "clinical_hides_by_source": dict(sorted(self.clinical_sources.items(), key=lambda kv: -kv[1])),
            "extra_hides": self.extra_hides,
            "median_check_items": median,
            "median_decisions": statistics.median(self.decisions) if self.decisions else 0,
            "recall": {t: {"caught": k, "total": n, "rate": k / n if n else float("nan"),
                           "lb95": wilson_lower(k, n)} for t, (k, n) in sorted(self.caught.items())},
            "recall_by_type": {t: {"caught": k, "total": n, "rate": k / n if n else float("nan"),
                                   "lb95": wilson_lower(k, n)}
                               for t, (k, n) in sorted(self.caught_fine.items())},
        }


def summarize(scores: list[DocScore]) -> dict[str, dict]:
    by: dict[str, Summary] = defaultdict(Summary)
    for s in scores:
        by[s.stratum].add(s)
        by["ALL"].add(s)
    return {k: v.as_dict() for k, v in sorted(by.items())}
