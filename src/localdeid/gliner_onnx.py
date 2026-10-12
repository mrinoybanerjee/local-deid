"""GLiNER organization detector (SPEC 4.3 phase 1 experiment) on ONNX Runtime.

knowledgator/gliner-x-base (Apache-2.0; trained on knowledgator's synthetic
multilingual set and UberText-NER-Silver, no AI4Privacy data), int8 ONNX,
pinned in models/MANIFEST.json as `gliner-x-base-q8`. No PyTorch and no
`gliner` package: this module rebuilds the package's span-model inputs
(gliner 0.2.29, UniEncoderSpanProcessor + SpanDecoder, flat NER, greedy
decoding) so that tests/test_gliner_onnx.py can check parity against it.

Memory: the weights sit in an external data file that ONNX Runtime
memory-maps (tools/derive_gliner.py), with weight prepacking and the CPU
arena off, and the tokenizer is SentencePiece on the model's own
spiece.model, which gives the same ids as its 250k-entry tokenizer.json
at a quarter of the memory (tests/test_gliner_onnx.py checks both).

Only local, offline execution: the files are re-hashed before every load,
ONNX Runtime is limited to the CPU provider, and its telemetry is off.

The model reads short chunks (a sentence or line, at most CHUNK_WORDS
whitespace words), the same chunks the dev experiment scored. The model's
config asks for the network-only "stanza" word splitter; this module uses
the package's whitespace splitter instead (the experiment did the same).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .pf.artifacts import verify

ARTIFACT = "gliner-x-base-q8"
LABELS = ("organization", "company", "daycare", "school", "government agency", "hospital or clinic",
          "church", "library")
# Tag type for each label. Merge type priority (rules > model) still lets a rule's type win.
LABEL_KIND = {"organization": "ORG", "company": "ORG", "daycare": "DAYCARE", "school": "SCHOOL",
              "government agency": "AGENCY", "hospital or clinic": "HOSPITAL", "church": "ORG",
              "library": "ORG"}
CHUNK_WORDS = 120  # whitespace words per chunk
MAX_CHUNK_TOKENS = 384  # splitter words per model call (the model was trained with max_len 1024)
# Subword tokens per model call, prompt included. Attention memory grows with the square of
# this; a chunk over it is halved, and one word over it alone ("xq" * 5000) is skipped.
MAX_MODEL_TOKENS = 512
WINDOW_OVERLAP = 24  # words shared by neighbouring windows of an oversized chunk

_WORD = re.compile(r"\w+(?:[-_]\w+)*|\S")  # gliner WhitespaceTokenSplitter
_SEGMENT = re.compile(r"[^\n.!?]+[.!?]?")
_ALLOWED_PROVIDERS = ("CPUExecutionProvider",)
# Token ids of the model's vocabulary (added_tokens.json; mT5 SentencePiece).
ENT_ID, SEP_ID, EOS_ID, UNK_ID = 250100, 250101, 1, 2
_BYTE_PIECE_NUL = "<0x00>"  # SentencePiece's piece for NUL; the HF tokenizer gives <unk>


@dataclass(frozen=True)
class OrgSpan:
    start: int  # code-point offsets into the detected text
    end: int
    label: str  # one of LABELS
    score: float  # sigmoid probability of the span and label


def chunks(text: str) -> list[tuple[int, list[tuple[int, int]]]]:
    """Model inputs: (chunk start, splitter-word offsets relative to it).

    A chunk is a line or sentence piece of at most CHUNK_WORDS whitespace
    words, further cut to at most MAX_CHUNK_TOKENS splitter words."""
    out = []
    for m in _SEGMENT.finditer(text):
        seg, base = m.group(), m.start()
        ws = list(re.finditer(r"\S+", seg))
        for i in range(0, len(ws), CHUNK_WORDS):
            part = ws[i:i + CHUNK_WORDS]
            s, e = part[0].start(), part[-1].end()
            words = [(w.start() + s, w.end() + s) for w in _WORD.finditer(seg[s:e])]
            for j in range(0, len(words), MAX_CHUNK_TOKENS):
                piece = words[j:j + MAX_CHUNK_TOKENS]
                off = piece[0][0]
                out.append((base + off, [(a - off, b - off) for a, b in piece]))
    return out


def span_inputs(num_words: int, max_width: int) -> tuple[np.ndarray, np.ndarray]:
    """gliner prepare_span_idx + prepare_span_labels: every (start, start + w)
    for w < max_width, masked where the end passes the last word."""
    starts = np.repeat(np.arange(num_words, dtype=np.int64), max_width)
    ends = starts + np.tile(np.arange(max_width, dtype=np.int64), num_words)
    return np.stack([starts, ends], axis=1), ends <= num_words - 1


def greedy_flat(cands: list[tuple[float, int, int, int]]) -> list[tuple[float, int, int, int]]:
    """gliner greedy_search (flat NER, single label): highest score first, drop any
    span that overlaps a kept one. Ties keep the (start, width, class) order."""
    kept: list[tuple[float, int, int, int]] = []
    for c in sorted(cands, key=lambda c: -c[0]):  # stable, like the package
        _, s, e, _ = c
        if all(e < ks or ke < s for _, ks, ke, _ in kept):
            kept.append(c)
    return sorted(kept, key=lambda c: c[1])


class GlinerOnnx:
    def __init__(self, models_root: str | Path, *, threads: int | None = None,
                 labels: tuple[str, ...] = LABELS):
        folder = verify(models_root, ARTIFACT)
        cfg = json.loads((folder / "gliner_config.json").read_text(encoding="utf-8"))
        if (cfg.get("span_mode") != "markerV0" or cfg.get("subtoken_pooling", "first") != "first"
                or cfg.get("class_token_index") != ENT_ID):
            raise RuntimeError("unsupported GLiNER configuration")
        self.max_width = int(cfg["max_width"])
        self.labels = tuple(labels)

        os.environ["ORT_DISABLE_TELEMETRY"] = "1"  # before import (see pf/backends.py)
        import onnxruntime as ort
        import sentencepiece as spm

        ort.disable_telemetry_events()
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.add_session_config_entry("session.disable_prepacking", "1")  # keep weights in the mapped file
        opts.enable_cpu_mem_arena = False
        opts.enable_mem_pattern = False
        if threads:
            opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(folder / "model_quantized.onnx"), sess_options=opts,
                                            providers=list(_ALLOWED_PROVIDERS))
        if tuple(self.session.get_providers()) != _ALLOWED_PROVIDERS:
            raise RuntimeError(f"unexpected ONNX Runtime providers: {self.session.get_providers()}")
        self.sp = spm.SentencePieceProcessor(model_file=str(folder / "spiece.model"))
        if (self.sp.eos_id(), self.sp.unk_id()) != (EOS_ID, UNK_ID):
            raise RuntimeError("unexpected GLiNER tokenizer")
        self._nul = self.sp.piece_to_id(_BYTE_PIECE_NUL)
        self.special = {cfg["ent_token"]: ENT_ID, cfg["sep_token"]: SEP_ID}
        self.prompt: list[str] = []
        for lab in self.labels:
            self.prompt += [cfg["ent_token"], lab]
        self.prompt.append(cfg["sep_token"])

    def tokenize(self, words: list[str]) -> tuple[list[int], list[int | None]]:
        """Token ids and word ids for pre-split words, as the HF tokenizer gives
        them (`is_pretokenized=True`, `</s>` appended)."""
        ids: list[int] = []
        wids: list[int | None] = []
        for i, w in enumerate(words):
            if w in self.special:
                pieces = [self.special[w]]
            else:
                pieces = [UNK_ID if t == self._nul else t for t in self.sp.encode(w)]
            ids += pieces
            wids += [i] * len(pieces)
        return ids + [EOS_ID], wids + [None]

    def encode(self, words: list[str]) -> tuple[dict[str, np.ndarray], list[int]]:
        """Model inputs for one chunk (batch of one, so no padding), and the
        indices of the words the model sees. Words that produce no subword
        token (zero-width or control characters) are left out, because the
        package would shift every later word index by one."""
        skip = len(self.prompt)
        ids, wids = self.tokenize(self.prompt + words)
        present = {wid - skip for wid in wids if wid is not None and wid >= skip}
        kept = [i for i in range(len(words)) if i in present]
        if len(kept) < len(words):
            if not kept:
                return {}, []
            ids, wids = self.tokenize(self.prompt + [words[i] for i in kept])
        mask, prev = [], None
        for wid in wids:  # gliner prepare_word_mask, first-subtoken pooling
            first = wid is not None and wid != prev
            mask.append(wid - skip + 1 if first and wid >= skip else 0)
            prev = wid
        n = len(kept)
        if sorted(v for v in mask if v) != list(range(1, n + 1)):
            raise RuntimeError("GLiNER word mask does not cover every word once")
        span_idx, span_mask = span_inputs(n, self.max_width)
        return {
            "input_ids": np.asarray([ids], dtype=np.int64),
            "attention_mask": np.ones((1, len(ids)), dtype=np.int64),
            "words_mask": np.asarray([mask], dtype=np.int64),
            "text_lengths": np.asarray([[n]], dtype=np.int64),
            "span_idx": span_idx[None],
            "span_mask": span_mask[None],
        }, kept

    def predict_words(self, words: list[str], threshold: float) -> list[tuple[int, int, str, float]]:
        """Word-level spans (first word, last word inclusive, label, score)."""
        inputs, kept = self.encode(words)
        if not kept:
            return []
        logits = self.session.run(["logits"], inputs)[0][0]  # [words, width, classes]
        probs = 1.0 / (1.0 + np.exp(-logits.astype(np.float32)))  # float32, like torch.sigmoid
        n = len(kept)
        cands = [(float(probs[s, k, c]), int(s), int(s + k), int(c))
                 for s, k, c in zip(*np.nonzero(probs > threshold), strict=True) if s + k < n]
        return [(kept[s], kept[e], self.labels[c], p) for p, s, e, c in greedy_flat(cands)]

    def bounded(self, words: list[str]) -> list[tuple[int, int]]:
        """Word windows [lo, hi) whose model input stays within MAX_MODEL_TOKENS.
        A chunk that fits is one window (the common case, identical to the
        package). Otherwise windows overlap by WINDOW_OVERLAP words, so a name
        cut by one window boundary is whole in the next. A single word over the
        bound is skipped: no organization name is hundreds of tokens long.
        Words are tokenized independently, so token counts add up."""
        fixed = len(self.tokenize(self.prompt)[0])  # prompt + </s>
        cost = [len(self.tokenize([w])[0]) - 1 for w in words]
        budget = MAX_MODEL_TOKENS - fixed
        if sum(cost) <= budget:
            return [(0, len(words))] if words else []
        out: list[tuple[int, int]] = []
        lo = 0
        while lo < len(words):
            if cost[lo] > budget:
                lo += 1  # an oversized word alone
                continue
            hi, used = lo, 0
            while hi < len(words) and used + cost[hi] <= budget:
                used += cost[hi]
                hi += 1
            out.append((lo, hi))
            if hi >= len(words):
                break
            # An oversized next word is a barrier: no name spans it, so restart after it.
            lo = hi if cost[hi] > budget else max(lo + 1, hi - WINDOW_OVERLAP)
        return out

    def detect(self, text: str, threshold: float = 0.5) -> list[OrgSpan]:
        out = []
        for base, offs in chunks(text):
            chunk_text = text[base:base + offs[-1][1]]
            words = [chunk_text[a:b] for a, b in offs]
            seen: dict[tuple[int, int, str], float] = {}
            for lo, hi in self.bounded(words):
                for s, e, label, score in self.predict_words(words[lo:hi], threshold):
                    key = (base + offs[lo + s][0], base + offs[lo + e][1], label)
                    seen[key] = max(score, seen.get(key, 0.0))  # the same span from two windows
            out += [OrgSpan(a, b, label, score) for (a, b, label), score in sorted(seen.items())]
        return out
