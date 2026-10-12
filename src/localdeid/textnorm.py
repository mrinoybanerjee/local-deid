"""One coordinate system (SPEC 4.2).

The pasted string is normalized to NFC once; every span is a code-point
offset into that string. Detectors that need a transformed copy (case and
accent folding) use `fold`, which returns the copy plus a monotonic map back
to source offsets.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


class InvalidText(ValueError):
    """The pasted text holds characters that are not valid Unicode (lone surrogates)."""


def normalize_paste(raw: str) -> tuple[str, bool]:
    """NFC-normalize pasted text. Returns (text, changed). The only text change.

    Text with lone surrogates is refused rather than repaired, because a
    repair would be a second, silent text change.
    """
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidText(f"invalid character at offset {exc.start}") from exc
    text = unicodedata.normalize("NFC", raw)
    return text, text != raw


def fold_char(ch: str) -> str:
    """Case- and accent-fold one character: 'É' -> 'e', 'ß' -> 'ss', 'ñ' -> 'n'."""
    decomposed = unicodedata.normalize("NFD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return base.casefold()


@dataclass(frozen=True)
class Folded:
    text: str  # folded copy
    src: tuple[int, ...]  # src[i] = source offset of folded char i; len == len(text) + 1

    def to_source(self, start: int, end: int) -> tuple[int, int]:
        """Map a folded half-open span to the smallest covering source span."""
        if not 0 <= start <= end <= len(self.text):
            raise ValueError("span outside folded text")
        if start == end:
            return self.src[start], self.src[start]
        # The last folded char came from source char src[end - 1].
        return self.src[start], self.src[end - 1] + 1


def fold(text: str) -> Folded:
    """Fold `text`; every folded char maps to the source char it came from.

    A source char that folds to nothing (a lone combining mark) maps to no
    folded char; a char that folds to several ('ß' -> 'ss') maps all of them
    back to itself. The map is non-decreasing, so spans stay ordered.
    """
    out: list[str] = []
    src: list[int] = []
    for i, ch in enumerate(text):
        f = fold_char(ch)
        out.append(f)
        src.extend([i] * len(f))
    src.append(len(text))
    return Folded("".join(out), tuple(src))
