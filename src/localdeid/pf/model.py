"""Privacy Filter detector: text -> typed character spans (code-point offsets)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .backends import Backend
from .tokenize import PFTokenizer
from .viterbi import LabelSpace, ViterbiDecoder, load_biases, log_softmax
from .windows import windowed_logits

# Window and overlap per backend (reports/harness/day1.md). ONNX attention is
# dense (T x T), so its windows must stay small; MLX attention is banded.
DEFAULT_WINDOWS = {"mlx_8bit": (8192, 1024), "mlx_8bit_ft1": (8192, 1024), "onnx_q4": (2048, 512),
                   "onnx_fp32_ref": (3072, 1024)}
SAFE_WINDOW = (2048, 512)  # for any backend not listed


@dataclass(frozen=True)
class PFSpan:
    start: int
    end: int
    label: str  # Privacy Filter entity, e.g. "private_person"
    score: float  # lowest per-token probability of the decoded label in the span


def labels_to_spans(
    text: str, path: list[int], probs: np.ndarray, starts: np.ndarray, ends: np.ndarray,
    labels: LabelSpace,
) -> list[PFSpan]:
    """Turn a decoded BIOES path into character spans, trimmed of edge whitespace."""
    spans: list[PFSpan] = []
    i, n = 0, len(path)
    while i < n:
        tag, ent = labels.tag(path[i]), labels.entity(path[i])
        if tag is None:
            i += 1
            continue
        j = i
        if tag == "B":
            while j + 1 < n and labels.tag(path[j + 1]) in ("I", "E") and (
                labels.entity(path[j + 1]) == ent
            ):
                j += 1
                if labels.tag(path[j]) == "E":
                    break
        # Tags I/E without a B cannot come out of the constrained decoder, but
        # an argmax fallback could produce them; treat them as one-token spans.
        start, end = int(starts[i : j + 1].min()), int(ends[i : j + 1].max())
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            score = float(min(probs[k, path[k]] for k in range(i, j + 1)))
            spans.append(PFSpan(start, end, ent or "", score))
        i = j + 1
    return spans


def _pinned_biases(model_dir: Path) -> dict[str, float] | None:
    """Viterbi biases from viterbi_calibration.json, only if the manifest pins it
    for this artifact (load_backend verified the hash). An unpinned file refuses."""
    import json

    calib = model_dir / "viterbi_calibration.json"
    manifest = model_dir.parent / "MANIFEST.json"
    pinned = False
    if manifest.exists():
        files = json.loads(manifest.read_text(encoding="utf-8")).get("artifacts", {}).get(
            model_dir.name, {}).get("files", {})
        pinned = any(Path(rel).name == calib.name for rel in files)
    if calib.exists() and not pinned:
        raise RuntimeError(f"{calib} is not pinned in MANIFEST.json")
    return load_biases(calib) if calib.exists() else None


class PrivacyFilter:
    def __init__(
        self, backend: Backend, model_dir: str | Path, *, window: int | None = None,
        overlap: int | None = None, bpe_file: str | Path | None = None,
    ):
        model_dir = Path(model_dir)
        self.backend = backend
        # The o200k BPE file sits next to the model directories (models/o200k-bpe/).
        bpe = Path(bpe_file) if bpe_file else model_dir.parent / "o200k-bpe" / "o200k_base.tiktoken"
        self.tokenizer = PFTokenizer(bpe)
        self.labels = LabelSpace.from_mapping(backend.id2label)
        self.decoder = ViterbiDecoder(self.labels, _pinned_biases(model_dir))
        dw, do = DEFAULT_WINDOWS.get(getattr(backend, "name", ""), SAFE_WINDOW)
        self.window = dw if window is None else window
        self.overlap = do if overlap is None else overlap

    def token_logits(self, text: str) -> tuple[np.ndarray, "object"]:
        enc = self.tokenizer.encode(text)
        if len(enc.ids) == 0:
            return np.zeros((0, len(self.labels)), dtype=np.float32), enc
        return windowed_logits(enc.ids, self.backend.logits, self.window, self.overlap), enc

    def detect(self, text: str) -> list[PFSpan]:
        logits, enc = self.token_logits(text)
        if logits.shape[0] == 0:
            return []
        lp = log_softmax(logits)
        path = self.decoder.decode(lp)
        return labels_to_spans(text, path, np.exp(lp), enc.starts, enc.ends, self.labels)
