"""Copy block and restore (SPEC 4.4, 4.5).

Copy: a ref line, a context line, then the tagged text. Restore: find the
session by ref (or by fingerprint, with confirmation), parse tags with the
tolerant grammar in tags.scan, block on any residue, warn on dropped tags and
new numbers, and put the real values back. An unedited copy restores
byte-for-byte.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .pipeline import Result
from .tags import scan

REF_LINE = "ref: {ref} — keep this line and all [TAGS] exactly as written."
# A line holding only our ref (the AI may drop or keep the explanation after it).
_REF_RE = re.compile(r"(?im)^[ \t]*ref[ \t]*[:：][ \t]*([A-Z0-9]{4})(?:[ \t]*[—–-][^\n]*)?[ \t]*(?:\n|$)")
_AGE_RE = re.compile(r"(?<![\w;])(\d{1,2});(\d{1,2})(?![\w;])")


# --------------------------------------------------------------------------- copy


def language_of(text: str) -> str:
    """'English', 'Spanish', or 'Spanish/English' from common function words."""
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    es = sum(w in _ES_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    if es and en and min(es, en) >= 0.15 * max(es, en):
        return "Spanish/English"
    return "Spanish" if es > en else "English"


_ES_WORDS = frozenset("el la los las de del que y en un una con por para es su se al lo como más pero".split())
_EN_WORDS = frozenset("the and of to in a is with for on was that he she his her at by from as".split())


def context_line(result: Result, gender: str | None = None) -> str:
    """'[CHILD_1]: girl, 4;2. Language: Spanish/English.' (SPEC 4.4).

    Gender: explicitly supplied by the caller; never inferred from a name.
    Age: the first unhidden y;m age under 90.
    """
    child = next((it for it in result.items if it.hidden and it.kind == "CHILD"), None)
    lang = language_of(result.source)
    if child is None:
        return f"Language: {lang}."
    age = None
    hidden = [(it.start, it.end) for it in result.items if it.hidden]
    for m in _AGE_RE.finditer(result.source):
        if int(m.group(1)) < 90 and not any(a < m.end() and m.start() < b for a, b in hidden):
            age = m.group(0)  # never an over-89 or hidden age (SPEC 4.4, 6)
            break
    details = ", ".join(x for x in (gender, age) if x)
    head = f"[{child.tag}]: {details}." if details else f"[{child.tag}]."
    surname = any(it.hidden and it.kind == "SURNAME" for it in result.items)
    tail = " Tags named SURNAME are family surnames." if surname else ""
    return f"{head} Language: {lang}.{tail}"


def copy_block(result: Result, gender: str | None = None, template: str | None = None) -> str:
    """The text that goes to the clipboard. `template` wraps it ({note} placeholder).
    The context line actually copied is recorded, so restore removes exactly it."""
    ctx = context_line(result, gender)
    if ctx not in result.copied_contexts:
        result.copied_contexts.append(ctx)
    body = "\n".join([REF_LINE.format(ref=result.ref), ctx, result.output])
    if template:
        if "{note}" not in template:
            raise ValueError("template must contain {note}")
        return template.replace("{note}", body)
    return body


# --------------------------------------------------------------------------- restore


@dataclass
class Issue:
    code: str  # block: no-session, wrong-ref, unknown-tag, unknown-escape, fragment
    text: str  # warn: missing-tag, new-number
    start: int = -1
    end: int = -1
    blocking: bool = True


@dataclass
class Restored:
    text: str  # restored draft (empty when blocked)
    issues: list[Issue] = field(default_factory=list)
    spans: list[tuple[int, int, str]] = field(default_factory=list)  # restored values in `text`

    @property
    def blocked(self) -> bool:
        return any(i.blocking for i in self.issues)


def find_ref(draft: str) -> str | None:
    m = _REF_RE.search(draft)
    return m.group(1).upper() if m else None


def find_refs(draft: str) -> list[str]:
    return [m.group(1).upper() for m in _REF_RE.finditer(draft)]


def strip_ref_and_context(draft: str, result: Result) -> str:
    """Remove our ref line, and the copied context line only when it directly
    follows the ref line. Text that merely looks like metadata is kept."""
    m = _REF_RE.search(draft)
    if not m:
        return draft
    head, rest = draft[:m.start()], draft[m.end():]
    for ctx in sorted(result.copied_contexts, key=len, reverse=True):
        if rest.startswith(ctx + "\n") or rest == ctx:
            rest = rest[len(ctx) + 1:]
            break
    # Repeated copies of the same ref line (the AI echoed it) are ours too.
    rest = _REF_RE.sub(lambda r: "" if r.group(1).upper() == result.ref else r.group(0), rest)
    return head + rest


_NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3,
              "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}


def _numbers(text: str) -> tuple[set[str], set[str]]:
    """(scores, plain numbers). Scores are canonical percentages: '8/10', '8 out of
    10', 'ocho de diez', and '80%' are all 'p80'. Plain numbers exclude the parts
    of scores, so '80%' never yields a bare '80'."""
    t = unicodedata.normalize("NFKC", text.lower())
    t = re.sub(r"\b(" + "|".join(_NUM_WORDS) + r")\b", lambda m: str(_NUM_WORDS[m.group(1)]), t)
    scores: set[str] = set()
    parts: set[str] = set()

    def ratio(m: re.Match[str]) -> str:
        a, b = int(m.group(1)), int(m.group(2))
        if b:
            scores.add(f"r{100 * a / b:.4f}")  # ratio-derived: matches a percent within half a point
        parts.update({str(a), str(b)})
        return " "

    t = re.sub(r"(\d+)\s*(?:/|out of|of|de)\s*(\d+)", ratio, t)

    def pct(m: re.Match[str]) -> str:
        scores.add(f"p{float(m.group(1)):.4f}")  # exact percent, precision kept ("80.4%" is not "80%")
        return " "

    t = re.sub(r"(\d+(?:\.\d+)?)\s*%", pct, t)
    plain = {m.group() for m in re.finditer(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", t)}
    return scores, plain, parts


def _norm_numbers(text: str) -> set[str]:
    """All number forms of `text` (for fingerprints)."""
    scores, plain, parts = _numbers(text)
    return scores | plain | parts


def _new_numbers(draft: str, source: str) -> list[str]:
    """Numbers or scores in the draft that the source does not have, in any form.
    A draft ratio is judged by its score ('8/10' = '80%'), not by its parts."""
    d_scores, d_plain, _ = _numbers(draft)
    s_scores, s_plain, s_parts = _numbers(source)

    def same(x: str, y: str) -> bool:
        vx, vy = float(x[1:]), float(y[1:])
        if x[0] == "p" and y[0] == "p":
            return vx == vy
        return abs(vx - vy) < 0.5  # "2/3" = "67%", "8/10" = "80%"

    new = [_score_label(x) for x in sorted(d_scores) if not any(same(x, y) for y in s_scores)]
    s_values = s_plain | s_parts | {_score_label(x)[:-1] for x in s_scores}  # "8" matches a source "8/10"
    new += sorted(d_plain - s_values)
    return new


def _score_label(x: str) -> str:
    v = float(x[1:])
    return (f"{v:.0f}" if v == int(v) else f"{v:.1f}") + "%"


def restore(draft: str, result: Result, *, ref_confirmed: bool = False) -> Restored:
    """Restore a draft to the session in `result`.

    The draft must carry the session's ref, or the caller must pass
    ref_confirmed=True after the clinician confirmed a fingerprint match.
    """
    refs = find_refs(draft)
    if not refs and not ref_confirmed:
        return Restored("", [Issue("no-session", "The ref line is missing; confirm the session first.")])
    wrong = sorted({r for r in refs if r != result.ref})
    if wrong:
        # Any foreign ref blocks: a paste holding two sessions' drafts must not restore.
        return Restored("", [Issue("wrong-ref", f"This draft holds session {', '.join(wrong)}, not {result.ref}.")])
    body = strip_ref_and_context(draft, result)

    known = {inst.tag for inst in result.instances if inst.tag}
    escapes = {inst.esc: inst.value for inst in result.instances if not inst.tag}
    found = scan(body)
    issues: list[Issue] = []
    for t in found:
        raw = body[t.start:t.end]
        if t.kind == "tag" and (t.tag is None or t.tag not in known):
            issues.append(Issue("unknown-tag", raw, t.start, t.end))
        elif t.kind == "escape" and t.escape not in escapes:
            issues.append(Issue("unknown-escape", raw, t.start, t.end))
        elif t.kind == "residue":
            issues.append(Issue("fragment", raw, t.start, t.end))
    if issues:
        return Restored("", issues)

    tags = [t for t in found if t.kind == "tag"]
    # Byte-exact path: the draft's tag sequence equals the render's sequence.
    rendered_seq = [inst.tag for inst in result.instances if inst.tag]
    draft_seq = [t.tag for t in tags]
    per_instance = draft_seq == rendered_seq
    values = iter([inst.value for inst in result.instances if inst.tag])
    out: list[str] = []
    spans: list[tuple[int, int, str]] = []
    pos = 0
    length = 0
    for t in found:
        out.append(body[pos:t.start])
        length += t.start - pos
        if t.kind == "escape":
            value = escapes[t.escape]
        else:
            value = next(values) if per_instance else result.canonical[t.tag]
            spans.append((length, length + len(value), t.tag))
        out.append(value)
        length += len(value)
        pos = t.end
    out.append(body[pos:])
    text = "".join(out)

    # Warnings (not blocking).
    for tag in sorted(known - set(draft_seq)):
        issues.append(Issue("missing-tag", f"[{tag}] is not in the draft", blocking=False))
    masked = list(body)
    for t in found:
        masked[t.start:t.end] = " " * (t.end - t.start)
    for n in _new_numbers("".join(masked), result.source):
        issues.append(Issue("new-number", n, blocking=False))
    return Restored(text, issues, spans)


# --------------------------------------------------------------------------- fingerprint


def fingerprint_scores(draft: str, sessions: Sequence[Result]) -> list[tuple[float, Result]]:
    """Rank sessions for a draft with no ref line: numbers, rare words, tags (SPEC 4.5)."""
    d_nums = _norm_numbers(draft)
    d_words = set(re.findall(r"[^\W\d_]{6,}", draft.lower()))
    d_tags = {t.tag for t in scan(draft) if t.kind == "tag" and t.tag}
    scored = []
    for r in sessions:
        nums = _norm_numbers(r.output)
        words = set(re.findall(r"[^\W\d_]{6,}", r.output.lower()))
        tags = {inst.tag for inst in r.instances if inst.tag}
        score = (_jaccard(d_nums, nums) * 2 + _jaccard(d_words, words) + _jaccard(d_tags, tags)) / 4
        scored.append((score, r))
    return sorted(scored, key=lambda x: -x[0])


def best_session(draft: str, sessions: Sequence[Result], margin: float = 0.1) -> Result | None:
    """The single best match, or None when two sessions score close (ask the clinician)."""
    ranked = fingerprint_scores(draft, sessions)
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < margin:
        return None
    return ranked[0][1] if ranked[0][0] > 0 else None


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0
