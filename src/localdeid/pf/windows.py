"""Split a long token sequence into overlapping windows and stitch the results.

Each window is at most `window` tokens. Neighbouring windows share `overlap`
tokens of context on each side of the cut, and each token's logits come from
the window where it sits farthest from an edge (its "core"). The cores tile
the sequence exactly once, in order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class Window:
    start: int  # first token fed to the model
    end: int  # one past the last token fed
    keep_start: int  # first token whose logits we keep (absolute index)
    keep_end: int  # one past the last kept token


def plan_windows(n_tokens: int, window: int, overlap: int) -> list[Window]:
    if window <= 0 or overlap < 0:
        raise ValueError("window must be > 0 and overlap >= 0")
    if 2 * overlap >= window:
        raise ValueError("window must be larger than twice the overlap")
    if n_tokens <= 0:
        return []
    if n_tokens <= window:
        return [Window(0, n_tokens, 0, n_tokens)]
    out: list[Window] = []
    keep = 0
    while True:
        start = max(0, keep - overlap)
        end = start + window
        if end >= n_tokens:
            # Last window: full width, ending at the last token.
            out.append(Window(n_tokens - window, n_tokens, keep, n_tokens))
            return out
        keep_end = end - overlap  # leave `overlap` tokens of right context
        out.append(Window(start, end, keep, keep_end))
        keep = keep_end


def windowed_logits(
    ids: np.ndarray, forward: Callable[[np.ndarray], np.ndarray], window: int, overlap: int
) -> np.ndarray:
    """Run `forward` ([t] int64 -> [t, C] float) over windows; return [T, C]."""
    plan = plan_windows(len(ids), window, overlap)
    if not plan:
        return np.zeros((0, 0), dtype=np.float32)
    parts: list[np.ndarray] = []
    for w in plan:
        logits = forward(ids[w.start : w.end])
        if logits.shape[0] != w.end - w.start:
            raise ValueError("forward returned the wrong number of rows")
        parts.append(logits[w.keep_start - w.start : w.keep_end - w.start])
    out = np.concatenate(parts, axis=0)
    if out.shape[0] != len(ids):
        raise AssertionError("window stitching lost or duplicated tokens")
    return out
