"""Synthetic evaluation data: inline markup -> plain text + exact labels (SPEC 7).

Generators (Claude for dev data, GPT-6.1 Sol for the gate set) write every
planted identifier inline, and every clinical token that must survive:

    [[CHILD|Mateo]] produced [[KEEP|/k/]] in [[KEEP|7/10 trials]] on [[DATE|March 3]], 2026.

`parse` strips the markup and returns the text with code-point spans, so
labels are exact by construction. Markup may not nest. A literal "[[" in a
document is written "[[[[".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .protect import CPT_CODES

# Gold identifier types. Roles map onto the coarse types used for recall.
GOLD_TYPES = {
    "CHILD": "NAME", "PEER": "NAME", "PARENT": "NAME", "SIBLING": "NAME", "FAMILY": "NAME",
    "TEACHER": "NAME", "PROVIDER": "NAME", "PERSON": "NAME", "SURNAME": "NAME", "PET": "NAME",
    "EMPLOYER": "ORG", "ORG": "ORG", "SCHOOL": "ORG", "DAYCARE": "ORG", "HOSPITAL": "ORG",
    "AGENCY": "ORG", "PLACE": "PLACE", "ADDRESS": "PLACE", "DATE": "DATE", "TIME": "TIME",
    "PHONE": "CONTACT", "EMAIL": "CONTACT", "URL": "CONTACT", "ID": "ID", "MEDICAID_ID": "ID",
}
KEEP = "KEEP"
# The surface may hold single brackets ("[w]", "[EW:goed]"); it ends at the
# last "]]" of a closing run, and may not hold "[[", "|", or a newline.
_MARK = re.compile(r"\[\[(?P<type>[A-Z_]+)\|(?P<surface>(?:(?!\[\[)[^|\n])+?)\]\](?!\])")


@dataclass
class GoldSpan:
    start: int
    end: int
    type: str  # fine type (CHILD, DATE, ...) or KEEP

    @property
    def coarse(self) -> str:
        return GOLD_TYPES.get(self.type, self.type)


@dataclass
class Doc:
    doc_id: str
    text: str
    spans: list[GoldSpan] = field(default_factory=list)
    meta: dict = field(default_factory=dict)  # language, doc_type, style, family, template, generator

    @property
    def identifiers(self) -> list[GoldSpan]:
        return [s for s in self.spans if s.type != KEEP]

    @property
    def keeps(self) -> list[GoldSpan]:
        return [s for s in self.spans if s.type == KEEP]


class MarkupError(ValueError):
    pass


def parse(marked: str) -> tuple[str, list[GoldSpan]]:
    """Strip [[TYPE|surface]] markup; return text and spans. Strict: bad markup raises."""
    out: list[str] = []
    spans: list[GoldSpan] = []
    pos = 0
    length = 0
    src = marked.replace("[[[[", "\x00")  # escaped literal "[["
    for m in _MARK.finditer(src):
        t = m.group("type")
        if t != KEEP and t not in GOLD_TYPES:
            raise MarkupError(f"unknown type {t!r} at {m.start()}")
        chunk = src[pos:m.start()]
        if "[[" in chunk or "]]" in chunk:
            raise MarkupError(f"malformed markup near {pos}: {chunk[-40:]!r}")
        chunk = chunk.replace("\x00", "[[")  # escaped literal: two characters in the output
        out.append(chunk)
        length += len(chunk)
        surface = m.group("surface")
        if surface != surface.strip():
            raise MarkupError(f"surface with edge spaces: {surface!r}")
        spans.append(GoldSpan(length, length + len(surface), t))
        out.append(surface)
        length += len(surface)
        pos = m.end()
    tail = src[pos:]
    if "[[" in tail or "]]" in tail:
        raise MarkupError(f"malformed markup in tail: {tail[:40]!r}")
    out.append(tail.replace("\x00", "[["))
    text = "".join(out)
    return text, spans


def load_jsonl(path: str | Path) -> list[Doc]:
    docs = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        docs.append(Doc(rec["doc_id"], rec["text"], [GoldSpan(**s) for s in rec["spans"]], rec.get("meta", {})))
    return docs


def dump_jsonl(docs: list[Doc], path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps({"doc_id": d.doc_id, "text": d.text,
                                "spans": [s.__dict__ for s in d.spans], "meta": d.meta},
                               ensure_ascii=False) + "\n")


# Generators sometimes label clinical codes as identifiers. CPT and ICD-10
# codes are clinical content (SPEC 4.3 protect), so such gold spans become KEEP.
_ICD_DOTTED = re.compile(r"[A-TV-Z]\d[0-9AB]\.[0-9A-Z]{1,4}")
_CODE_CONTEXT = re.compile(r"(?i)\b(?:cpt|icd(?:-10)?(?:-cm)?|code|codes|c[oó]digo|c[oó]digos|dx|diagnosis)\b[^\n]{0,15}$")


_MONTH_WORD = re.compile(r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ene|abr|ago|dic)[a-záéíóú]*\b")
_DURATION = re.compile(r"(?i)^\d+(?:\.\d+)?\s*(?:weeks?|months?|years?|days?|semanas?|meses|mes|años?|días?)"
                       r"(?:\s+(?:old|of age|de edad|gestation|gestational age|de gestación))?$")


_STATE_NAME = re.compile(r"(?i)\b(?:north|south)\s+$")
_STATE_TAIL = re.compile(r"(?i)\s+del\s+(?:norte|sur)\b")


def code_digest() -> str:
    """SHA-256 over the engine and scorer code and data (src/localdeid without the app)
    and the evaluation runner: a cached evaluation is valid only for the code that made it."""
    import hashlib

    pkg = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(pkg.rglob("*")):
        rel = p.relative_to(pkg)
        if p.is_file() and rel.parts[0] != "app" and "__pycache__" not in rel.parts and p.suffix in (".py", ".txt",
                                                                                                    ".json", ".tsv"):
            h.update(str(rel).encode() + b"\0" + p.read_bytes() + b"\0")
    runner = pkg.parents[1] / "tools" / "run_eval.py"  # the runner sets the caseload and employer names
    if runner.is_file():
        h.update(b"tools/run_eval.py\0" + runner.read_bytes() + b"\0")
    return h.hexdigest()


def docs_digest(docs: list[Doc]) -> str:
    """SHA-256 over everything an evaluation reads: ids, text, gold spans, and meta."""
    import hashlib

    h = hashlib.sha256()
    for d in sorted(docs, key=lambda d: d.doc_id):
        h.update(json.dumps({"id": d.doc_id, "text": d.text, "spans": [[s.start, s.end, s.type] for s in d.spans],
                             "meta": d.meta}, sort_keys=True, ensure_ascii=False).encode())
        h.update(b"\n")
    return h.hexdigest()


def sanitize(doc: Doc) -> tuple[Doc, int]:
    """Fix generator label noise so scoring follows the spec. Returns (doc, changes).

    - clinical codes labelled ID (with code context) -> KEEP;
    - test names labelled as people ("Rossetti", "Bayley") -> KEEP;
    - durations labelled DATE ("39 weeks": gestational age stays, SPEC 6) -> dropped;
    - spans made only of kin words, pronouns, or articles ("la niña", "yo") -> dropped;
    - KEEP spans holding a word that the same document labels as a person or pet
      name, exact case ('"Canela run fast"' with pet Canela) -> dropped: a gold
      conflict, the name is an identifier (DECISIONS 25);
    - unlabelled exact repeats of a labelled identifier -> labelled (DECISIONS 26).
    """
    from .generic import all_generic
    from .protect import protect

    from . import fields as _fields
    from .pipeline import _drop_protect_in_fields

    prot = _drop_protect_in_fields(doc.text, protect(doc.text), _fields.find(doc.text))
    terms = [(p.start, p.end) for p in prot if p.reason == "term"]
    clinical = [(p.start, p.end) for p in prot if p.reason in ("age", "score", "code")]
    from .rules import _COUNTRY_SET
    fixed = 0
    spans = []
    for s in doc.spans:
        value = doc.text[s.start:s.end]
        relative = s.type == "DATE" and not re.search(r"\d", value) and not _MONTH_WORD.search(value)
        if s.type != KEEP and (all_generic(value) or relative or (s.type == "DATE" and _DURATION.match(value.strip()))
                               or (s.type == "PLACE" and value.strip().lower() in _COUNTRY_SET)):
            fixed += 1
            continue  # not an identifier under SPEC 6 (relative time, states and countries)
        if s.type != KEEP and any(a <= s.start and s.end <= b for a, b in clinical):
            spans.append(GoldSpan(s.start, s.end, KEEP))  # an age or score labelled as a name
            fixed += 1
            continue
        if s.type in GOLD_TYPES and GOLD_TYPES[s.type] == "NAME" and any(
                a <= s.start and s.end <= b for a, b in terms):
            spans.append(GoldSpan(s.start, s.end, KEEP))
            fixed += 1
            continue
        value = doc.text[s.start:s.end]
        before = doc.text[max(0, s.start - 30):s.start]
        is_code = _ICD_DOTTED.fullmatch(value) or (
            (value in CPT_CODES or re.fullmatch(r"[A-TV-Z]\d[0-9AB]", value)) and _CODE_CONTEXT.search(before))
        if s.type in ("ID", "MEDICAID_ID") and is_code:
            spans.append(GoldSpan(s.start, s.end, KEEP))
            fixed += 1
        else:
            spans.append(s)
    names = {v for s in spans if s.type != KEEP and GOLD_TYPES.get(s.type) == "NAME"
             and len(v := doc.text[s.start:s.end].strip()) >= 3}
    name_re = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                         + r")(?!\w)") if names else None
    kept = []
    for s in spans:
        if s.type == KEEP and name_re is not None and name_re.search(doc.text[s.start:s.end]):
            # keep the clinical words around the name: '"Canela run fast"' -> KEEP "run fast"
            fixed += 1
            pos = s.start
            cuts = [(m.start() + s.start, m.end() + s.start) for m in name_re.finditer(doc.text[s.start:s.end])]
            for a, b in cuts + [(s.end, s.end)]:
                piece = doc.text[pos:a]
                lead = len(piece) - len(piece.lstrip(" \t\"“”'‘’,.;:!?()[]"))
                trail = len(piece) - len(piece.rstrip(" \t\"“”'‘’,.;:!?()[]"))
                ps, pe = pos + lead, a - trail
                if pe > ps and any(ch.isalnum() for ch in doc.text[ps:pe]):
                    kept.append(GoldSpan(ps, pe, KEEP))
                pos = b
            continue
        kept.append(s)
    # Repeats the generator left unlabelled ("Ingles Market" labelled once, its other
    # mentions not): every exact, whole-word repeat of a labelled identifier surface
    # in the same document gets the same label (DECISIONS 26 addendum). Surfaces of
    # fewer than 4 characters, without a capital or digit, or made of common words
    # are left alone. An identifier labelled nowhere cannot be found this way.
    taken = [(s.start, s.end) for s in kept]
    protected = [(p.start, p.end) for p in prot]  # clinical tokens never get a repeat label
    from .roster import word_names
    from .textnorm import fold as _fold

    wordish = word_names()  # "Mark", "Will", "Hope", "Rosa": a repeat may be the word, so no repair
    surfaces: dict[str, str] = {}
    for s in kept:
        v = doc.text[s.start:s.end]
        if s.type == KEEP or len(v.strip()) < 4 or not any(c.isupper() or c.isdigit() for c in v) or all_generic(v):
            continue
        if " " not in v.strip() and _fold(v.strip()).text in wordish:
            continue
        surfaces.setdefault(v, s.type)
    added = []
    for v, ty in sorted(surfaces.items(), key=lambda x: -len(x[0])):
        for m in re.finditer(r"(?<!\w)" + re.escape(v) + r"(?!\w)", doc.text):
            a, b = m.start(), m.end()
            if any(x < b and a < y for x, y in taken):
                continue
            if _STATE_NAME.search(doc.text[max(0, a - 6):a]) or _STATE_TAIL.match(doc.text, b):
                continue  # "Carolina" in "North Carolina" / "Carolina del Norte" is the state
            if any(x <= a and b <= y for x, y in protected):
                continue  # the repeat is itself a clinical term ("IFSP"), even if one mention was labelled
            taken.append((a, b))
            added.append(GoldSpan(a, b, ty))
            fixed += 1
    kept = sorted(kept + added, key=lambda s: (s.start, s.end))
    return Doc(doc.doc_id, doc.text, kept, doc.meta), fixed
