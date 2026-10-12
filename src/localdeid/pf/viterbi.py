"""Constrained BIOES Viterbi decoder for Privacy Filter logits.

A NumPy port of `ViterbiCRFDecoder` from openai/privacy-filter
(opf/_core/decoding.py, commit f7f00ca7, Apache-2.0; see
LICENSES/openai-privacy-filter-APACHE-2.0.txt). The transition rules, the six
transition biases, and the all-impossible fallback to argmax match the
reference. The decoder takes per-token log-probabilities, not raw logits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np

NEG_INF = -1e9

BIAS_KEYS: tuple[str, ...] = (
    "transition_bias_background_stay",
    "transition_bias_background_to_start",
    "transition_bias_inside_to_continue",
    "transition_bias_inside_to_end",
    "transition_bias_end_to_background",
    "transition_bias_end_to_start",
)


@dataclass(frozen=True)
class LabelSpace:
    """BIOES label space: index -> (tag, entity) with 'O' as background."""

    id2label: tuple[str, ...]

    @classmethod
    def from_mapping(cls, id2label: Mapping[int | str, str]) -> "LabelSpace":
        items = {int(k): v for k, v in id2label.items()}
        if sorted(items) != list(range(len(items))):
            raise ValueError("id2label must cover 0..N-1 exactly")
        labels = tuple(items[i] for i in range(len(items)))
        if labels.count("O") != 1:
            raise ValueError("label space needs exactly one 'O' label")
        for lab in labels:
            if lab != "O" and (len(lab) < 3 or lab[0] not in "BIES" or lab[1] != "-"):
                raise ValueError(f"bad BIOES label {lab!r}")
        return cls(labels)

    @property
    def background(self) -> int:
        return self.id2label.index("O")

    def tag(self, idx: int) -> str | None:
        lab = self.id2label[idx]
        return None if lab == "O" else lab[0]

    def entity(self, idx: int) -> str | None:
        lab = self.id2label[idx]
        return None if lab == "O" else lab[2:]

    def __len__(self) -> int:
        return len(self.id2label)


def load_biases(path: str | Path) -> dict[str, float]:
    """Read the default operating point from viterbi_calibration.json (strict)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    biases = payload["operating_points"]["default"]["biases"]
    if set(biases) != set(BIAS_KEYS):
        raise ValueError(f"calibration biases must be exactly {sorted(BIAS_KEYS)}")
    out: dict[str, float] = {}
    for key in BIAS_KEYS:
        value = biases[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        out[key] = float(value)
    return out


class ViterbiDecoder:
    def __init__(self, labels: LabelSpace, biases: Mapping[str, float] | None = None):
        b = {k: 0.0 for k in BIAS_KEYS}
        if biases:
            unknown = set(biases) - set(BIAS_KEYS)
            if unknown:
                raise ValueError(f"unknown bias keys {sorted(unknown)}")
            b.update({k: float(v) for k, v in biases.items()})
        self.labels = labels
        n = len(labels)
        bg = labels.background
        start = np.full(n, NEG_INF, dtype=np.float64)
        end = np.full(n, NEG_INF, dtype=np.float64)
        trans = np.full((n, n), NEG_INF, dtype=np.float64)
        for i in range(n):
            tag, ent = labels.tag(i), labels.entity(i)
            if i == bg or tag in ("B", "S"):
                start[i] = 0.0
            if i == bg or tag in ("E", "S"):
                end[i] = 0.0
            for j in range(n):
                ntag, nent = labels.tag(j), labels.entity(j)
                nbg = j == bg
                if i == bg or tag in ("E", "S"):
                    valid = nbg or ntag in ("B", "S")
                else:  # B or I
                    valid = (not nbg) and ent == nent and ntag in ("I", "E")
                if not valid:
                    continue
                if i == bg:
                    bias = b["transition_bias_background_stay"] if nbg else (
                        b["transition_bias_background_to_start"]
                    )
                elif tag in ("B", "I"):
                    bias = b["transition_bias_inside_to_continue"] if ntag == "I" else (
                        b["transition_bias_inside_to_end"]
                    )
                else:  # E or S
                    bias = b["transition_bias_end_to_background"] if nbg else (
                        b["transition_bias_end_to_start"]
                    )
                trans[i, j] = bias
        self.start, self.end, self.trans = start, end, trans

    def decode(self, logprobs: np.ndarray) -> list[int]:
        """Decode a [T, C] log-probability array into T label ids."""
        if logprobs.ndim != 2 or logprobs.shape[1] != len(self.labels):
            raise ValueError("logprobs must have shape [T, num_labels]")
        lp = logprobs.astype(np.float64, copy=False)
        t_len = lp.shape[0]
        if t_len == 0:
            return []
        if not np.isfinite(lp).all():
            raise ValueError("logprobs contain NaN or inf")
        scores = lp[0] + self.start
        back = np.empty((t_len - 1, lp.shape[1]), dtype=np.int64)
        for t in range(1, t_len):
            cand = scores[:, None] + self.trans
            back[t - 1] = cand.argmax(axis=0)
            scores = cand.max(axis=0) + lp[t]
        scores = scores + self.end
        if not np.isfinite(scores).any() or scores.max() <= NEG_INF / 2:
            return lp.argmax(axis=1).tolist()
        path = np.empty(t_len, dtype=np.int64)
        path[-1] = int(scores.argmax())
        for t in range(t_len - 2, -1, -1):
            path[t] = back[t, path[t + 1]]
        return path.tolist()


def log_softmax(logits: np.ndarray) -> np.ndarray:
    x = logits.astype(np.float64, copy=False)
    m = x.max(axis=-1, keepdims=True)
    return x - m - np.log(np.exp(x - m).sum(axis=-1, keepdims=True))
